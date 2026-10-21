"""User-owned versioned personal settings and immutable per-run context DTOs.

The host injects a ChatStore. No KEY, HTTP request or RequestAccess is accepted.
Settings share its safe filesystem, profile lock and quota transaction. The run
service owns conversation locking and publishing snapshots before run.accepted.
"""

from contextlib import contextmanager
from datetime import datetime
import errno
from functools import wraps
import hashlib
import json
import re
from uuid import UUID, uuid4

from ...platform.identity import Principal
from . import config
from .errors import ChatError
from .fs import canonical_json, utcnow
from .locks import profile_lock
from .skills import normalize_text, parse_skill

PROFILE_NAMES = ("MEMORY.md", "SOUL.md", "IDENTITY.md")
POLICY_VERSION = "personal-context-v1"
MAX_REVISION = 9007199254740991
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_DOCUMENT_KEYS = {"name", "revision", "content", "sha256", "size_bytes", "updated_at"}
_PROFILE_META_KEYS = _DOCUMENT_KEYS - {"name", "content"}
_SKILL_KEYS = {
    "id",
    "name",
    "description",
    "revision",
    "sha256",
    "size_bytes",
    "created_at",
    "updated_at",
}
_SNAPSHOT_KEYS = {
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
}
_CREDENTIAL_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?i)\b(?:authorization|cookie|x-cockpit-session)\s*[:=]\s*\S+",
        r"\bsk-[A-Za-z0-9_-]{16,}\b",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    )
)


def validate_credentials(content, credential=None):
    """Reject recognized secrets; callers may supply one known current credential.

    This is local validation, not comprehensive DLP. Neither values nor matches
    are returned in errors. Call at HTTP acceptance while credentials are live.
    """
    if not isinstance(content, str) or (
        credential is not None and not isinstance(credential, str)
    ):
        raise ChatError()
    candidates = [content]
    if credential:
        # Description is decoded into metadata, so JSON escapes must not hide a
        # known credential from the request-boundary check. No resources load.
        try:
            candidates.append(parse_skill(content).description)
        except ChatError:
            pass
    if any(
        (credential and credential in candidate)
        or any(pattern.search(candidate) for pattern in _CREDENTIAL_PATTERNS)
        for candidate in candidates
    ):
        raise ChatError(
            "个人上下文不能包含认证凭据。", 422, "CHAT_CONTEXT_CREDENTIAL_FORBIDDEN"
        )


def _canonical(value):
    return canonical_json(value) + b"\n"


def _corrupt():
    return ChatError("个人上下文数据损坏。", 503, "CHAT_CONTEXT_CORRUPT")


def _not_found():
    return ChatError("内容不存在。", 404, "CHAT_NOT_FOUND")


def _integer(value):
    return type(value) is int and 0 <= value <= MAX_REVISION


def _identifier(value):
    try:
        result = UUID(value) if isinstance(value, str) else None
        if result is None or result.version != 4 or str(result) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ChatError("标识格式无效。") from None
    return value


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _keys(value, keys):
    return isinstance(value, dict) and set(value) == keys


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def bad_constant(value):
        raise ValueError()

    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=bad_constant
        )
    except (ValueError, UnicodeError, RecursionError):
        raise _corrupt() from None


def _io_guard(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OSError as exc:
            full = exc.errno in {errno.ENOSPC, errno.EDQUOT}
            raise ChatError(
                "存储空间不足。" if full else "存储暂不可用。",
                507 if full else 503,
                "CHAT_STORAGE_FULL" if full else "CHAT_STORAGE_UNAVAILABLE",
            ) from None

    return guarded


class _FrozenDict(dict):
    def _deny(self, *args, **kwargs):
        raise TypeError("Context snapshot is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = (
        __ior__
    ) = _deny

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


class _FrozenList(list):
    def _deny(self, *args, **kwargs):
        raise TypeError("Context snapshot is immutable")

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = (
        reverse
    ) = sort = __iadd__ = __imul__ = _deny

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def _freeze(value):
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    return value


class UserContext:
    """Versioned settings, sharing the store's instance-rooted SafeFS and locks."""

    def __init__(self, store, *, clock=None, render_personal_block=None):
        self.store = store
        self.fs = store.fs
        self.clock = clock or utcnow
        self._renderer = render_personal_block

    @staticmethod
    def _principal(principal):
        if not isinstance(principal, Principal) or principal.auth_kind != "session":
            raise ChatError("个人上下文需要登录会话。", 403, "CHAT_SESSION_REQUIRED")
        return principal.user_id

    def _root(self, principal):
        return f"{self.store.prefix}/users/{self._principal(principal)}"

    @staticmethod
    def _profile_limits():
        return {
            "MEMORY.md": config.MEMORY_BYTES,
            "SOUL.md": config.SOUL_BYTES,
            "IDENTITY.md": config.IDENTITY_BYTES,
        }

    @classmethod
    def _limits(cls):
        return {
            "profile_bytes": cls._profile_limits(),
            "skill_file_bytes": config.SKILL_FILE_BYTES,
            "skill_name_chars": config.SKILL_NAME_CHARS,
            "skill_description_chars": config.SKILL_DESCRIPTION_CHARS,
            "skills_per_user": config.SKILLS_PER_USER,
            "skills_per_run": config.SKILLS_PER_RUN,
            "personal_context_bytes": config.PERSONAL_CONTEXT_BYTES,
            "settings_request_bytes": config.SETTINGS_REQUEST_BYTES,
        }

    def _lock(self, principal):
        try:
            return profile_lock(self.fs, self.store.prefix, self._principal(principal))
        except ChatError as exc:
            if exc.code == "CHAT_CONTEXT_BUSY":
                raise ChatError(
                    "个人设置正在使用，请稍后手动重试。", 409, "CHAT_PROFILE_BUSY"
                ) from None
            raise

    def _read_bytes(self, path, limit):
        try:
            if self.fs.stat(path).st_size > limit:
                raise _corrupt()
            raw = self.fs.read_bytes(path, max_bytes=limit)
        except FileNotFoundError:
            raise _corrupt() from None
        if not isinstance(raw, bytes) or len(raw) > limit:
            raise _corrupt()
        return raw

    @staticmethod
    def _default():
        return {
            "v": 1,
            "context_revision": 0,
            "updated_at": None,
            "profiles": {
                name: {
                    "revision": 0,
                    "sha256": EMPTY_SHA256,
                    "size_bytes": 0,
                    "updated_at": None,
                }
                for name in PROFILE_NAMES
            },
            "skills": {},
        }

    @staticmethod
    def _profile_path(root, name, revision):
        return f"{root}/profiles/{name[:-3]}/versions/{revision}.md"

    @staticmethod
    def _skill_path(root, sid, revision):
        return f"{root}/skills/{sid}/versions/{revision}/SKILL.md"

    @staticmethod
    def _meta_valid(value, limit, context_revision):
        return (
            _keys(value, _PROFILE_META_KEYS)
            and _integer(value["revision"])
            and value["revision"] <= context_revision
            and type(value["size_bytes"]) is int
            and 0 <= value["size_bytes"] <= limit
            and isinstance(value["sha256"], str)
            and bool(re.fullmatch(r"[a-f0-9]{64}", value["sha256"]))
            and (
                (
                    value["revision"] == 0
                    and value["size_bytes"] == 0
                    and value["sha256"] == EMPTY_SHA256
                    and value["updated_at"] is None
                )
                or (value["revision"] > 0 and _timestamp(value["updated_at"]))
            )
        )

    @staticmethod
    def _summary_valid(summary, context_revision):
        if not _keys(summary, _SKILL_KEYS):
            return False
        try:
            _identifier(summary["id"])
        except ChatError:
            return False
        return (
            _integer(summary["revision"])
            and 1 <= summary["revision"] <= context_revision
            and isinstance(summary["name"], str)
            and len(summary["name"]) <= config.SKILL_NAME_CHARS
            and bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", summary["name"]))
            and isinstance(summary["description"], str)
            and 1 <= len(summary["description"]) <= config.SKILL_DESCRIPTION_CHARS
            and bool(summary["description"].strip())
            and not any(
                ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF
                for c in summary["description"]
            )
            and type(summary["size_bytes"]) is int
            and 0 < summary["size_bytes"] <= config.SKILL_FILE_BYTES
            and isinstance(summary["sha256"], str)
            and bool(re.fullmatch(r"[a-f0-9]{64}", summary["sha256"]))
            and _timestamp(summary["created_at"])
            and _timestamp(summary["updated_at"])
        )

    def _manifest(self, principal, *, verify_resources=True):
        root = self._root(principal)
        path = f"{root}/context-manifest.json"
        if not self.fs.exists(path):
            for directory in (
                "profiles",
                "skills",
                ".context-tmp",
                ".context-quarantine",
            ):
                child = f"{root}/{directory}"
                if self.fs.exists(child) and self.fs.listdir(child):
                    raise _corrupt()
            return self._default()
        manifest = _decode(self._read_bytes(path, config.CONTEXT_MANIFEST_BYTES))
        if not (
            _keys(
                manifest, {"v", "context_revision", "updated_at", "profiles", "skills"}
            )
            and type(manifest["v"]) is int
            and manifest["v"] == 1
            and _integer(manifest["context_revision"])
            and (
                manifest["updated_at"] is None
                if manifest["context_revision"] == 0
                else _timestamp(manifest["updated_at"])
            )
            and _keys(manifest["profiles"], set(PROFILE_NAMES))
            and isinstance(manifest["skills"], dict)
            and len(manifest["skills"]) <= config.SKILLS_PER_USER
        ):
            raise _corrupt()
        for name, meta in manifest["profiles"].items():
            if not self._meta_valid(
                meta, self._profile_limits()[name], manifest["context_revision"]
            ):
                raise _corrupt()
            if meta["revision"] and not self.fs.exists(
                self._profile_path(root, name, meta["revision"])
            ):
                raise _corrupt()
        names = set()
        for sid, summary in manifest["skills"].items():
            if (
                not self._summary_valid(summary, manifest["context_revision"])
                or summary["id"] != sid
                or summary["name"] in names
            ):
                raise _corrupt()
            names.add(summary["name"])
            if not self.fs.exists(self._skill_path(root, sid, summary["revision"])):
                raise _corrupt()
        if verify_resources:
            self._profiles(root, manifest)
            for summary in manifest["skills"].values():
                self._skill(root, summary)
        return manifest

    @staticmethod
    def _content(raw, meta):
        if (
            len(raw) != meta["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != meta["sha256"]
        ):
            raise _corrupt()
        try:
            content = raw.decode("utf-8")
            if normalize_text(content, strip_bom=False) != content:
                raise ValueError()
        except (UnicodeError, ChatError, ValueError):
            raise _corrupt() from None
        return content

    def _profiles(self, root, manifest):
        result = []
        for name in PROFILE_NAMES:
            meta = manifest["profiles"][name]
            raw = (
                self._read_bytes(
                    self._profile_path(root, name, meta["revision"]),
                    self._profile_limits()[name],
                )
                if meta["revision"]
                else b""
            )
            result.append({"name": name, **meta, "content": self._content(raw, meta)})
        return result

    def _skill(self, root, summary):
        raw = self._read_bytes(
            self._skill_path(root, summary["id"], summary["revision"]),
            config.SKILL_FILE_BYTES,
        )
        content = self._content(raw, summary)
        try:
            parsed = parse_skill(content)
        except ChatError:
            raise _corrupt() from None
        if (
            parsed.name != summary["name"]
            or parsed.description != summary["description"]
        ):
            raise _corrupt()
        return {**summary, "content": content}

    @staticmethod
    def _summaries(manifest):
        return sorted(
            manifest["skills"].values(), key=lambda item: (item["name"], item["id"])
        )

    @staticmethod
    def _check_revision(manifest, revision):
        if not _integer(revision):
            raise ChatError("版本参数无效。")
        if revision != manifest["context_revision"]:
            raise ChatError(
                "个人设置已更新，请重新查看。",
                409,
                "CHAT_CONTEXT_CHANGED",
                details={"current_context_revision": manifest["context_revision"]},
            )

    @staticmethod
    def _check_target(revision, expected_revision, code):
        if not _integer(expected_revision):
            raise ChatError("版本参数无效。")
        if expected_revision != revision:
            raise ChatError("内容版本冲突。", 409, code)

    @staticmethod
    def _advance(revision):
        if revision == MAX_REVISION:
            raise ChatError(
                "版本已达上限，无法继续保存。", 503, "CHAT_STORAGE_UNAVAILABLE"
            )
        return revision + 1

    def _time(self):
        value = self.clock()
        if not _timestamp(value):
            raise ChatError("服务端时钟无效。", 503, "CHAT_CONFIG_INVALID")
        return value

    @_io_guard
    def read_context(self, principal):
        with self._lock(principal):
            manifest = self._manifest(principal)
            return {
                "schema_version": 1,
                "context_revision": manifest["context_revision"],
                "updated_at": manifest["updated_at"],
                "profiles": self._profiles(self._root(principal), manifest),
                "skills": self._summaries(manifest),
                "limits": self._limits(),
            }

    @_io_guard
    def list_skills(self, principal):
        with self._lock(principal):
            manifest = self._manifest(principal)
            summaries = self._summaries(manifest)
            return {
                "context_revision": manifest["context_revision"],
                "skills": summaries,
                "total": len(summaries),
            }

    @_io_guard
    def get_skill(self, principal, sid, context_revision=None):
        _identifier(sid)
        with self._lock(principal):
            manifest = self._manifest(principal)
            summary = manifest["skills"].get(sid)
            if summary is None:
                raise _not_found()
            if context_revision is not None:
                self._check_revision(manifest, context_revision)
            return {
                "context_revision": manifest["context_revision"],
                "skill": self._skill(self._root(principal), summary),
            }

    @contextmanager
    def _transaction(self, principal, required_bytes):
        """Store-provided profile→storage quota transaction; never a private ledger."""
        with self.store.profile_transaction(principal, required_bytes):
            yield

    def _commit(self, principal, old_manifest, manifest, new_path=None, content=None):
        root = self._root(principal)
        raw_manifest = _canonical(manifest)
        if len(raw_manifest) > config.CONTEXT_MANIFEST_BYTES:
            raise ChatError("个人设置超出存储限制。", 507, "CHAT_STORAGE_LIMIT")
        required = (
            len(raw_manifest) + (len(content) if content is not None else 0) + 16384
        )
        with self._transaction(principal, required):
            if new_path is not None:
                operation = str(uuid4())
                temp = f"{root}/.context-tmp/{operation}/version"
                self.fs.mkdir(temp.rsplit("/", 1)[0], parents=True)
                self.fs.atomic_write(temp, content, exclusive=True)
                if (
                    self._read_bytes(
                        temp, max(config.SKILL_FILE_BYTES, config.MEMORY_BYTES)
                    )
                    != content
                ):
                    raise _corrupt()
                self.fs.mkdir(new_path.rsplit("/", 1)[0], parents=True)
                if self.fs.exists(new_path):
                    quarantine = f"{root}/.context-quarantine/{uuid4()}"
                    self.fs.mkdir(quarantine.rsplit("/", 1)[0], parents=True)
                    self.fs.rename(new_path, quarantine)
                self.fs.rename(temp, new_path)
            self.fs.atomic_write(f"{root}/context-manifest.json", raw_manifest)
            if new_path is not None:
                try:
                    self.fs.remove_tree(temp.rsplit("/", 1)[0])
                except (OSError, ChatError):
                    pass
            # The manifest is the sole commit point. Cleanup failure must never
            # report a rollback or resurrect a removed Skill. Disk accounting is
            # recomputed by the store, including leftover files and directories.
            self._cleanup(root, old_manifest, manifest)

    def _cleanup(self, root, old, current):
        paths = []
        for name, meta in old["profiles"].items():
            if (
                meta["revision"]
                and meta["revision"] != current["profiles"][name]["revision"]
            ):
                paths.append(self._profile_path(root, name, meta["revision"]))
        for sid, meta in old["skills"].items():
            if (
                sid not in current["skills"]
                or meta["revision"] != current["skills"][sid]["revision"]
            ):
                paths.append(self._skill_path(root, sid, meta["revision"]))
        for path in paths:
            try:
                self.fs.unlink(path)
            except (OSError, ChatError):
                pass

    @_io_guard
    def update_profile(
        self, principal, name, context_revision, expected_revision, content
    ):
        self._principal(principal)
        if name not in PROFILE_NAMES:
            raise ChatError("个人文档名称无效。")
        content = normalize_text(content)
        raw = content.encode("utf-8")
        if len(raw) > self._profile_limits()[name]:
            raise ChatError(
                "个人文档超出大小限制。",
                413,
                "CHAT_PROFILE_TOO_LARGE",
                details={"limit_bytes": self._profile_limits()[name]},
            )
        validate_credentials(content)
        with self._lock(principal):
            old = self._manifest(principal)
            self._check_revision(old, context_revision)
            meta = old["profiles"][name]
            self._check_target(
                meta["revision"], expected_revision, "CHAT_PROFILE_REVISION_CONFLICT"
            )
            profiles = self._profiles(self._root(principal), old)
            previous = next(item for item in profiles if item["name"] == name)
            if previous["content"] == content:
                return {
                    "context_revision": old["context_revision"],
                    "changed": False,
                    "profile": previous,
                }
            manifest = _decode(_canonical(old))
            now = self._time()
            new_meta = {
                "revision": self._advance(meta["revision"]),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "updated_at": now,
            }
            manifest["profiles"][name] = new_meta
            manifest.update(
                context_revision=self._advance(old["context_revision"]), updated_at=now
            )
            self._commit(
                principal,
                old,
                manifest,
                self._profile_path(self._root(principal), name, new_meta["revision"]),
                raw,
            )
            return {
                "context_revision": manifest["context_revision"],
                "changed": True,
                "profile": {"name": name, **new_meta, "content": content},
            }

    @_io_guard
    def create_skill(self, principal, context_revision, content):
        self._principal(principal)
        parsed = parse_skill(content)
        validate_credentials(parsed.normalized_content)
        validate_credentials(parsed.description)
        with self._lock(principal):
            old = self._manifest(principal)
            self._check_revision(old, context_revision)
            if len(old["skills"]) >= config.SKILLS_PER_USER:
                raise ChatError("Skill 数量已达上限。", 409, "CHAT_SKILL_LIMIT")
            return self._save_skill(principal, old, parsed)

    @_io_guard
    def update_skill(
        self, principal, sid, context_revision, expected_revision, content
    ):
        _identifier(sid)
        self._principal(principal)
        parsed = parse_skill(content)
        validate_credentials(parsed.normalized_content)
        validate_credentials(parsed.description)
        with self._lock(principal):
            old = self._manifest(principal)
            summary = old["skills"].get(sid)
            if summary is None:
                raise _not_found()
            self._check_revision(old, context_revision)
            self._check_target(
                summary["revision"], expected_revision, "CHAT_SKILL_REVISION_CONFLICT"
            )
            previous = self._skill(self._root(principal), summary)
            if previous["content"] == parsed.normalized_content:
                return {
                    "context_revision": old["context_revision"],
                    "changed": False,
                    "skill": previous,
                }
            return self._save_skill(principal, old, parsed, summary)

    def _save_skill(self, principal, old, parsed, previous=None):
        sid = previous["id"] if previous else str(uuid4())
        if any(
            item["name"] == parsed.name and item["id"] != sid
            for item in old["skills"].values()
        ):
            raise ChatError("Skill 名称已存在。", 409, "CHAT_SKILL_NAME_CONFLICT")
        now = self._time()
        summary = {
            "id": sid,
            "name": parsed.name,
            "description": parsed.description,
            "revision": self._advance(previous["revision"]) if previous else 1,
            "sha256": parsed.sha256,
            "size_bytes": parsed.size_bytes,
            "created_at": previous["created_at"] if previous else now,
            "updated_at": now,
        }
        manifest = _decode(_canonical(old))
        manifest["skills"][sid] = summary
        manifest.update(
            context_revision=self._advance(old["context_revision"]), updated_at=now
        )
        self._commit(
            principal,
            old,
            manifest,
            self._skill_path(self._root(principal), sid, summary["revision"]),
            parsed.normalized_content.encode("utf-8"),
        )
        return {
            "context_revision": manifest["context_revision"],
            "changed": True,
            "skill": {**summary, "content": parsed.normalized_content},
        }

    @_io_guard
    def delete_skill(self, principal, sid, context_revision, expected_revision):
        _identifier(sid)
        with self._lock(principal):
            old = self._manifest(principal)
            summary = old["skills"].get(sid)
            if summary is None:
                raise _not_found()
            self._check_revision(old, context_revision)
            self._check_target(
                summary["revision"], expected_revision, "CHAT_SKILL_REVISION_CONFLICT"
            )
            manifest = _decode(_canonical(old))
            del manifest["skills"][sid]
            manifest.update(
                context_revision=self._advance(old["context_revision"]),
                updated_at=self._time(),
            )
            self._commit(principal, old, manifest)
            return {
                "context_revision": manifest["context_revision"],
                "deleted_id": sid,
                "deleted": True,
            }

    def _render(self, snapshot):
        renderer = self._renderer
        if renderer is None:
            from .context import render_personal_block

            renderer = render_personal_block
        return renderer(snapshot)

    @staticmethod
    def _skill_ids(skill_ids):
        if not isinstance(skill_ids, list) or len(skill_ids) > config.SKILLS_PER_RUN:
            raise ChatError("本轮 Skill 列表无效。")
        result = [_identifier(sid) for sid in skill_ids]
        if len(set(result)) != len(result):
            raise ChatError("本轮 Skill 列表不能重复。")
        return result

    @_io_guard
    def freeze_context(self, principal, cid, rid, context_revision, skill_ids):
        _identifier(cid)
        _identifier(rid)
        self._principal(principal)
        selected = self._skill_ids(skill_ids)
        self.store.open_conversation(principal, cid)
        with self._lock(principal):
            # Freeze never opens unselected Skill bodies. Current-settings reads
            # and mutations validate the complete library; the run copies only
            # explicitly selected entries from this same locked manifest.
            manifest = self._manifest(principal, verify_resources=False)
            # Resolve ownership before disclosing this user's current revision.
            if any(sid not in manifest["skills"] for sid in selected):
                raise _not_found()
            self._check_revision(manifest, context_revision)
            root = self._root(principal)
            profiles = self._profiles(root, manifest)
            skills = [self._skill(root, manifest["skills"][sid]) for sid in selected]
            for document in profiles + skills:
                validate_credentials(document["content"])
                if "description" in document:
                    validate_credentials(document["description"])
            snapshot = {
                "v": 1,
                "conversation_id": cid,
                "run_id": rid,
                "captured_at": self._time(),
                "context_revision": manifest["context_revision"],
                "policy_version": POLICY_VERSION,
                "profiles": profiles,
                "skills": skills,
                "skill_ids": list(selected),
            }
            block_bytes = len(self._render(snapshot).encode("utf-8"))
            if block_bytes > config.PERSONAL_CONTEXT_BYTES:
                raise ChatError(
                    "本轮个人上下文超出大小限制。",
                    413,
                    "CHAT_CONTEXT_TOO_LARGE",
                    details={"limit_bytes": config.PERSONAL_CONTEXT_BYTES},
                )
            snapshot["budget"] = {
                "personal_block_bytes": block_bytes,
                "limit_bytes": config.PERSONAL_CONTEXT_BYTES,
            }
            if len(_canonical(snapshot)) > config.CONTEXT_SNAPSHOT_BYTES:
                raise ChatError(
                    "本轮个人上下文快照超出大小限制。",
                    413,
                    "CHAT_CONTEXT_TOO_LARGE",
                    details={"limit_bytes": config.CONTEXT_SNAPSHOT_BYTES},
                )
            return _freeze(snapshot)

    def _validate_snapshot(self, snapshot, cid, rid, reference):
        if not (
            _keys(snapshot, _SNAPSHOT_KEYS)
            and type(snapshot["v"]) is int
            and snapshot["v"] == 1
            and snapshot["conversation_id"] == cid
            and snapshot["run_id"] == rid
            and _timestamp(snapshot["captured_at"])
            and _integer(snapshot["context_revision"])
            and snapshot["context_revision"] == reference["context_revision"]
            and snapshot["policy_version"] == POLICY_VERSION
            and isinstance(snapshot["profiles"], list)
            and len(snapshot["profiles"]) == 3
            and isinstance(snapshot["skills"], list)
            and len(snapshot["skills"]) <= config.SKILLS_PER_RUN
            and snapshot["skill_ids"] == reference["skill_ids"]
        ):
            raise _corrupt()
        for name, document in zip(PROFILE_NAMES, snapshot["profiles"]):
            if not _keys(document, _DOCUMENT_KEYS) or document["name"] != name:
                raise _corrupt()
            meta = {key: document[key] for key in _PROFILE_META_KEYS}
            if not self._meta_valid(
                meta, self._profile_limits()[name], snapshot["context_revision"]
            ) or not isinstance(document["content"], str):
                raise _corrupt()
            try:
                self._content(document["content"].encode("utf-8"), meta)
            except UnicodeError:
                raise _corrupt() from None
        actual_ids = []
        for document in snapshot["skills"]:
            if not _keys(document, _SKILL_KEYS | {"content"}) or not isinstance(
                document["content"], str
            ):
                raise _corrupt()
            summary = {key: document[key] for key in _SKILL_KEYS}
            if not self._summary_valid(summary, snapshot["context_revision"]):
                raise _corrupt()
            try:
                parsed = parse_skill(document["content"])
            except ChatError:
                raise _corrupt() from None
            if (
                any(
                    getattr(parsed, key) != summary[key]
                    for key in ("name", "description", "sha256", "size_bytes")
                )
                or parsed.normalized_content != document["content"]
            ):
                raise _corrupt()
            actual_ids.append(document["id"])
        if actual_ids != snapshot["skill_ids"] or len(set(actual_ids)) != len(
            actual_ids
        ):
            raise _corrupt()
        budget = snapshot["budget"]
        if not (
            _keys(budget, {"personal_block_bytes", "limit_bytes"})
            and type(budget["personal_block_bytes"]) is int
            and type(budget["limit_bytes"]) is int
            and budget["limit_bytes"] == config.PERSONAL_CONTEXT_BYTES
            and 0 <= budget["personal_block_bytes"] <= budget["limit_bytes"]
            and len(self._render(snapshot).encode("utf-8"))
            == budget["personal_block_bytes"]
        ):
            raise _corrupt()

    def _accepted_reference(self, scope, rid):
        after, until = 0, None
        while True:
            page = self.store.read_events(scope, after_seq=after, until_seq=until)
            until = page["until_seq"]
            for event in page["events"]:
                if event["type"] == "run.accepted" and event["run_id"] == rid:
                    return event["data"]["context_snapshot"]
            if not page["has_more"]:
                raise _not_found()
            next_after = page["next_after_seq"]
            if next_after <= after:
                raise _corrupt()
            after = next_after

    @_io_guard
    def get_run_context(self, principal, cid, rid):
        _identifier(cid)
        _identifier(rid)
        self._principal(principal)
        scope = self.store.open_conversation(principal, cid)
        try:
            snapshot = self.store.read_context(scope, rid)
        except ChatError as exc:
            if exc.code in {
                "CHAT_STORAGE_CORRUPT",
                "CHAT_CONTEXT_INVALID",
                "CHAT_INVALID_REQUEST",
            }:
                raise _corrupt() from None
            raise
        reference = self._accepted_reference(scope, rid)
        if not (
            _keys(reference, {"file", "sha256", "context_revision", "skill_ids"})
            and reference["file"] == f"contexts/{rid}.json"
            and _integer(reference["context_revision"])
            and isinstance(reference["sha256"], str)
            and re.fullmatch(r"[a-f0-9]{64}", reference["sha256"])
        ):
            raise _corrupt()
        try:
            self._skill_ids(reference["skill_ids"])
        except ChatError:
            raise _corrupt() from None
        raw = self._read_bytes(
            f"{scope.path}/contexts/{rid}.json", config.CONTEXT_SNAPSHOT_BYTES
        )
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise _corrupt()
        try:
            # Strict parsing rejects duplicate keys, non-finite numbers and
            # noncanonical encodings even if the stored reference hashes them.
            decoded = _decode(raw)
            if decoded != snapshot or _canonical(decoded) != raw:
                raise _corrupt()
            self._validate_snapshot(snapshot, cid, rid, reference)
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            if isinstance(exc, ChatError):
                raise _corrupt() from None
            raise _corrupt() from None
        return _freeze(snapshot)
