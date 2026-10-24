"""Real document/raster rendering, not extension substitution or model code."""

import json
from contextlib import suppress
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from . import config
from .errors import ChatError


def generate(name, content):
    extension = Path(name).suffix.lower()
    if extension in config.TEXT_EXTENSIONS:
        return content
    if extension not in config.OUTPUT_BINARY_EXTENSIONS:
        raise ChatError(
            "不支持此输出格式，请使用 PDF、DOCX、XLSX、PNG、JPEG 或 WebP。",
            422,
            "TOOL_OUTPUT_INVALID",
        )
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).with_name("output_worker.py"))],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.DEVNULL,
            cwd="/",
            close_fds=True,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        try:
            process.communicate(
                json.dumps(
                    {"extension": extension, "content": content}, ensure_ascii=False
                ).encode(),
                timeout=config.OUTPUT_RENDER_SECONDS,
            )
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ChatError(
                "文件生成超时，请简化内容。", 422, "TOOL_OUTPUT_INVALID"
            ) from None
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            if process.stdin is not None and not process.stdin.closed:
                with suppress(OSError):
                    process.stdin.close()
        output.seek(0)
        raw = output.read(config.OUTPUT_FILE_BYTES + 1)
    if process.returncode or not 0 < len(raw) <= config.OUTPUT_FILE_BYTES:
        raise ChatError(
            "文件生成失败，请检查生成格式和大小。", 422, "TOOL_OUTPUT_INVALID"
        )
    return raw
