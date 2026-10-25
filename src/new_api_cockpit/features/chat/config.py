"""Resource limits and operator-only configuration for the chat feature."""

from dataclasses import dataclass
import os
import re

from .errors import ChatError

GLOBAL_RUNS = 2
USER_RUNS = 1
MAX_MODEL_REQUESTS = 6
MAX_AUX_MODEL_REQUESTS = 4
MAX_TOTAL_MODEL_REQUESTS = MAX_MODEL_REQUESTS + MAX_AUX_MODEL_REQUESTS
MAX_CANDIDATES = 4
MAX_TOOL_SCHEMAS = 8
MAX_TOOL_CALLS = 16
RUN_DEADLINE_SECONDS = 180
CONNECT_TIMEOUT_SECONDS = 5
UPSTREAM_IDLE_SECONDS = 60
OUTPUT_TOKENS_PER_REQUEST = 2048
MAX_OUTPUT_TOKENS = 32768
USER_TEXT_BYTES = 32768
CONTEXT_BYTES = 65536
EXTENDED_CONTEXT_BYTES = 2097152
MODEL_REQUEST_BYTES = 4194304
REASONING_BYTES = 262144
PROTOCOL_STATE_BYTES = 524288
SUMMARY_BYTES = 16384
SUMMARY_TRIGGER_PERCENT = 75
SUMMARY_RECENT_TURNS = 2
MEMORY_FACTS_PER_UPDATE = 8
MEMORY_FACT_BYTES = 512
MEMORY_INPUT_BYTES = 16384
CONTEXT_HISTORY_RUNS = 1024
CONTEXT_COVERAGE_RUNS = 16384
CONTEXT_COVERAGE_BYTES = 655360
RUN_TEXT_BYTES = 262144
UPSTREAM_BYTES_PER_REQUEST = 4194304
SSE_FRAME_BYTES = 262144
PROTOCOL_FRAME_BYTES = 2097152
TOOL_ARGUMENT_BYTES = 131072
TOOL_RESULT_BYTES = 32768
TOOL_WRITE_BYTES = 65536
OUTPUT_FILE_BYTES = 4 * 1024 * 1024
OUTPUT_RENDER_SECONDS = 15
TOOL_SEARCH_SCAN_BYTES = 8388608
TOOL_READ_MAX_LINES = 200
TOOL_DEADLINE_SECONDS = 5
DELTA_FLUSH_MS = 250
DELTA_FLUSH_BYTES = 4096
HEARTBEAT_SECONDS = 10
POLL_INTERVAL = 2
EVENT_PAGE_MAX = 200
EVENT_RESPONSE_BYTES = 2097152
EVENT_BYTES = 1048576
READ_CACHE_ENTRIES = 8
READ_CACHE_BYTES = 8 * 1024 * 1024
LIST_PAGE_DEFAULT = 50
LIST_PAGE_MAX = 100
TITLE_CHARS = 80
CONVERSATIONS_PER_USER = 200
FILES_PER_CONVERSATION = 200
ATTACHMENTS_PER_TURN = 8
UPLOAD_FILE_BYTES = 50 * 1024 * 1024
UPLOAD_REQUEST_OVERHEAD_BYTES = 65536
UPLOAD_FORM_MEMORY_BYTES = 131072
JSON_REQUEST_BYTES = 131072
# Upload publication reserves two copies plus metadata. Leave room for a
# maximum-size original and a subsequent run without weakening disk quotas.
CONVERSATION_BYTES = 128 * 1024 * 1024
USER_STORAGE_BYTES = 1073741824
TOTAL_STORAGE_BYTES = 10737418240
RUN_STORAGE_RESERVE_BYTES = 16777216
RUN_EVENT_BYTES = 8388608
EVENT_TERMINAL_RESERVE_BYTES = 16384
DISK_FREE_MIN_BYTES = 536870912
QUEUE_EVENTS = 32
STORAGE_LOCK_WAIT_SECONDS = 0.5
MEMORY_BYTES = 8192
SOUL_BYTES = 4096
IDENTITY_BYTES = 2048
SKILL_FILE_BYTES = 8192
SKILL_NAME_CHARS = 64
SKILL_DESCRIPTION_CHARS = 256
SKILLS_PER_USER = 64
SKILLS_PER_RUN = 3
PERSONAL_CONTEXT_BYTES = 32768
SETTINGS_REQUEST_BYTES = 65536
CONTEXT_MANIFEST_BYTES = 131072
CONTEXT_SNAPSHOT_BYTES = 65536
USER_CONTEXT_STORAGE_BYTES = 4194304
CONTEXT_VERSIONS_PER_RESOURCE = 1
TEXT_EXTENSIONS = frozenset(
    ".txt .md .csv .json .yaml .yml .toml .xml .log .py .js .ts .jsx .tsx .html .css .sql .sh".split()
)
BINARY_MIMES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | BINARY_MIMES.keys()
OUTPUT_BINARY_EXTENSIONS = frozenset(".pdf .docx .xlsx .png .jpg .jpeg .webp".split())
OUTPUT_EXTENSIONS = TEXT_EXTENSIONS | OUTPUT_BINARY_EXTENSIONS
DOCUMENT_PARSE_SECONDS = 20
DOCUMENT_TEXT_BYTES = EXTENDED_CONTEXT_BYTES
DOCUMENT_MAX_PAGES = 200
DOCUMENT_ZIP_BYTES = 64 * 1024 * 1024
DOCUMENT_XML_BYTES = 16 * 1024 * 1024
DOCUMENT_MAX_CELLS = 200000
IMAGE_MAX_PIXELS = 20000000
IMAGE_MAX_EDGE = 2048
IMAGE_ENCODED_BYTES = 1048576
IMAGE_COUNT_MAX = 8
IMAGE_CONTEXT_BYTES = 4096


@dataclass(frozen=True)
class Settings:
    data_dir: str = "/app/data/chat"
    instance_id: str = ""
    enabled: bool = False
    max_concurrent_runs: int = GLOBAL_RUNS
    total_storage_bytes: int = TOTAL_STORAGE_BYTES
    capabilities_file: str = ""

    @classmethod
    def from_env(cls):
        try:
            result = cls(
                data_dir=os.environ.get("CHAT_DATA_DIR", "/app/data/chat"),
                instance_id=os.environ.get("CHAT_INSTANCE_ID", ""),
                enabled=os.environ.get("CHAT_ENABLED", "false").lower() == "true",
                max_concurrent_runs=int(
                    os.environ.get("CHAT_MAX_CONCURRENT_RUNS", "2")
                ),
                total_storage_bytes=int(
                    os.environ.get("CHAT_TOTAL_STORAGE_BYTES", str(TOTAL_STORAGE_BYTES))
                ),
                capabilities_file=os.environ.get("CHAT_CAPABILITIES_FILE", ""),
            )
            if result.enabled and (
                not os.path.isabs(result.data_dir)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", result.instance_id)
                or not 1 <= result.max_concurrent_runs <= 8
                or result.total_storage_bytes < USER_STORAGE_BYTES
                or (
                    result.capabilities_file
                    and not os.path.isabs(result.capabilities_file)
                )
            ):
                raise ValueError()
            return result
        except (ValueError, TypeError):
            raise ChatError("聊天配置无效。", 503, "CHAT_CONFIG_INVALID") from None


def public_limits():
    return {
        name.lower(): value
        for name, value in globals().items()
        if name.isupper() and type(value) is int
    }
