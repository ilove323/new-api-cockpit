"""Owner-scoped chat use cases, composed from explicit adapters and local files."""

import os
import time
from uuid import uuid4

from . import attachments, config, schemas, working_context, file_deletions
from .capabilities import Capabilities
from .errors import ChatError
from .ports import SessionVerifier, TokenProvider
from .recovery import TERMINAL
from .runner import ActiveRun, Runner
from .storage import ChatStore
from .user_context import UserContext, validate_credentials


class ChatService:
    def __init__(
        self,
        settings,
        tokens: TokenProvider,
        verifier: SessionVerifier,
        *,
        store=None,
        user_context=None,
        stream_chat=None,
    ):
        self.settings, self.token_provider, self.verifier = settings, tokens, verifier
        self.store = store or ChatStore(settings)
        self.user_context = user_context or UserContext(self.store)
        self.capabilities = Capabilities(settings.capabilities_file)
        self.runner = Runner(
            self.store,
            tokens,
            verifier,
            stream_chat=stream_chat,
            user_context=self.user_context,
        )

    def _scope(self, principal, cid):
        return self.store.open_conversation(principal, cid)

    @staticmethod
    def _token_call(function, *args):
        try:
            return function(*args)
        except ChatError:
            raise
        except ValueError as exc:
            status = getattr(exc, "status", 503)
            if status == 429:
                raise ChatError(
                    "New API 请求过于频繁，请稍后再试。",
                    429,
                    "CHAT_UPSTREAM_RATE_LIMIT",
                    retry_after=getattr(exc, "retry_after", None),
                ) from None
            raise ChatError(
                "无法使用所选本人令牌，请检查状态和权限。"
                if status < 500
                else "New API 暂时无法读取令牌或模型信息，请稍后再试。",
                status,
                "CHAT_TOKEN_FORBIDDEN" if status < 500 else "CHAT_UPSTREAM_ERROR",
            ) from None

    def tokens(self, access):
        return {
            "tokens": self._token_call(self.token_provider.list_tokens, access),
            "token_manager_path": "/keys",
        }

    def models(self, access, token_id):
        rows = self._token_call(self.token_provider.list_models, access, token_id)
        entries = [
            {k: v for k, v in row.items() if k != "policy"}
            for row in self.capabilities.catalog(rows)
        ]
        return {"models": entries, "token_manager_path": "/keys"}

    def list_conversations(self, principal, query):
        return self.store.list_conversations(principal, **query)

    def create_conversation(self, principal, body):
        existed = False
        try:
            self._scope(principal, body["conversation_id"])
            existed = True
        except ChatError as exc:
            if exc.code != "CHAT_NOT_FOUND":
                raise
        value = self.store.create_conversation(
            principal, body["conversation_id"], body["title"]
        )
        return {"conversation": value}, 200 if existed else 201

    def get_conversation(self, principal, cid):
        return self.store.get_conversation(self._scope(principal, cid))

    def rename_conversation(self, principal, cid, body):
        return self.store.rename(
            self._scope(principal, cid), body["title"], body["expected_revision"]
        )

    def set_conversation_skills(self, access, cid, body):
        """Persist this chat's ordered loaded Skills without starting a model."""
        schemas.object_fields(
            body, {"expected_revision", "context_revision", "loaded_skill_ids"}
        )
        schemas.integer(body["expected_revision"], 1)
        schemas.integer(body["context_revision"])
        selected = schemas.id_list(body["loaded_skill_ids"], config.SKILLS_PER_RUN)
        scope = self._scope(access.principal, cid)
        with self.store.lease(scope) as lease:
            meta = self.store.get_conversation(scope, lease=lease)
            if meta["storage_state"] == "corrupt":
                raise ChatError("聊天记录需要修复。", 503, "CHAT_STORAGE_CORRUPT")
            if meta["revision"] != body["expected_revision"]:
                raise ChatError(
                    "聊天已变化，请刷新后再载入 Skills。",
                    409,
                    "CHAT_REVISION_CONFLICT",
                )
            if meta["active_run_id"]:
                raise ChatError("聊天正在运行。", 409, "CHAT_CONVERSATION_BUSY")
            # freeze_context constructs an in-memory value only. Reuse its
            # owner/version/content/budget validation, but never save a run or
            # a snapshot file merely to load Skills.
            preview = self.user_context.freeze_context(
                access.principal, cid, str(uuid4()), body["context_revision"], selected
            )
            for item in [*preview["profiles"], *preview["skills"]]:
                validate_credentials(item["content"], access.credential)
            if meta["loaded_skill_ids"] != selected:
                self.store.append(
                    scope,
                    "conversation.skills.updated",
                    {"skill_ids": selected},
                    lease=lease,
                )
            return self.store.get_conversation(scope, lease=lease)

    def delete_conversation(self, principal, cid, body):
        scope = self.store._scope(principal, cid)
        self.store.delete(scope, body["expected_revision"])

    def events(self, principal, cid, query):
        return self.store.read_events(self._scope(principal, cid), **query)

    def sync(self, principal, cid, query):
        values = dict(query)
        context_revision = values.pop("context_revision", 0)
        schemas.integer(context_revision)
        result = self.store.sync(self._scope(principal, cid), **values)
        result["context"] = (
            self.user_context.read_context(principal)
            if context_revision < result.pop("required_context_revision")
            else None
        )
        return result

    def files(self, principal, cid):
        files = self.store.files(self._scope(principal, cid))
        return {"files": files, "total": len(files)}

    def upload(self, principal, cid, fid, name, data):
        scope = self._scope(principal, cid)
        existed = any(entry["id"] == fid for entry in self.store.files(scope))
        return self.store.upload(scope, name, data, fid), 200 if existed else 201

    def upload_request(self, principal, cid):
        # Check owner before touching the multipart body or taking shared capacity.
        return self.store.upload_request(self._scope(principal, cid))

    def download(self, principal, cid, fid):
        info, data = self.store.read_file(self._scope(principal, cid), fid)
        return data, info

    def delete_upload(self, principal, cid, fid):
        return self.store.delete_upload(self._scope(principal, cid), fid)

    def _history(self, scope, *, lease=None):
        events, after, until = [], 0, None
        while True:
            page = self.store.read_events(
                scope, after_seq=after, until_seq=until, lease=lease
            )
            events.extend(page["events"])
            if not page["has_more"]:
                return events
            after, until = page["next_after_seq"], page["until_seq"]

    def accept_run(self, access, cid, body):
        body = schemas.parse_run(body)
        scope = self._scope(access.principal, cid)
        existing = self.store.find_request(scope, body["client_request_id"], body)
        if existing is not None:
            return {
                "replayed": True,
                "run_id": existing["id"],
                "status": existing["status"],
                "last_seq": existing["last_seq"],
            }
        lease = self.store.lease(scope, run=True)
        try:
            # The locked lookup is authoritative: a prior run may have finished
            # between the optimistic lookup and acquisition of this lease.
            existing = self.store.find_request(
                scope, body["client_request_id"], body, lease=lease
            )
            if existing is not None:
                lease.close()
                return {
                    "replayed": True,
                    "run_id": existing["id"],
                    "status": existing["status"],
                    "last_seq": existing["last_seq"],
                }
            meta = self.store.get_conversation(scope, lease=lease)
            if meta["storage_state"] == "corrupt":
                raise ChatError("聊天记录需要修复。", 503, "CHAT_STORAGE_CORRUPT")
            if meta["revision"] != body["expected_revision"]:
                raise ChatError(
                    "聊天已变化，请刷新后再发送。", 409, "CHAT_REVISION_CONFLICT"
                )
            rows = self._token_call(
                self.token_provider.list_models, access, body["token_id"]
            )
            capability = self.capabilities.select(
                rows, body["model"], body["capability_revision"], body["mode"]
            )
            lease.file_deletion_signature = file_deletions.signature(self.store, scope)
            files = {
                entry["id"]: entry for entry in self.store.files(scope, lease=lease)
            }
            for fid in body["attachment_ids"]:
                if fid not in files:
                    raise ChatError("附件不存在。", 404, "CHAT_NOT_FOUND")
                if files[fid].get("deleted_at"):
                    raise ChatError(
                        "附件已删除，请取消引用。", 410, "CHAT_FILE_DELETED"
                    )
            rid = str(uuid4())
            snapshot = self.user_context.freeze_context(
                access.principal, cid, rid, body["context_revision"], body["skill_ids"]
            )
            for item in [*snapshot["profiles"], *snapshot["skills"]]:
                validate_credentials(item["content"], access.credential)
            history = self._history(scope, lease=lease)
            attachment_texts = attachments.read_texts(
                self.store,
                scope,
                files,
                body,
                history,
                capability["policy"],
                lease=lease,
            )
            previous = next(
                (
                    event
                    for event in reversed(history)
                    if event["type"] == "run.accepted"
                ),
                None,
            )
            if (
                previous
                and previous["data"].get("options", {}).get("candidate_count", 1) > 1
            ):
                prior = self.store.get_run(scope, previous["run_id"], lease=lease)
                if (
                    prior["status"] == "completed"
                    and prior.get("selected_candidate") is None
                ):
                    raise ChatError(
                        "请先选择上一轮的候选回答，再继续聊天。",
                        409,
                        "CHAT_CANDIDATE_REQUIRED",
                    )
            request_context = {
                **body,
                "protocol": capability["protocol"],
                "attachments": [files[fid] for fid in body["attachment_ids"]],
                "attachment_texts": attachment_texts,
                "attachment_mode": "server-text-v1",
            }
            saved, saved_ref = None, None
            binding = {
                key: request_context[key]
                for key in ("protocol", "model", "token_id", "capability_revision")
            }
            if body.get("context_strategy") == "adaptive":
                latest_delete = max(
                    (f.get("deleted_at", "") for f in files.values()), default=""
                )
                accepted_at = {
                    e["run_id"]: e["at"] for e in history if e["type"] == "run.accepted"
                }
                for event in reversed(history):
                    if event["type"] != "context.compacted":
                        continue
                    # Includes a run already in flight when deletion occurred.
                    if (
                        latest_delete
                        and accepted_at.get(event["run_id"], "") <= latest_delete
                    ):
                        continue
                    candidate = self.store.read_working_context(
                        scope, event["data"]["checkpoint"], lease=lease
                    )
                    if candidate.get("binding") == binding and not candidate.get(
                        "active_only", False
                    ):
                        saved, saved_ref = candidate, event["data"]["checkpoint"]
                        break
            planned = working_context.plan(
                history,
                snapshot,
                request_context,
                capability["policy"],
                checkpoint=saved,
            )
            planned["checkpoint_reference"] = saved_ref
            messages, included = planned["messages"] or [], planned["included_run_ids"]
            self.store.reserve(scope, config.RUN_STORAGE_RESERVE_BYTES, lease=lease)
            reference = self.store.write_context(scope, rid, snapshot, lease=lease)
            accepted_at = time.monotonic()
            initial = [
                self.store.append(
                    scope,
                    "run.accepted",
                    {
                        "client_request_id": body["client_request_id"],
                        "message_id": str(uuid4()),
                        "text": body["text"],
                        "attachment_ids": body["attachment_ids"],
                        "token_id": body["token_id"],
                        "model": body["model"],
                        "mode": body["mode"],
                        "capability_revision": body["capability_revision"],
                        "context_snapshot": reference,
                        "context_from_run_ids": included,
                        "protocol": capability["protocol"],
                        **(
                            {"attachment_mode": "server-text-v1"}
                            if body["attachment_ids"]
                            else {}
                        ),
                        **(
                            {"options": schemas.run_options(body)}
                            if schemas.run_options(body)
                            else {}
                        ),
                    },
                    run_id=rid,
                    lease=lease,
                )
            ]
            if meta["title"] == "新聊天":
                auto_title = (
                    " ".join(body["text"].split())[: config.TITLE_CHARS] or "新聊天"
                )
                initial.append(
                    self.store.append(
                        scope,
                        "conversation.renamed",
                        {"title": auto_title},
                        lease=lease,
                    )
                )
            active = ActiveRun(
                scope,
                lease,
                rid,
                dict(body),
                access,
                messages,
                capability["policy"],
                initial,
                started=accepted_at,
                context_plan=planned,
                history_events=history,
            )
            return self.runner.start(active)
        except BaseException:
            lease.close()
            raise

    def stream(self, accepted):
        return self.runner.stream(accepted)

    def get_run(self, principal, cid, rid):
        return self.store.get_run(self._scope(principal, cid), rid)

    def select_candidate(self, principal, cid, rid, body):
        scope = self._scope(principal, cid)
        with self.store.lease(scope) as lease:
            meta = self.store.get_conversation(scope, lease=lease)
            if meta["revision"] != body["expected_revision"]:
                raise ChatError(
                    "聊天已变化，请刷新后再选择。", 409, "CHAT_REVISION_CONFLICT"
                )
            state = self.store.get_run(scope, rid, lease=lease)
            if state["status"] != "completed" or state.get("candidate_count", 1) <= 1:
                raise ChatError(
                    "此轮不能选择候选回答。", 409, "CHAT_CANDIDATE_CONFLICT"
                )
            # Projection also checks latest-run/no-active-run and candidate existence.
            self.store.append(
                scope,
                "candidate.selected",
                {"candidate_index": body["candidate_index"]},
                run_id=rid,
                lease=lease,
            )
            return {
                "run": self.store.get_run(scope, rid, lease=lease),
                "conversation": self.store.get_conversation(scope, lease=lease),
            }

    def undo_memory(self, access, cid, rid, body):
        from .auto_memory import undo_update

        scope = self._scope(access.principal, cid)
        with self.store.lease(scope) as lease:
            meta = self.store.get_conversation(scope, lease=lease)
            if meta["revision"] != body["expected_revision"]:
                raise ChatError(
                    "聊天已变化，请刷新后重试。", 409, "CHAT_REVISION_CONFLICT"
                )
            state = self.store.get_run(scope, rid, lease=lease)
            if (
                state["status"] not in TERMINAL
                or not state.get("memory_updated")
                or state.get("memory_reverted")
            ):
                raise ChatError(
                    "此自动记忆变更不能撤销。", 409, "CHAT_MEMORY_UNDO_CONFLICT"
                )
            audit = next(
                (
                    event["data"]["audit"]
                    for event in reversed(self._history(scope, lease=lease))
                    if event["run_id"] == rid and event["type"] == "memory.updated"
                ),
                None,
            )
            if audit is None:
                raise ChatError("自动记忆审计缺失。", 503, "CHAT_STORAGE_CORRUPT")
            result = undo_update(
                self.user_context,
                access.principal,
                audit,
                body["context_revision"],
                credential=access.credential,
            )
            self.store.append(
                scope,
                "memory.reverted",
                {
                    "after_sha256": result["profile"]["sha256"],
                    "context_revision": result["context_revision"],
                    "memory_revision": result["profile"]["revision"],
                },
                run_id=rid,
                lease=lease,
            )
            return {
                "context": self.user_context.read_context(access.principal),
                "conversation": self.store.get_conversation(scope, lease=lease),
            }

    def cancel_run(self, principal, cid, rid):
        scope = self._scope(principal, cid)
        result = self.store.cancel(scope, rid)
        self.runner.cancel_local(scope, rid)
        return result, 200 if result["status"] in TERMINAL else 202

    def health(self):
        self.capabilities.load()
        ready = (
            not self.runner.stopping
            and self.store.fs.free_bytes() >= config.DISK_FREE_MIN_BYTES
        )
        try:
            probe = f"{self.store.prefix}/.health-{os.getpid()}-{uuid4()}"
            self.store.fs.atomic_write(probe, b"1", exclusive=True)
            self.store.fs.unlink(probe)
        except (OSError, ChatError):
            ready = False
        return {"ready": ready, "role": "chat"}

    def request_stop(self):
        return self.runner.request_stop()

    def shutdown(self, timeout=20):
        self.runner.shutdown(timeout)
