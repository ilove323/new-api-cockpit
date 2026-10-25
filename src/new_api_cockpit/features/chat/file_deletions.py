"""Immutable, owner/chat-scoped deletion controls, like run cancellation.

Deletion controls are authoritative independently of the append-only history.
Publish intent before unlinking a blob; recovery finishes an interrupted purge.
No model-call lease is needed, so a user can delete during an active response.
"""

import re

from .errors import ChatError
from .fs import canonical_json, utcnow
from .locks import FileLock
from .schemas import identifier, object_fields
from .recovery import STAMP

PREFIX = "deleted-upload-"


def lock(store, scope):
    store._check_scope(scope)
    return FileLock(
        store.fs,
        f"{store.prefix}/.locks/conversations/{scope.user_id}.{scope.cid}.files.lock",
        wait_seconds=2,
    )


def records(store, scope):
    try:
        return _records(store, scope)
    except ChatError as exc:
        if exc.status < 500:
            raise ChatError("附件删除记录无效。", 503, "CHAT_STORAGE_CORRUPT") from None
        raise


def _records(store, scope):
    result = {}
    for name in store.fs.listdir(f"{scope.path}/control"):
        if not name.startswith(PREFIX):
            continue
        fid = identifier(name[len(PREFIX) : -5])
        if name != PREFIX + fid + ".json":
            raise ChatError("附件删除记录无效。", 503, "CHAT_STORAGE_CORRUPT")
        value = store.fs.read_json(f"{scope.path}/control/{name}", 2048)
        object_fields(
            value,
            {
                "v",
                "owner_user_id",
                "conversation_id",
                "file_id",
                "sha256",
                "deleted_at",
            },
        )
        if (
            type(value["v"]) is not int
            or value["v"] != 1
            or type(value["owner_user_id"]) is not int
            or value["owner_user_id"] != scope.user_id
            or value["conversation_id"] != scope.cid
            or value["file_id"] != fid
            or not isinstance(value["sha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", value["sha256"])
            or not isinstance(value["deleted_at"], str)
            or not STAMP.fullmatch(value["deleted_at"])
        ):
            raise ChatError("附件删除记录无效。", 503, "CHAT_STORAGE_CORRUPT")
        result[fid] = value
    return result


def signature(store, scope):
    return canonical_json(records(store, scope))


def overlay(store, scope, files):
    # Never mutate file.added's immutable event data via projection aliases.
    result = {fid: dict(entry) for fid, entry in files.items()}
    for fid, value in records(store, scope).items():
        entry = result.get(fid)
        if entry is None:
            # This reader may hold an older committed prefix than the delete
            # request. A marker only removes access; never invent a file here.
            continue
        if entry["kind"] != "upload" or entry["sha256"] != value["sha256"]:
            raise ChatError("附件删除记录归属无效。", 503, "CHAT_STORAGE_CORRUPT")
        entry["deleted_at"] = value["deleted_at"]
    return result


def purge(store, scope, files):
    for fid, entry in files.items():
        if entry.get("deleted_at"):
            store.fs.unlink(f"{scope.path}/uploads/{fid}.blob", missing_ok=True)


def delete(store, scope, fid):
    fid = identifier(fid)
    # Lock order: file control -> storage. No long conversation/run lease.
    with lock(store, scope):
        state = store._state(scope)
        if state.storage_state == "corrupt":
            raise ChatError("聊天记录需要修复。", 503, "CHAT_STORAGE_CORRUPT")
        entry = store._projection(scope, state)[1].get(fid)
        if entry is None:
            raise ChatError("附件不存在。", 404, "CHAT_NOT_FOUND")
        if entry["kind"] != "upload":
            raise ChatError("此操作仅删除上传附件。", 400, "CHAT_INVALID_REQUEST")
        with store.storage_lock():
            if not entry.get("deleted_at"):
                value = {
                    "v": 1,
                    "owner_user_id": scope.user_id,
                    "conversation_id": scope.cid,
                    "file_id": fid,
                    "sha256": entry["sha256"],
                    "deleted_at": utcnow(),
                }
                # At most 200 small markers per conversation, bounded by its
                # immutable file history. Allow deletion even at upload quota.
                store.fs.write_json(
                    f"{scope.path}/control/{PREFIX}{fid}.json", value, exclusive=True
                )
                entry["deleted_at"] = value["deleted_at"]
            purge(store, scope, {fid: entry})
        return entry
