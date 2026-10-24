"""Owner-authorized document/image extraction; no provider upload or external URL.

Untrusted binary parsers run in a bounded, credential-free subprocess. Originals
remain immutable. Only derived text/images enter prompts, never event records.
"""

import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from . import config
from .errors import ChatError

TEXT_MIME = "text/plain; charset=utf-8"


def mime_for(name, *, output=False):
    extension = Path(name).suffix.lower()
    if extension in config.TEXT_EXTENSIONS:
        return TEXT_MIME
    if extension in config.BINARY_MIMES and (
        not output or extension in config.OUTPUT_BINARY_EXTENSIONS
    ):
        return config.BINARY_MIMES[extension]
    raise ChatError(
        "不支持此文件类型。支持文本、PDF、Word、Excel 和常见图片。",
        415,
        "CHAT_FILE_TYPE_UNSUPPORTED",
    )


def invalid(code="CHAT_FILE_INVALID"):
    messages = {
        "CHAT_FILE_INVALID": "文件已损坏、加密或与扩展名不符，无法读取；请导出为未加密的受支持格式。",
        "CHAT_FILE_PARSE_LIMIT": "文件解析超过安全上限（文本、页数、单元格、图片数量或像素），请拆分文件后上传。",
        "CHAT_FILE_PARSE_TIMEOUT": "文件解析超时，请简化或拆分文件。",
        "CHAT_FILE_PARSER_UNAVAILABLE": "文件解析组件暂不可用，请联系维护者。",
    }
    return ChatError(
        messages.get(code, messages["CHAT_FILE_INVALID"]),
        503 if code.endswith("UNAVAILABLE") else 413 if code.endswith("LIMIT") else 422,
        code,
    )


def validate_image_part(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"mime", "data"}
        or value["mime"] != "image/jpeg"
    ):
        raise invalid()
    data = value["data"]
    if (
        not isinstance(data, str)
        or len(data) > (config.IMAGE_ENCODED_BYTES + 2) // 3 * 4
    ):
        raise invalid("CHAT_FILE_PARSE_LIMIT")
    try:
        raw = base64.b64decode(data, validate=True)
        if not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9"):
            raise ValueError()
    except (ValueError, TypeError):
        raise invalid() from None
    return value


def extract(name, raw):
    """Bounded parser result; caller must verify file owner/hash before this call."""
    if mime_for(name) == TEXT_MIME:
        return {"text": raw.decode("utf-8"), "images": []}
    extension = Path(name).suffix.lower()
    # Output goes to a capped regular file, not an unbounded stdout pipe. No
    # source filename, credentials, inherited sockets or environment is passed.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("document_worker.py")),
                extension,
            ],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.DEVNULL,
            cwd="/",
            close_fds=True,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        try:
            process.communicate(raw, timeout=config.DOCUMENT_PARSE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise invalid("CHAT_FILE_PARSE_TIMEOUT") from None
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        output.seek(0)
        encoded = output.read(config.MODEL_REQUEST_BYTES * 2 + 1)
    if process.returncode or len(encoded) > config.MODEL_REQUEST_BYTES * 2:
        raise invalid("CHAT_FILE_PARSE_LIMIT")
    try:
        value = json.loads(encoded)
        if "error" in value:
            raise invalid(
                value["error"]
                if value["error"]
                in {
                    "CHAT_FILE_INVALID",
                    "CHAT_FILE_PARSE_LIMIT",
                    "CHAT_FILE_PARSER_UNAVAILABLE",
                }
                else "CHAT_FILE_INVALID"
            )
        if (
            set(value) != {"text", "images"}
            or not isinstance(value["text"], str)
            or not isinstance(value["images"], list)
        ):
            raise ValueError()
        if (
            len(value["text"].encode()) > config.DOCUMENT_TEXT_BYTES
            or len(value["images"]) > config.IMAGE_COUNT_MAX
        ):
            raise invalid("CHAT_FILE_PARSE_LIMIT")
        for image in value["images"]:
            validate_image_part(image)
        return value
    except ChatError:
        raise
    except (ValueError, TypeError, KeyError):
        raise invalid() from None


def image_url(part):
    validate_image_part(part)
    return "data:" + part["mime"] + ";base64," + part["data"]


def measure(value):
    """Context-equivalent byte cost, separate from the hard wire JSON byte cap.

    Base64 transport expansion is not text context. Images reserve a fixed,
    conservative context allowance; actual tokens/cost remain provider-reported.
    """

    def clean(item):
        if isinstance(item, dict):
            if item.get("type") in {"image_url", "input_image"}:
                return {"image_context": "x" * config.IMAGE_CONTEXT_BYTES}
            return {key: clean(v) for key, v in item.items()}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item

    return len(
        json.dumps(
            clean(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )
