"""Read explicitly referenced ordinary attachments before any model dispatch.

Attachment text is hydrated from immutable owner/chat-scoped files, never from
a client-provided path/body. Legacy Agent history retains its file references.
History uses the same verified originals; no body is copied into run events.
"""

from collections import Counter

from . import config, context, media
from .errors import ChatError


def _large():
    return ChatError(
        "附件正文超过当前上下文大小限制，尚未调用模型。请拆分文件、减少本轮引用，"
        "或取消本轮引用、让助手按需检索文件。上传上限不等于模型上下文上限。",
        413,
        "CHAT_ATTACHMENTS_TOO_LARGE",
    )


def read_texts(store, scope, files, body, history, policy, *, lease):
    current = body["attachment_ids"]
    if any(files[fid].get("deleted_at") for fid in current):
        raise ChatError("附件已删除，请取消引用后重新发送。", 410, "CHAT_FILE_DELETED")
    budget = policy["max_input_bytes"]
    if (
        sum(
            files[fid]["size_bytes"]
            for fid in current
            if files[fid]["mime"] == media.TEXT_MIME
        )
        > budget
    ):
        raise _large()
    ids = list(current)
    for event in history:
        if event["type"] == "run.accepted" and (
            event["data"].get("mode") == "chat"
            or event["data"].get("attachment_mode") == "server-text-v1"
        ):
            ids.extend(event["data"].get("attachment_ids", []))
    occurrences = Counter(ids)
    texts, projected_bytes, current_bytes, image_count = {}, 0, 0, 0
    wire_bytes = 0
    for fid in dict.fromkeys(ids):
        if fid not in files:
            raise ChatError("附件不存在。", 404, "CHAT_NOT_FOUND")
        entry = files[fid]
        if entry.get("deleted_at"):
            texts[fid] = "[附件已删除，原文件不再提供或读取。]"
            continue
        size = entry["size_bytes"]
        is_text = entry["mime"] == media.TEXT_MIME
        if is_text and (
            size > config.EXTENDED_CONTEXT_BYTES
            or projected_bytes + size > config.MODEL_REQUEST_BYTES
        ):
            raise _large()
        _, raw = store.read_file(scope, fid, lease=lease)
        try:
            value = media.extract(entry["name"], raw)
        except UnicodeError:
            raise ChatError("附件正文校验失败。", 503, "CHAT_STORAGE_CORRUPT") from None
        text = value["text"] if not value["images"] else value
        cost = (
            len(context.compact(value["text"]).encode("utf-8"))
            + len(value["images"]) * config.IMAGE_CONTEXT_BYTES
        )
        image_count += len(value["images"]) * occurrences[fid]
        projected_bytes += cost * occurrences[fid]
        wire_bytes += len(context.compact(text).encode()) * occurrences[fid]
        if fid in current:
            current_bytes += cost
        if (
            projected_bytes > config.MODEL_REQUEST_BYTES
            or current_bytes > budget
            or image_count > config.IMAGE_COUNT_MAX
            or wire_bytes
            > config.MODEL_REQUEST_BYTES - config.EXTENDED_CONTEXT_BYTES // 2
        ):
            raise _large()
        texts[fid] = text
    return texts
