"""User-scoped, committed JSONL chat storage. No database or feature imports.

Public contract: ChatStore(settings), open_conversation(principal,cid) -> scope;
lease(scope,run=False) -> acquired, reusable ConversationLease. Mutation helpers
accept that same lease. Public dictionaries contain no internal request digest.
"""

from contextlib import contextmanager
from collections import OrderedDict
from dataclasses import dataclass, field
import base64
import copy
import hashlib
import json
import os
import re
import stat
import threading
from functools import wraps
from uuid import uuid4

from . import config, media, file_deletions
from .errors import ChatError
from .fs import SafeFS, canonical_json, storage_error, utcnow
from .locks import ConversationLease, FileLock, profile_lock
from .recovery import (
    LogState,
    STAMP,
    TERMINAL,
    project,
    Projection,
    request_digest,
    scan,
    validate_event,
)
from .schemas import identifier, integer, object_fields, title as clean_title, id_list


def io_guard(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OSError as exc:
            raise storage_error(exc) from None

    return guarded


def not_found():
    return ChatError("聊天记录不存在。", 404, "CHAT_NOT_FOUND")


@dataclass(frozen=True)
class ConversationScope:
    principal: object
    cid: str
    instance_id: str
    _store: object = field(repr=False, compare=False)

    @property
    def user_id(self):
        return self.principal.user_id

    @property
    def path(self):
        return f"{self.instance_id}/users/{self.user_id}/conversations/{self.cid}"


class Reservation:
    """Durable remaining budget; close after physical writes, not before."""

    def __init__(self, store, record):
        self.store, self.record, self.closed = store, record, False
        self.path = f"{store.prefix}/reservations/{record['operation_id']}.json"

    @property
    def remaining(self):
        return self.record["bytes"]

    def _consume_locked(self, amount):
        if amount > self.remaining:
            raise ChatError("存储预留已用尽。", 507, "CHAT_STORAGE_LIMIT")
        self.record["bytes"] -= max(0, amount)
        self.store.fs.write_json(self.path, self.record)

    def close(self):
        if not self.closed:
            with self.store.storage_lock():
                self.store.fs.unlink(self.path, missing_ok=True)
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class ChatStore:
    _TEXT_EVENTS = frozenset(
        {"assistant.delta", "assistant.reasoning", "assistant.protocol"}
    )

    def __init__(self, settings):
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", settings.instance_id)
            or not 1 <= settings.max_concurrent_runs <= 8
        ):
            raise ChatError("聊天配置无效。", 503, "CHAT_CONFIG_INVALID")
        self.settings, self.prefix = settings, settings.instance_id
        self.fs = SafeFS(settings.data_dir)
        # Validated journals only, never credentials or authorization results.
        # A hit still opens and verifies the source inode and reads commit.json.
        self._read_cache = OrderedDict()
        self._read_cache_lock = threading.Lock()
        try:
            for suffix in (
                "",
                "/users",
                "/reservations",
                "/.locks/users",
                "/.locks/conversations",
                "/.locks/slots",
            ):
                self.fs.mkdir(self.prefix + suffix, parents=True)
            with FileLock(
                self.fs,
                f"{self.prefix}/.locks/startup.lock",
                code="CHAT_STORAGE_UNAVAILABLE",
                status=503,
                wait_seconds=5,
            ):
                marker = f"{self.prefix}/storage.json"
                expected = {"v": 1, "instance_id": self.prefix}
                if self.fs.exists(marker):
                    if self.fs.read_json(marker) != expected:
                        raise ChatError(
                            "存储实例标识不符。", 503, "CHAT_STORAGE_CORRUPT"
                        )
                else:
                    with self.storage_lock():
                        self.fs.write_json(marker, expected, exclusive=True)
                self.recover()
        except BaseException:
            self.fs.close()
            raise

    def close(self):
        with self._read_cache_lock:
            self._read_cache.clear()
        self.fs.close()

    def storage_lock(self):
        return FileLock(
            self.fs,
            f"{self.prefix}/.locks/storage.lock",
            code="CHAT_STORAGE_UNAVAILABLE",
            status=503,
            wait_seconds=config.STORAGE_LOCK_WAIT_SECONDS,
        )

    def _principal(self, principal):
        integer(principal.user_id, 1, 2147483647)
        if principal.auth_kind != "session":
            raise ChatError("聊天需要登录会话。", 403, "CHAT_SESSION_REQUIRED")
        return f"{self.prefix}/users/{principal.user_id}"

    def _user_dirs(self, principal):
        root = self._principal(principal)
        for suffix in ("", "/conversations", "/deleted", "/.trash"):
            self.fs.mkdir(root + suffix, parents=True)
        return root

    def _check_scope(self, scope):
        if (
            not isinstance(scope, ConversationScope)
            or scope._store is not self
            or scope.instance_id != self.prefix
        ):
            raise not_found()
        self._principal(scope.principal)
        identifier(scope.cid)

    def _scope(self, principal, cid):
        self._principal(principal)
        return ConversationScope(principal, identifier(cid), self.prefix, self)

    @io_guard
    def open_conversation(self, principal, cid):
        scope = self._scope(principal, cid)
        if not self.fs.exists(scope.path) or self._deleted(scope):
            raise not_found()
        if not stat.S_ISDIR(self.fs.stat(scope.path).st_mode):
            raise not_found()
        return scope

    def _deleted(self, scope):
        root = self._principal(scope.principal)
        return self.fs.exists(f"{root}/deleted/{scope.cid}.json") or self.fs.exists(
            f"{root}/.trash/{scope.cid}"
        )

    def lease(self, scope, *, run=False):
        return ConversationLease(self, scope, run=run)

    @contextmanager
    def _with_lease(self, scope, lease=None):
        self._check_scope(scope)
        if lease is not None:
            lease.validate(self, scope)
            yield lease
        else:
            with self.lease(scope) as acquired:
                yield acquired

    def _reservations(self):
        result = []
        for name in self.fs.listdir(f"{self.prefix}/reservations"):
            if not re.fullmatch(r"[a-f0-9-]{36}\.json", name):
                raise storage_error()
            value = self.fs.read_json(f"{self.prefix}/reservations/{name}")
            try:
                object_fields(
                    value,
                    {
                        "v",
                        "operation_id",
                        "user_id",
                        "conversation_id",
                        "kind",
                        "bytes",
                        "created_at",
                    },
                )
                identifier(value["operation_id"])
                integer(value["user_id"], 1, 2147483647)
                integer(value["bytes"])
                if (
                    type(value["v"]) is not int
                    or value["v"] != 1
                    or f"{value['operation_id']}.json" != name
                    or value["kind"]
                    not in {
                        "run",
                        "upload",
                        "output",
                        "metadata",
                        "recovery",
                        "profile",
                        "context",
                    }
                    or not STAMP.fullmatch(str(value["created_at"]))
                ):
                    raise ValueError()
                if value["conversation_id"] is not None:
                    identifier(value["conversation_id"])
            except (ChatError, ValueError, TypeError):
                raise storage_error() from None
            result.append(value)
        return result

    def _reserve(self, principal, cid, amount, kind):
        root = self._user_dirs(principal)
        integer(amount, 1, config.TOTAL_STORAGE_BYTES)
        if kind not in {
            "run",
            "upload",
            "output",
            "metadata",
            "recovery",
            "profile",
            "context",
        }:
            raise ChatError("预留类型无效。")
        with self.storage_lock():
            records = self._reservations()
            total = self.fs.disk_usage(self.prefix)
            user = self.fs.disk_usage(root)
            reserved = sum(v["bytes"] for v in records)
            user_reserved = sum(
                v["bytes"] for v in records if v["user_id"] == principal.user_id
            )
            conv_reserved = (
                sum(
                    v["bytes"]
                    for v in records
                    if v["user_id"] == principal.user_id and v["conversation_id"] == cid
                )
                if cid
                else 0
            )
            convpath = f"{root}/conversations/{cid}" if cid else None
            conv = (
                self.fs.disk_usage(convpath)
                if convpath and self.fs.exists(convpath)
                else 0
            )
            # Include reservation metadata and atomic temporary publication headroom.
            overhead = 2048
            if (
                total + reserved + amount + overhead > self.settings.total_storage_bytes
                or user + user_reserved + amount + overhead > config.USER_STORAGE_BYTES
                or (cid and conv + conv_reserved + amount > config.CONVERSATION_BYTES)
            ):
                raise ChatError("聊天存储额度已满。", 507, "CHAT_STORAGE_LIMIT")
            if (
                self.fs.free_bytes()
                < config.DISK_FREE_MIN_BYTES + reserved + amount + overhead
            ):
                raise ChatError("存储空间不足。", 507, "CHAT_STORAGE_FULL")
            record = {
                "v": 1,
                "operation_id": str(uuid4()),
                "user_id": principal.user_id,
                "conversation_id": cid,
                "kind": kind,
                "bytes": amount,
                "created_at": utcnow(),
            }
            reservation = Reservation(self, record)
            self.fs.write_json(reservation.path, record, exclusive=True)
            return reservation

    def reserve(self, scope, amount, kind="run", *, lease):
        self._check_scope(scope)
        lease.validate(self, scope)
        if lease.reservation is not None:
            if lease.reservation.remaining < amount:
                raise ChatError("存储预留不足。", 507, "CHAT_STORAGE_LIMIT")
            return lease.reservation
        lease.reservation = self._reserve(scope.principal, scope.cid, amount, kind)
        return lease.reservation

    def reserve_user(self, principal, amount, kind="profile"):
        """Caller holds profile_lock for the lifetime of this reservation."""
        return self._reserve(principal, None, amount, kind)

    @contextmanager
    def profile_transaction(self, principal, required_bytes):
        """Caller already owns the profile lock; serialize bounded quota writes."""
        root = self._user_dirs(principal)
        integer(required_bytes, 1, config.USER_CONTEXT_STORAGE_BYTES)
        with self.reserve_user(principal, required_bytes, "profile") as reservation:
            with self.storage_lock():
                personal = sum(
                    self.fs.disk_usage(f"{root}/{name}")
                    for name in (
                        "profiles",
                        "skills",
                        ".context-tmp",
                        ".context-quarantine",
                    )
                    if self.fs.exists(f"{root}/{name}")
                )
                manifest = f"{root}/context-manifest.json"
                if self.fs.exists(manifest):
                    personal += self.fs.stat(manifest).st_size
                if personal + required_bytes > config.USER_CONTEXT_STORAGE_BYTES:
                    raise ChatError("个人设置存储额度已满。", 507, "CHAT_STORAGE_LIMIT")
                before = self.fs.disk_usage(root)
                try:
                    yield reservation
                finally:
                    reservation._consume_locked(
                        max(0, self.fs.disk_usage(root) - before)
                    )

    @contextmanager
    def _budget(self, scope, estimate, lease, kind="metadata", *, paths=None):
        temporary = None
        if lease.reservation is None:
            if lease.run:
                self.reserve(
                    scope, config.RUN_STORAGE_RESERVE_BYTES, "run", lease=lease
                )
            else:
                temporary = self._reserve(scope.principal, scope.cid, estimate, kind)
        reservation = lease.reservation or temporary
        try:
            with self.storage_lock():
                reservation.record = self.fs.read_json(reservation.path)
                if reservation.closed or reservation.remaining < estimate:
                    raise ChatError("存储预留不足。", 507, "CHAT_STORAGE_LIMIT")
                root = self._principal(scope.principal)
                before = (
                    self.fs.disk_usage(root)
                    if paths is None
                    else self._file_sizes(paths)
                )
                try:
                    yield
                finally:
                    after = (
                        self.fs.disk_usage(root)
                        if paths is None
                        else self._file_sizes(paths)
                    )
                    reservation._consume_locked(max(0, after - before))
        finally:
            if temporary:
                temporary.close()

    def _file_sizes(self, paths):
        total = 0
        for path in paths:
            try:
                info = self.fs.stat(path)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise storage_error()
            total += info.st_size
        return total

    @staticmethod
    def _journal_signature(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def _journal_identity(self, scope):
        fd = self.fs.open_file(f"{scope.path}/events.jsonl")
        try:
            return self._journal_signature(os.fstat(fd))
        finally:
            os.close(fd)

    def _cache_read(self, key, signature, state):
        # Bound both entry count and journal bytes (decoded data is larger).
        if (
            state.storage_state != "ok"
            or state.unpublished
            or state.offset > config.READ_CACHE_BYTES
        ):
            return
        with self._read_cache_lock:
            self._read_cache[key] = (signature, state)
            self._read_cache.move_to_end(key)
            while (
                len(self._read_cache) > config.READ_CACHE_ENTRIES
                or sum(item[1].offset for item in self._read_cache.values())
                > config.READ_CACHE_BYTES
            ):
                self._read_cache.popitem(last=False)

    def _published(self, scope):
        """Read a high-water snapshot, never infer publication from a trailing LF."""
        commit = self.fs.read_json(f"{scope.path}/commit.json")
        object_fields(commit, {"v", "seq", "byte_offset"})
        if type(commit["v"]) is not int or commit["v"] != 1:
            raise ValueError()
        integer(commit["seq"])
        integer(commit["byte_offset"], 0, config.CONVERSATION_BYTES)
        identity = self._journal_identity(scope)
        signature = (commit["seq"], commit["byte_offset"], identity)
        key = (scope.user_id, scope.cid)
        with self._read_cache_lock:
            cached = self._read_cache.get(key)
            if cached is not None and cached[0] == signature:
                self._read_cache.move_to_end(key)
                return cached[1]
            self._read_cache.pop(key, None)
        raw = self.fs.read_bytes(
            f"{scope.path}/events.jsonl", config.CONVERSATION_BYTES
        )
        if len(raw) < commit["byte_offset"]:
            raise ValueError()
        state = scan(raw[: commit["byte_offset"]], scope)
        if (
            state.seq != commit["seq"]
            or state.offset != commit["byte_offset"]
            or state.tail
        ):
            state.storage_state = "corrupt"
        state.commit_seq = commit["seq"]
        state.unpublished = len(raw) != commit["byte_offset"]
        if identity == self._journal_identity(scope):
            self._cache_read(key, signature, state)
        return state

    def _projections_current(self, scope, state):
        try:
            for name in ("meta", "files", "runs"):
                value = self.fs.read_json(
                    f"{scope.path}/{name}.json", config.CONVERSATION_BYTES
                )
                if (
                    not isinstance(value, dict)
                    or type(value.get("v")) is not int
                    or value["v"] != 1
                    or value.get("last_seq") != state.seq
                ):
                    return False
            return True
        except (FileNotFoundError, ChatError, ValueError, TypeError):
            return False

    def _state(self, scope, *, lease=None):
        self._check_scope(scope)
        if self._deleted(scope) or not self.fs.exists(scope.path):
            raise not_found()
        if lease is not None:
            lease.validate(self, scope)
            if lease._state is None:
                lease._state = self._recover_locked(scope, lease)
            return lease._state
        try:
            state = self._published(scope)
        except (FileNotFoundError, ValueError, TypeError, KeyError):
            try:
                with self.lease(scope) as acquired:
                    return self._recover_locked(scope, acquired)
            except ChatError as exc:
                if exc.code == "CHAT_CONVERSATION_BUSY":
                    raise ChatError(
                        "记录尚未提交，请稍后读取。", 503, "CHAT_STORAGE_UNAVAILABLE"
                    ) from None
                raise
        # Healthy GETs are strictly read-only: taking the writer lease on every
        # poll races an otherwise valid POST. Recover only actual evidence of
        # unpublished bytes, orphan runs, or missing/stale derived state.
        meta, files, _, _, _ = self._projection(scope, state)
        pending_delete = any(
            f.get("deleted_at")
            and self.fs.exists(f"{scope.path}/uploads/{f['id']}.blob")
            for f in files.values()
        )
        if state.storage_state == "corrupt" or (
            not state.unpublished
            and not pending_delete
            and meta["active_run_id"] is None
            and self._projections_current(scope, state)
        ):
            return state
        # A live writer keeps its long lease: read only its published prefix.
        try:
            with self.lease(scope) as acquired:
                return self._recover_locked(scope, acquired)
        except ChatError as exc:
            if exc.code != "CHAT_CONVERSATION_BUSY":
                raise
        return state

    def _projection(self, scope, state):
        if state.projection is None or state.projection.seq != state.seq:
            state.projection = Projection(scope)
            for event in state.events:
                state.projection.apply(event)
        meta, files, runs, requests, writes = state.projection.value()
        meta = copy.deepcopy(meta)
        files = file_deletions.overlay(self, scope, files)
        meta["storage_state"] = state.storage_state
        return meta, files, runs, requests, writes

    def _write_projections(self, scope, state):
        meta, files, runs, _, _ = self._projection(scope, state)
        self.fs.write_json(
            f"{scope.path}/meta.json",
            {
                "v": 1,
                "owner_user_id": scope.user_id,
                "instance_id": self.prefix,
                **meta,
            },
        )
        # Stream chunks do not change the file catalogue or the conversation
        # list. The journal+commit are authoritative; refresh those derived
        # snapshots at the next non-chunk event (and always on recovery).
        if state.events and state.events[-1]["type"] in self._TEXT_EVENTS:
            return
        self.fs.write_json(
            f"{scope.path}/files.json",
            {
                "v": 1,
                "last_seq": state.seq,
                # Deletions live in independent durable controls. Do not grow
                # or rewrite historical file DTOs in the journal-derived cache.
                "entries": [
                    {k: v for k, v in f.items() if k != "deleted_at"}
                    for f in files.values()
                ],
            },
        )
        self.fs.write_json(
            f"{scope.path}/runs.json",
            {
                "v": 1,
                "last_seq": state.seq,
                "entries": [self._public_run(v) for v in runs.values()],
            },
        )
        self._index_entry(scope, meta)

    def _index_entry(self, scope, meta=None):
        """Caller owns storage lock; an index is never an authorization source."""
        path = f"{self._principal(scope.principal)}/index.json"
        try:
            old = self.fs.read_json(path)
            entries = (
                old["entries"]
                if isinstance(old, dict)
                and old.get("v") == 1
                and isinstance(old.get("entries"), list)
                else []
            )
        except (FileNotFoundError, KeyError, ChatError):
            entries = []
        entries = [
            e for e in entries if isinstance(e, dict) and e.get("id") != scope.cid
        ]
        if meta is not None:
            entries.append(meta)
        self.fs.write_json(path, {"v": 1, "entries": entries})

    def _projection_estimate(self, scope, state, *, projection=None):
        projected = (
            self._projection(scope, state) if projection is None else projection.value()
        )
        index = f"{self._principal(scope.principal)}/index.json"
        index_bytes = self.fs.stat(index).st_size if self.fs.exists(index) else 0
        return 32768 + 2 * (
            sum(len(canonical_json(v)) for v in projected[:3]) + index_bytes
        )

    def _recover_locked(self, scope, lease):
        """Exclusive lease proves no writer: validate complete bytes, repair only tail."""
        lease.validate(self, scope)
        try:
            raw = self.fs.read_bytes(
                f"{scope.path}/events.jsonl", config.CONVERSATION_BYTES
            )
        except FileNotFoundError:
            return LogState(storage_state="corrupt")
        state = scan(raw, scope)
        if state.storage_state == "corrupt":
            return state
        needs_publish = True
        try:
            commit = self.fs.read_json(f"{scope.path}/commit.json")
            needs_publish = commit != {
                "v": 1,
                "seq": state.seq,
                "byte_offset": state.offset,
            }
        except (FileNotFoundError, ChatError):
            pass
        repaired = bool(state.tail or needs_publish)
        unpublished = needs_publish
        # Recovery writes have a budget too. No mutation if disk is exhausted.
        try:
            if repaired:
                with self._budget(scope, len(state.tail) + 262144, lease, "recovery"):
                    if state.tail:
                        self.fs.mkdir(f"{scope.path}/.quarantine")
                        self.fs.atomic_write(
                            f"{scope.path}/.quarantine/tail-{uuid4()}.bin",
                            state.tail,
                            exclusive=True,
                        )
                        fd = self.fs.open_file(f"{scope.path}/events.jsonl", os.O_RDWR)
                        try:
                            os.ftruncate(fd, state.offset)
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    else:
                        fd = self.fs.open_file(
                            f"{scope.path}/events.jsonl", os.O_RDONLY
                        )
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    self.fs.write_json(
                        f"{scope.path}/commit.json",
                        {"v": 1, "seq": state.seq, "byte_offset": state.offset},
                    )
                unpublished = False
                state.tail = b""
                state.storage_state = "repaired"
            lease._state = state
            meta, files, runs, _, _ = self._projection(scope, state)
            # A crash after durable deletion intent must not retain the blob.
            file_deletions.purge(self, scope, files)
            # Isolate unpublished immutable files. Never manufacture file.added.
            references = {"uploads": set(), "outputs": set(), "contexts": set()}
            for f in files.values():
                references["uploads" if f["kind"] == "upload" else "outputs"].add(
                    f"{f['id']}.blob"
                )
            for event in state.events:
                if event["type"] == "run.accepted":
                    references["contexts"].add(f"{event['run_id']}.json")
                elif event["type"] == "context.compacted":
                    references["contexts"].add(
                        event["data"]["checkpoint"]["file"].split("/")[1]
                    )
            orphans = []
            for folder in ("uploads", "outputs", "contexts", ".tmp"):
                try:
                    names = self.fs.listdir(f"{scope.path}/{folder}")
                except FileNotFoundError:
                    names = []
                for name in names:
                    if name not in references.get(folder, set()):
                        orphans.append((folder, name))
            if orphans:
                with self._budget(scope, 262144, lease, "recovery"):
                    for folder, name in orphans:
                        self.fs.rename(
                            f"{scope.path}/{folder}/{name}",
                            f"{scope.path}/.quarantine/orphan-{uuid4()}",
                        )
                state.storage_state = "repaired"
            for run in runs.values():
                if run["status"] not in TERMINAL:
                    self.append(
                        scope,
                        "run.terminal",
                        {
                            "status": "interrupted",
                            "reason": "worker_interrupted",
                            "error_code": "CHAT_UPSTREAM_INTERRUPTED",
                            "uncertain": run["model_requests"] > 0,
                            "model_requests": run["model_requests"],
                            "tool_calls": run["tool_calls"],
                            "usage": run["usage"],
                            "usage_complete": run["usage_complete"],
                        },
                        run_id=run["id"],
                        lease=lease,
                    )
                    state.storage_state = "repaired"
            # Cache failures never hide a readable committed prefix.
            try:
                with self._budget(
                    scope, self._projection_estimate(scope, state), lease, "recovery"
                ):
                    self._write_projections(scope, state)
            except ChatError:
                pass
        except ChatError as exc:
            if exc.code not in {
                "CHAT_STORAGE_LIMIT",
                "CHAT_STORAGE_FULL",
                "CHAT_STORAGE_UNAVAILABLE",
            }:
                raise
            if unpublished:
                lease.poisoned = True
                try:
                    return self._published(scope)
                except (FileNotFoundError, ValueError, TypeError, KeyError):
                    raise storage_error() from None
        return state

    @io_guard
    def append(self, scope, event_type, data, run_id=None, *, lease):
        if event_type == "run.accepted":
            # Serialize acceptance with deletion intent, not with a whole run.
            with file_deletions.lock(self, scope):
                expected = getattr(lease, "file_deletion_signature", None)
                if expected is not None and expected != file_deletions.signature(
                    self, scope
                ):
                    raise ChatError(
                        "附件已变化，请核对后重新发送。",
                        409,
                        "CHAT_ATTACHMENTS_CHANGED",
                    )
                return self._append(scope, event_type, data, run_id, lease=lease)
        return self._append(scope, event_type, data, run_id, lease=lease)

    def _append(self, scope, event_type, data, run_id=None, *, lease):
        lease.validate(self, scope)
        state = self._state(scope, lease=lease)
        if state.storage_state == "corrupt":
            raise ChatError("聊天记录需要修复。", 503, "CHAT_STORAGE_CORRUPT")
        event = {
            "v": 1,
            "seq": state.seq + 1,
            "type": event_type,
            "conversation_id": scope.cid,
            "run_id": run_id,
            "at": utcnow(),
            "data": data,
        }
        event = json.loads(canonical_json(event))
        validate_event(event, scope, state.seq + 1)
        self._projection(scope, state)
        proposed = state.projection.fork(event)
        proposed.apply(event)
        line = canonical_json(event) + b"\n"
        if run_id is not None:
            if run_id not in lease.event_bytes:
                lease.event_bytes[run_id] = sum(
                    len(canonical_json(e)) + 1
                    for e in state.events
                    if e["run_id"] == run_id
                )
            used = lease.event_bytes[run_id]
            maximum = config.RUN_EVENT_BYTES - (
                0
                if event_type == "run.terminal"
                else config.EVENT_TERMINAL_RESERVE_BYTES
            )
            if used + len(line) > maximum:
                raise ChatError("本轮记录已达上限。", 507, "CHAT_STORAGE_LIMIT")
        if event_type == "run.accepted":
            snapshot = self._raw_context(scope, run_id)
            ref = data["context_snapshot"]
            if hashlib.sha256(snapshot).hexdigest() != ref["sha256"]:
                raise ChatError("上下文快照校验失败。", 503, "CHAT_STORAGE_CORRUPT")
            snap = json.loads(snapshot)
            if (
                snap["context_revision"] != ref["context_revision"]
                or snap["skill_ids"] != ref["skill_ids"]
            ):
                raise ChatError("上下文快照版本不符。", 503, "CHAT_STORAGE_CORRUPT")
        elif event_type == "context.compacted":
            self._raw_working_context(scope, data["checkpoint"], run_id=run_id)
        chunk = event_type in self._TEXT_EVENTS
        estimate = len(line) + (
            32768 + 2 * len(canonical_json(proposed.meta))
            if chunk
            else self._projection_estimate(scope, state, projection=proposed)
        )
        paths = [
            f"{scope.path}/{name}"
            for name in ("events.jsonl", "commit.json", "meta.json")
        ]
        if not chunk:
            paths.extend(f"{scope.path}/{name}.json" for name in ("files", "runs"))
            paths.append(f"{self._principal(scope.principal)}/index.json")
        with self._budget(scope, estimate, lease, paths=paths):
            try:
                fd = self.fs.open_file(
                    f"{scope.path}/events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT
                )
                try:
                    # A cached lease must not append over an unknown external tail.
                    if os.fstat(fd).st_size != state.offset:
                        raise storage_error()
                    SafeFS.write_all(fd, line)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self.fs.write_json(
                    f"{scope.path}/commit.json",
                    {
                        "v": 1,
                        "seq": event["seq"],
                        "byte_offset": state.offset + len(line),
                    },
                )
            except (OSError, ChatError) as exc:
                lease.poisoned = True
                raise storage_error(exc) from None
            state.events.append(event)
            state.projection = proposed
            state.offset += len(line)
            state.commit_seq = state.seq
            if run_id is not None:
                lease.event_bytes[run_id] += len(line)
            # If projection publication fails, the event is still committed;
            # stop the caller, never rewind or replay its paid/file action.
            self._write_projections(scope, state)
        return event

    @io_guard
    def create_conversation(self, principal, cid=None, title="新聊天"):
        cid, title = identifier(cid or str(uuid4())), clean_title(title)
        root = self._user_dirs(principal)
        scope = self._scope(principal, cid)
        with self.lease(scope) as lease:
            if self._deleted(scope):
                raise ChatError("聊天已删除。", 410, "CHAT_DELETED")
            if self.fs.exists(scope.path):
                return self.get_conversation(scope, lease=lease)
            # The storage lock serializes the per-user directory count too.
            reservation = self._reserve(principal, cid, 524288, "metadata")
            lease.reservation = reservation
            with self.storage_lock():
                if (
                    len(self.fs.listdir(f"{root}/conversations"))
                    >= config.CONVERSATIONS_PER_USER
                ):
                    raise ChatError("聊天数量已达上限。", 507, "CHAT_STORAGE_LIMIT")
                for suffix in (
                    "",
                    "/uploads",
                    "/outputs",
                    "/contexts",
                    "/control",
                    "/.tmp",
                    "/.quarantine",
                ):
                    self.fs.mkdir(scope.path + suffix)
            lease._state = LogState()
            self.append(
                scope,
                "conversation.created",
                {
                    "owner_user_id": principal.user_id,
                    "instance_id": self.prefix,
                    "title": title,
                },
                lease=lease,
            )
            return self.get_conversation(scope, lease=lease)

    @io_guard
    def get_conversation(self, scope, *, lease=None):
        state = self._state(scope, lease=lease)
        return self._projection(scope, state)[0]

    @io_guard
    def rename(self, scope, title, expected_revision, *, lease=None):
        title = clean_title(title)
        with self._with_lease(scope, lease) as lease:
            state = self._state(scope, lease=lease)
            self._revision(state, expected_revision)
            self.append(scope, "conversation.renamed", {"title": title}, lease=lease)
            return self.get_conversation(scope, lease=lease)

    @staticmethod
    def _revision(state, expected):
        integer(expected, 1)
        if state.seq != expected:
            raise ChatError("聊天已变化，请刷新。", 409, "CHAT_REVISION_CONFLICT")

    @io_guard
    def delete(self, scope, expected_revision, *, lease=None):
        self._check_scope(scope)
        root = self._principal(scope.principal)
        if self._deleted(scope):
            return
        with self._with_lease(scope, lease) as lease, file_deletions.lock(self, scope):
            state = self._state(scope, lease=lease)
            self._revision(state, expected_revision)
            if self._projection(scope, state)[0]["active_run_id"]:
                raise ChatError("聊天正在运行。", 409, "CHAT_CONVERSATION_BUSY")
            with self._budget(scope, 262144, lease):
                self.fs.rename(scope.path, f"{root}/.trash/{scope.cid}")
                self.fs.write_json(
                    f"{root}/deleted/{scope.cid}.json",
                    {"v": 1, "cid": scope.cid, "deleted_at": utcnow()},
                )
                self._index_entry(scope)
            with self._read_cache_lock:
                self._read_cache.pop((scope.user_id, scope.cid), None)
            try:
                self.fs.remove_tree(f"{root}/.trash/{scope.cid}")
            except (ChatError, OSError):
                pass

    @io_guard
    def list_conversations(
        self, principal, limit=config.LIST_PAGE_DEFAULT, cursor=None
    ):
        integer(limit, 1, config.LIST_PAGE_MAX)
        root = self._principal(principal)
        if not self.fs.exists(root):
            return {
                "conversations": [],
                "next_cursor": None,
                "has_more": False,
                "total": 0,
                "warnings": [],
            }
        entries, warnings = [], []
        # Directory enumeration, never index.json, is the complete membership set.
        for cid in self.fs.listdir(f"{root}/conversations"):
            try:
                identifier(cid)
            except ChatError:
                warnings.append({"code": "CHAT_STORAGE_CORRUPT"})
                continue
            scope = self._scope(principal, cid)
            if self._deleted(scope):
                continue
            try:
                entries.append(self.get_conversation(scope))
                if entries[-1]["storage_state"] == "corrupt":
                    warnings.append(
                        {"conversation_id": cid, "code": "CHAT_STORAGE_CORRUPT"}
                    )
            except ChatError as exc:
                if exc.code == "CHAT_NOT_FOUND":
                    # A concurrent explicit deletion may remove a directory
                    # after enumeration; it is no longer a member of this page.
                    continue
                # Unsafe filesystem objects fail closed; do not turn them into
                # a successful empty list or expose their data as a placeholder.
                if exc.code != "CHAT_STORAGE_CORRUPT":
                    raise
                placeholder = project(scope, [])[0]
                placeholder["storage_state"] = "corrupt"
                entries.append(placeholder)
                warnings.append({"conversation_id": cid, "code": exc.code})
        entries.sort(key=lambda e: (e["created_at"], e["id"]), reverse=True)
        all_entries = entries
        high = [entries[0]["created_at"], entries[0]["id"]] if entries else None
        after = None
        if cursor is not None:
            high, after = self._cursor(cursor)
        if high is not None:
            entries = [e for e in entries if (e["created_at"], e["id"]) <= tuple(high)]
        total = len(entries)
        page = [
            e
            for e in entries
            if after is None or (e["created_at"], e["id"]) < tuple(after)
        ]
        more = len(page) > limit
        page = page[:limit]
        next_cursor = None
        if more:
            next_cursor = (
                base64.urlsafe_b64encode(
                    canonical_json(
                        {
                            "v": 1,
                            "high_water": high,
                            "after": [page[-1]["created_at"], page[-1]["id"]],
                        }
                    )
                )
                .decode()
                .rstrip("=")
            )
        # A complete, current index needs no write or lock during normal reads.
        try:
            cached = self.fs.read_json(f"{root}/index.json", config.USER_STORAGE_BYTES)
            cached_entries = cached.get("entries") if isinstance(cached, dict) else None
            current = (
                isinstance(cached, dict)
                and cached.get("v") == 1
                and isinstance(cached_entries, list)
                and len(cached_entries) == len(all_entries)
                and all(
                    isinstance(e, dict) and isinstance(e.get("id"), str)
                    for e in cached_entries
                )
                and {e["id"]: e for e in cached_entries}
                == {e["id"]: e for e in all_entries}
            )
        except (FileNotFoundError, ChatError, ValueError, TypeError):
            current = False
        # Best-effort derived repair; failure never hides a reconciled chat.
        try:
            if current:
                return {
                    "conversations": page,
                    "next_cursor": next_cursor,
                    "has_more": more,
                    "total": total,
                    "warnings": warnings,
                }
            with profile_lock(self.fs, self.prefix, principal.user_id):
                with self.reserve_user(
                    principal, len(canonical_json(all_entries)) * 2 + 8192, "metadata"
                ):
                    with self.storage_lock():
                        self.fs.write_json(
                            f"{root}/index.json", {"v": 1, "entries": all_entries}
                        )
        except ChatError:
            pass
        return {
            "conversations": page,
            "next_cursor": next_cursor,
            "has_more": more,
            "total": total,
            "warnings": warnings,
        }

    @staticmethod
    def _cursor(value):
        try:
            if (
                not isinstance(value, str)
                or len(value) > 1024
                or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
            ):
                raise ValueError()
            raw = base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )
            if len(raw) > 1024:
                raise ValueError()
            body = json.loads(raw)
            object_fields(body, {"v", "high_water", "after"})
            if type(body["v"]) is not int or body["v"] != 1:
                raise ValueError()
            for key in ("high_water", "after"):
                pair = body[key]
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or not isinstance(pair[0], str)
                    or not STAMP.fullmatch(pair[0])
                ):
                    raise ValueError()
                identifier(pair[1])
            if tuple(body["after"]) > tuple(body["high_water"]):
                raise ValueError()
            return body["high_water"], body["after"]
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ChatError("分页位置无效。", 400, "CHAT_INVALID_CURSOR") from None

    @io_guard
    def read_events(
        self,
        scope,
        after_seq=0,
        limit=config.EVENT_PAGE_MAX,
        until_seq=None,
        *,
        lease=None,
    ):
        return self._event_page(
            self._state(scope, lease=lease), after_seq, limit, until_seq
        )

    @staticmethod
    def _event_page(state, after_seq=0, limit=config.EVENT_PAGE_MAX, until_seq=None):
        integer(after_seq)
        integer(limit, 1, config.EVENT_PAGE_MAX)
        until_seq = state.seq if until_seq is None else integer(until_seq)
        if not after_seq <= until_seq <= state.seq:
            raise ChatError("事件分页位置无效。")
        result, size = [], 0
        for event in state.events[after_seq : min(until_seq, after_seq + limit)]:
            item_size = len(canonical_json(event)) + 1
            if result and (
                len(result) == limit or size + item_size > config.EVENT_RESPONSE_BYTES
            ):
                break
            result.append(event)
            size += item_size
        last = result[-1]["seq"] if result else after_seq
        return {
            "events": copy.deepcopy(result),
            "until_seq": until_seq,
            "next_after_seq": last,
            "has_more": last < until_seq,
            "storage_state": state.storage_state,
        }

    @io_guard
    def sync(self, scope, after_seq=0, limit=config.EVENT_PAGE_MAX, files_revision=""):
        """One published snapshot for recovery; never resumes a run or dispatches."""
        if not isinstance(files_revision, str) or (
            files_revision and not re.fullmatch(r"[0-9a-f]{64}", files_revision)
        ):
            raise ChatError("文件版本无效。")
        state = self._state(scope)
        meta, files, runs, _, _ = self._projection(scope, state)
        file_list = list(files.values())
        revision = hashlib.sha256(canonical_json(file_list)).hexdigest()
        rid = meta["active_run_id"] or next(reversed(runs), None)
        return {
            "conversation": meta,
            "events": self._event_page(state, after_seq, limit),
            "run": self._run_view(scope, rid, runs[rid], state) if rid else None,
            "files": file_list if files_revision != revision else None,
            "files_revision": revision,
            "required_context_revision": state.projection.context_revision,
        }

    @io_guard
    def files(self, scope, *, lease=None):
        return list(
            self._projection(scope, self._state(scope, lease=lease))[1].values()
        )

    @io_guard
    def read_file(self, scope, file_id, *, lease=None):
        file_id = identifier(file_id)
        entry = self._projection(scope, self._state(scope, lease=lease))[1].get(file_id)
        if entry is None:
            raise not_found()
        if entry.get("deleted_at"):
            raise ChatError("附件已删除。", 410, "CHAT_FILE_DELETED")
        folder = "uploads" if entry["kind"] == "upload" else "outputs"
        try:
            data = self.fs.read_bytes(
                f"{scope.path}/{folder}/{file_id}.blob", config.UPLOAD_FILE_BYTES
            )
        except FileNotFoundError:
            if file_id in file_deletions.records(self, scope):
                raise ChatError("附件已删除。", 410, "CHAT_FILE_DELETED") from None
            raise ChatError("文件需要修复。", 503, "CHAT_STORAGE_CORRUPT") from None
        if (
            len(data) != entry["size_bytes"]
            or hashlib.sha256(data).hexdigest() != entry["sha256"]
        ):
            raise ChatError("文件校验失败。", 503, "CHAT_STORAGE_CORRUPT")
        if file_id in file_deletions.records(self, scope):
            raise ChatError("附件已删除。", 410, "CHAT_FILE_DELETED")
        return dict(entry), data

    @io_guard
    def delete_upload(self, scope, file_id):
        self._check_scope(scope)
        return file_deletions.delete(self, scope, file_id)

    @io_guard
    def upload(self, scope, name, content, client_file_id, *, lease=None):
        return self._add_file(
            scope, name, content, identifier(client_file_id), "upload", lease=lease
        )

    @io_guard
    def upload_request(self, scope):
        """Admit one HTTP upload before spooling into the pool's bounded /tmp."""
        self._check_scope(scope)
        return FileLock(
            self.fs,
            f"{self.prefix}/.locks/upload.lock",
            code="CHAT_UPLOAD_BUSY",
            status=429,
        )

    @staticmethod
    def normalize_file(name, content, kind="upload"):
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 120
            or name.strip() != name
            or name.startswith(".")
            or name.endswith((".", " "))
            or any(ord(c) < 32 or ord(c) == 127 or c in '/\\:<>"|?*' for c in name)
        ):
            raise ChatError("文件名无效。")
        mime = media.mime_for(name, output=kind != "upload")
        try:
            raw = (
                content.encode("utf-8") if isinstance(content, str) else bytes(content)
            )
            if len(raw) > (
                config.UPLOAD_FILE_BYTES
                if kind == "upload"
                else config.OUTPUT_FILE_BYTES
                if mime != media.TEXT_MIME
                else config.TOOL_WRITE_BYTES
            ):
                raise ChatError("文件超过大小上限。", 413, "CHAT_FILE_TOO_LARGE")
            if mime != media.TEXT_MIME:
                media.extract(name, raw)
                return raw
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            raw.decode("utf-8")
            if b"\x00" in raw:
                raise ValueError()
        except ChatError:
            raise
        except (ValueError, UnicodeError, TypeError):
            raise ChatError(
                "文件必须是无NUL的UTF-8文本。", 415, "CHAT_FILE_TYPE_UNSUPPORTED"
            ) from None
        return raw

    def _add_file(self, scope, name, content, fid, kind, *, lease=None, run_id=None):
        raw = self.normalize_file(name, content, kind)
        with self._with_lease(scope, lease) as lease:
            state = self._state(scope, lease=lease)
            _, files, _, _, _ = self._projection(scope, state)
            digest = hashlib.sha256(raw).hexdigest()
            if fid in files:
                original = files[fid]
                if original.get("deleted_at"):
                    raise ChatError(
                        "附件已删除，请重新选择文件上传。", 410, "CHAT_FILE_DELETED"
                    )
                if original["sha256"] != digest or original["kind"] != kind:
                    raise ChatError("文件ID内容冲突。", 409, "CHAT_FILE_CONFLICT")
                return dict(original)
            if len(files) >= config.FILES_PER_CONVERSATION:
                raise ChatError("文件数量已达上限。", 507, "CHAT_STORAGE_LIMIT")
            version = (
                max(
                    (
                        f["version"]
                        for f in files.values()
                        if f["kind"] == "output" and f["logical_name"] == name
                    ),
                    default=0,
                )
                + 1
            )
            entry = {
                "id": fid,
                "kind": kind,
                "name": name,
                "mime": media.mime_for(name, output=kind != "upload"),
                "size_bytes": len(raw),
                "sha256": digest,
                "created_at": utcnow(),
                "logical_name": name if kind == "output" else None,
                "version": version if kind == "output" else None,
                "source_run_id": run_id,
                "download_path": f"/cockpit/api/chat/conversations/{scope.cid}/files/{fid}/download",
            }
            # Reuse a single reservation across blob publication and event commit.
            temporary = lease.reservation is None and not lease.run
            if lease.reservation is None:
                self.reserve(
                    scope,
                    config.RUN_STORAGE_RESERVE_BYTES
                    if lease.run
                    else len(raw) * 2 + 524288,
                    "run" if lease.run else "upload",
                    lease=lease,
                )
            try:
                with self._budget(scope, len(raw) * 2 + 8192, lease, kind):
                    folder = "uploads" if kind == "upload" else "outputs"
                    self.fs.atomic_write(
                        f"{scope.path}/{folder}/{fid}.blob", raw, exclusive=True
                    )
                self.append(
                    scope, "file.added", {"file": entry}, run_id=run_id, lease=lease
                )
            finally:
                if temporary:
                    lease.reservation.close()
                    lease.reservation = None
            return dict(entry)

    @staticmethod
    def _public_run(run):
        return copy.deepcopy(
            {k: v for k, v in run.items() if not k.startswith("_") and k != "reason"}
        )

    @io_guard
    def get_run(self, scope, rid, legacy_rid=None, *, lease=None):
        if legacy_rid is not None:
            scope, rid = self.open_conversation(scope, rid), legacy_rid
        rid = identifier(rid)
        state = self._state(scope, lease=lease)
        run = self._projection(scope, state)[2].get(rid)
        if run is None:
            raise not_found()
        return self._run_view(scope, rid, run, state)

    def _run_view(self, scope, rid, run, state):
        value = self._public_run(run)
        value["cancel_requested"] = self.is_cancelled(scope, rid)
        value["context_snapshot"] = copy.deepcopy(state.projection.accepted[rid])
        return value

    @io_guard
    def find_request(self, scope, client_request_id, payload=None, *, lease=None):
        identifier(client_request_id)
        _, _, runs, requests, _ = self._projection(
            scope, self._state(scope, lease=lease)
        )
        rid = requests.get(client_request_id)
        if rid is None:
            return None
        run = runs[rid]
        if payload is not None and request_digest(payload) != run["_digest"]:
            raise ChatError("相同请求ID的内容不同。", 409, "CHAT_IDEMPOTENCY_CONFLICT")
        return self._public_run(run)

    @io_guard
    def cancel(self, scope, rid):
        run = self.get_run(scope, rid)
        if run["status"] in TERMINAL:
            return {
                "run_id": rid,
                "status": run["status"],
                "cancel_requested": run["cancel_requested"],
            }
        # A run's reservation already covers this bounded control file and its
        # atomic temporary copy. Do not acquire the long conversation lease.
        with self.storage_lock():
            path = f"{scope.path}/control/{rid}.cancel.json"
            if not self.fs.exists(path):
                records = [
                    record
                    for record in self._reservations()
                    if record["user_id"] == scope.user_id
                    and record["conversation_id"] == scope.cid
                    and record["kind"] == "run"
                ]
                if not records:
                    # Producer may have finished between lookup and this lock.
                    current = self._projection(scope, self._published(scope))[2][rid]
                    if current["status"] in TERMINAL:
                        return {
                            "run_id": rid,
                            "status": current["status"],
                            "cancel_requested": False,
                        }
                    raise storage_error()
                reservation = Reservation(self, records[0])
                record = {"v": 1, "run_id": rid, "cancel_requested": True}
                needed = len(canonical_json(record))
                if reservation.remaining < needed * 2 + 1024:
                    raise ChatError("停止标志预留不足。", 507, "CHAT_STORAGE_LIMIT")
                self.fs.write_json(path, record)
                reservation._consume_locked(needed)
        return {"run_id": rid, "status": run["status"], "cancel_requested": True}

    def is_cancelled(self, scope, rid):
        self._check_scope(scope)
        identifier(rid)
        path = f"{scope.path}/control/{rid}.cancel.json"
        if not self.fs.exists(path):
            return False
        value = self.fs.read_json(path, 1024)
        if value != {"v": 1, "run_id": rid, "cancel_requested": True}:
            raise storage_error()
        return True

    @io_guard
    def write_context(self, scope, rid, snapshot, *, lease):
        lease.validate(self, scope)
        identifier(rid)
        self._state(scope, lease=lease)
        self._validate_context(scope, rid, snapshot)
        raw = canonical_json(snapshot) + b"\n"
        if len(raw) > config.CONTEXT_SNAPSHOT_BYTES:
            raise ChatError("个人上下文过大。", 413, "CHAT_CONTEXT_TOO_LARGE")
        path = f"{scope.path}/contexts/{rid}.json"
        with self._budget(scope, len(raw) * 2 + 8192, lease, "context"):
            if self.fs.exists(path):
                if self.fs.read_bytes(path, config.CONTEXT_SNAPSHOT_BYTES) != raw:
                    raise ChatError(
                        "上下文快照不可改写。", 409, "CHAT_IDEMPOTENCY_CONFLICT"
                    )
            else:
                self.fs.atomic_write(path, raw, exclusive=True)
        return {
            "file": f"contexts/{rid}.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "context_revision": snapshot["context_revision"],
            "skill_ids": list(snapshot["skill_ids"]),
        }

    def _raw_context(self, scope, rid):
        try:
            return self.fs.read_bytes(
                f"{scope.path}/contexts/{identifier(rid)}.json",
                config.CONTEXT_SNAPSHOT_BYTES,
            )
        except FileNotFoundError:
            raise ChatError("上下文快照缺失。", 503, "CHAT_STORAGE_CORRUPT") from None

    @io_guard
    def write_working_context(self, scope, rid, checkpoint, *, lease):
        """Immutable bounded working window; not the canonical UI transcript."""
        lease.validate(self, scope)
        identifier(rid)
        if self.get_run(scope, rid, lease=lease)["status"] in TERMINAL:
            raise ChatError("运行已结束。", 409, "CHAT_REVISION_CONFLICT")
        if not isinstance(checkpoint, dict):
            raise ChatError("工作上下文无效。")
        wrapped = {
            "v": 1,
            "instance_id": scope.instance_id,
            "owner_user_id": scope.user_id,
            "conversation_id": scope.cid,
            "run_id": rid,
            "checkpoint": checkpoint,
        }
        raw = canonical_json(wrapped) + b"\n"
        if len(raw) > config.MODEL_REQUEST_BYTES:
            raise ChatError("工作上下文超过大小限制。", 413, "CHAT_CONTEXT_TOO_LARGE")
        reference = {
            "file": f"contexts/{uuid4()}.working.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
        with self._budget(scope, len(raw) * 2 + 8192, lease, "context"):
            self.fs.atomic_write(
                f"{scope.path}/{reference['file']}", raw, exclusive=True
            )
        return reference

    def _raw_working_context(self, scope, reference, *, run_id=None):
        object_fields(reference, {"file", "sha256", "size_bytes"})
        match = re.fullmatch(
            r"contexts/([a-f0-9-]{36})\.working\.json", str(reference["file"])
        )
        if not match:
            raise ChatError("工作上下文路径无效。", 503, "CHAT_STORAGE_CORRUPT")
        identifier(match.group(1))
        try:
            raw = self.fs.read_bytes(
                f"{scope.path}/{reference['file']}", config.MODEL_REQUEST_BYTES
            )
        except FileNotFoundError:
            raise ChatError("工作上下文缺失。", 503, "CHAT_STORAGE_CORRUPT") from None
        if (
            len(raw) != reference["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != reference["sha256"]
        ):
            raise ChatError("工作上下文校验失败。", 503, "CHAT_STORAGE_CORRUPT")
        try:
            value = json.loads(raw)
            object_fields(
                value,
                {
                    "v",
                    "instance_id",
                    "owner_user_id",
                    "conversation_id",
                    "run_id",
                    "checkpoint",
                },
            )
            if (
                value["v"] != 1
                or type(value["v"]) is not int
                or type(value["owner_user_id"]) is not int
                or value["owner_user_id"] != scope.user_id
                or value["instance_id"] != scope.instance_id
                or value["conversation_id"] != scope.cid
                or run_id is not None
                and value["run_id"] != run_id
                or not isinstance(value["checkpoint"], dict)
            ):
                raise ValueError()
            identifier(value["run_id"])
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ChatError("工作上下文损坏。", 503, "CHAT_STORAGE_CORRUPT") from None
        return value["checkpoint"]

    @io_guard
    def read_working_context(self, scope, reference, *, lease=None):
        self._check_scope(scope)
        state = self._state(scope, lease=lease)
        event = next(
            (
                e
                for e in reversed(state.events)
                if e["type"] == "context.compacted"
                and e["data"]["checkpoint"] == reference
            ),
            None,
        )
        if event is None:
            raise not_found()
        return self._raw_working_context(scope, reference, run_id=event["run_id"])

    @io_guard
    def read_context(self, scope, rid):
        state = self._state(scope)
        accepted = next(
            (
                e
                for e in state.events
                if e["type"] == "run.accepted" and e["run_id"] == identifier(rid)
            ),
            None,
        )
        if accepted is None:
            raise not_found()
        raw = self._raw_context(scope, rid)
        if (
            hashlib.sha256(raw).hexdigest()
            != accepted["data"]["context_snapshot"]["sha256"]
        ):
            raise ChatError("上下文快照校验失败。", 503, "CHAT_STORAGE_CORRUPT")
        try:
            value = json.loads(raw)
            self._validate_context(scope, rid, value)
        except (ValueError, TypeError, KeyError):
            raise ChatError("上下文快照损坏。", 503, "CHAT_STORAGE_CORRUPT") from None
        return value

    @staticmethod
    def _validate_context(scope, rid, value):
        object_fields(
            value,
            {
                "v",
                "conversation_id",
                "run_id",
                "captured_at",
                "context_revision",
                "policy_version",
                "profiles",
                "skills",
                "skill_ids",
                "budget",
            },
        )
        if (
            type(value["v"]) is not int
            or value["v"] != 1
            or value["conversation_id"] != scope.cid
            or value["run_id"] != rid
            or value["policy_version"] != "personal-context-v1"
            or not STAMP.fullmatch(str(value["captured_at"]))
        ):
            raise ChatError("上下文快照无效。")
        integer(value["context_revision"])
        ids = id_list(value["skill_ids"], config.SKILLS_PER_RUN)
        if not isinstance(value["profiles"], list) or [
            p.get("name") for p in value["profiles"] if isinstance(p, dict)
        ] != ["MEMORY.md", "SOUL.md", "IDENTITY.md"]:
            raise ChatError("个人文档无效。")
        limits = {
            "MEMORY.md": config.MEMORY_BYTES,
            "SOUL.md": config.SOUL_BYTES,
            "IDENTITY.md": config.IDENTITY_BYTES,
        }
        for item in value["profiles"]:
            object_fields(
                item,
                {"name", "revision", "content", "sha256", "size_bytes", "updated_at"},
            )
            ChatStore._document(item, limits[item["name"]])
        if not isinstance(value["skills"], list) or len(value["skills"]) != len(ids):
            raise ChatError("Skill快照无效。")
        for sid, item in zip(ids, value["skills"]):
            object_fields(
                item,
                {
                    "id",
                    "name",
                    "description",
                    "revision",
                    "sha256",
                    "size_bytes",
                    "created_at",
                    "updated_at",
                    "content",
                },
            )
            if item["id"] != sid:
                raise ChatError("Skill快照无效。")
            ChatStore._document(item, config.SKILL_FILE_BYTES)
        object_fields(value["budget"], {"personal_block_bytes", "limit_bytes"})
        integer(
            value["budget"]["personal_block_bytes"], 0, config.PERSONAL_CONTEXT_BYTES
        )
        if value["budget"]["limit_bytes"] != config.PERSONAL_CONTEXT_BYTES:
            raise ChatError("上下文预算无效。")

    @staticmethod
    def _document(item, maximum):
        integer(item["revision"])
        if not isinstance(item["content"], str):
            raise ChatError("个人文档无效。")
        raw = item["content"].encode("utf-8")
        if (
            len(raw) > maximum
            or type(item["size_bytes"]) is not int
            or item["size_bytes"] != len(raw)
            or item["sha256"] != hashlib.sha256(raw).hexdigest()
        ):
            raise ChatError("个人文档校验失败。")

    @io_guard
    def recover(self):
        """Startup/lazy recovery. Locks, not PID/mtime, decide whether a writer lives."""
        from ...platform.identity import Principal

        recovered = 0
        # Reclaim only after acquiring the reservation owner's original lock.
        for record in self._reservations():
            principal = Principal(record["user_id"])
            try:
                lock = (
                    self.lease(self._scope(principal, record["conversation_id"]))
                    if record["conversation_id"]
                    else profile_lock(self.fs, self.prefix, principal.user_id)
                )
                with lock:
                    with self.storage_lock():
                        self.fs.unlink(
                            f"{self.prefix}/reservations/{record['operation_id']}.json",
                            missing_ok=True,
                        )
            except ChatError:
                continue
        for uid in self.fs.listdir(f"{self.prefix}/users"):
            if (
                not uid.isascii()
                or not uid.isdigit()
                or str(int(uid)) != uid
                or not 1 <= int(uid) <= 2147483647
            ):
                continue
            principal = Principal(int(uid))
            root = self._user_dirs(principal)
            for cid in self.fs.listdir(f"{root}/.trash"):
                try:
                    identifier(cid)
                    scope = self._scope(principal, cid)
                    with self.lease(scope):
                        with self.reserve_user(principal, 8192, "recovery"):
                            with self.storage_lock():
                                self.fs.write_json(
                                    f"{root}/deleted/{cid}.json",
                                    {"v": 1, "cid": cid, "deleted_at": utcnow()},
                                )
                        self.fs.remove_tree(f"{root}/.trash/{cid}")
                except (ChatError, OSError):
                    continue
            for cid in self.fs.listdir(f"{root}/conversations"):
                try:
                    scope = self._scope(principal, cid)
                    with self.lease(scope) as lease:
                        self._recover_locked(scope, lease)
                        recovered += 1
                except ChatError:
                    continue
        return {"recovered": recovered}
