"""Bounded, synchronous Images transport; no provider SDK or POST retries.

Use inspect_image(upload_bytes) before request_image(..., images=[record, ...]).
The caller owns authentication, model/parameter policy and capacity. POSTs use
only NEW_API_INTERNAL_URL; no proxy, cookies or client IP headers.
Result URLs require an explicit response contract; see image_results. Uncertain
errors mean a POST may have reached the upstream: do not auto-resend.
"""

from http.client import HTTPConnection, HTTPException
import io
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from PIL import Image, ImageOps

from . import image_results
from .image_results import ImageError

MAX_UPLOAD_BYTES = 24 * 1024 * 1024
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 4
MAX_INPUT_PIXELS = 16_000_000
MAX_TOTAL_PIXELS = 32_000_000
MAX_MASK_BYTES = 4_000_000  # Decimal; strictly less, before AND after encoding.
MAX_OUTPUT_PIXELS = 8_500_000
MAX_JSON_BYTES = 48 * 1024 * 1024
MAX_OUTPUT_BYTES = MAX_JSON_BYTES // 4 * 3
CONNECT_TIMEOUT = 10.0
UPLOAD_TIMEOUT = 30.0
TOTAL_TIMEOUT = 300.0
_CHUNK = 64 * 1024
_PATHS = {"/v1/images/generations", "/v1/images/edits"}
_FORMATS = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}
_FIELDS = {
    "model",
    "prompt",
    "size",
    "quality",
    "output_format",
    "background",
    "output_compression",
    "input_fidelity",
    "n",
    "response_format",
}
_RESOLVER_CODE = (
    "import json,socket,sys; "
    "a=socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)[0]; "
    "print(json.dumps([a[0],a[1],a[2],a[4]]))"
)
logger = logging.getLogger(__name__)


class _BoundedBuffer(io.BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def write(self, data):
        if self.tell() + len(data) > self.limit:
            raise ImageError("图片编码后超过大小限制。", 413, "IMAGE_TOO_LARGE")
        return super().write(data)


def _decode_raster(data, *, mask, max_pixels, max_bytes):
    """Check dimensions before verify/load; never change Pillow's global limits."""
    if not isinstance(data, bytes) or not data:
        raise ImageError("图片文件无效。", code="IMAGE_INVALID_IMAGE")
    if len(data) > max_bytes or (mask and len(data) >= MAX_MASK_BYTES):
        raise ImageError("图片文件超过大小限制。", 413, "IMAGE_TOO_LARGE")
    result = None
    try:
        with Image.open(io.BytesIO(data), formats=list(_FORMATS)) as source:
            image_format = source.format
            if mask and image_format != "PNG":
                raise ImageError(
                    "遮罩必须是带透明通道的 PNG。", code="IMAGE_INVALID_MASK"
                )
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ImageError("图片像素数超过限制。", 413, "IMAGE_TOO_MANY_PIXELS")
            if (
                getattr(source, "is_animated", False)
                or getattr(source, "n_frames", 1) != 1
            ):
                raise ImageError("不支持动画图片。", code="IMAGE_ANIMATED")
            if (
                mask
                and "A" not in source.getbands()
                and "transparency" not in source.info
            ):
                raise ImageError("遮罩必须具有透明通道。", code="IMAGE_INVALID_MASK")
            source.verify()
        with Image.open(io.BytesIO(data), formats=list(_FORMATS)) as source:
            source.load()  # Header/MIME checks alone are not image validation.
            result = source.copy()
        return result, image_format
    except ImageError:
        if result is not None:
            result.close()
        raise
    except (
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        TypeError,
        OverflowError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        if result is not None:
            result.close()
        raise ImageError(
            "图片无法完整解码或不是受支持的静态图片。", code="IMAGE_INVALID_IMAGE"
        ) from None


def _encode_raster(image, image_format, limit):
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    with image.convert(
        "RGBA" if has_alpha and image_format != "JPEG" else "RGB"
    ) as clean:
        # Do not copy EXIF, ICC, comments, text chunks or upstream metadata.
        clean.info.clear()
        clean.getexif().clear()
        with _BoundedBuffer(limit) as output:
            options = {"quality": 95} if image_format == "JPEG" else {}
            if image_format == "WEBP":
                options = {"lossless": True}
            clean.save(output, format=image_format, **options)
            return output.getvalue()


def _normalize_image(data, *, mask, max_pixels, max_bytes):
    image, image_format = _decode_raster(
        data, mask=mask, max_pixels=max_pixels, max_bytes=max_bytes
    )
    try:
        # Orientation is applied here once. Normalized records contain no EXIF;
        # request_image validates their raster but does not transpose/re-encode.
        ImageOps.exif_transpose(image, in_place=True)
        normalized = _encode_raster(
            image,
            image_format,
            min(max_bytes, MAX_MASK_BYTES - 1) if mask else max_bytes,
        )
        mime, extension = _FORMATS[image_format]
        return {
            "data": normalized,
            "mime": mime,
            "width": image.width,
            "height": image.height,
            "filename": "mask.png" if mask else "image." + extension,
        }
    except ImageError:
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        raise ImageError("图片无法安全规范化。", code="IMAGE_INVALID_IMAGE") from None
    finally:
        image.close()


def inspect_image(data: bytes, *, mask=False, max_pixels=None):
    """Decode static PNG/JPEG/WebP, normalize EXIF once and remove metadata.

    A custom pixel limit may tighten, but never loosen, the 16 MP input limit.
    Masks retain their alpha values (transparent = edit); no inversion/resizing.
    """
    if max_pixels is None:
        max_pixels = MAX_INPUT_PIXELS
    if type(max_pixels) is not int or max_pixels <= 0:
        raise ImageError("图片像素限制无效。", code="IMAGE_INVALID_IMAGE")
    return _normalize_image(
        data,
        mask=mask,
        max_pixels=min(max_pixels, MAX_INPUT_PIXELS),
        max_bytes=MAX_INPUT_BYTES,
    )


def _checked_record(record, *, mask=False):
    if not isinstance(record, dict):
        raise ImageError("图片记录无效。", code="IMAGE_INVALID_IMAGE")
    image, image_format = _decode_raster(
        record.get("data"),
        mask=mask,
        max_pixels=MAX_INPUT_PIXELS,
        max_bytes=MAX_INPUT_BYTES,
    )
    try:
        mime, extension = _FORMATS[image_format]
        if (
            type(record.get("width")) is not int
            or type(record.get("height")) is not int
            or (record["width"], record["height"]) != image.size
            or record.get("mime") != mime
            or image.getexif().get(274) not in (None, 1)
        ):
            raise ImageError(
                "图片记录未规范化或与实际像素不符。", code="IMAGE_INVALID_IMAGE"
            )
        # Never trust a supplied filename in multipart headers.
        return {
            "data": record["data"],
            "width": image.width,
            "height": image.height,
            "mime": mime,
            "filename": "mask.png" if mask else "image." + extension,
        }
    finally:
        image.close()


def _parameters(values, response_format="b64_json"):
    if (
        not isinstance(values, dict)
        or set(values) - _FIELDS
        or not {"model", "prompt"} <= set(values)
    ):
        raise ImageError("图片请求字段无效。", code="IMAGE_INVALID_REQUEST")
    if "response_format" in values and values["response_format"] != response_format:
        raise ImageError("图片响应格式无效。", code="IMAGE_INVALID_REQUEST")
    result = {}
    for name, value in values.items():
        if name == "n":
            continue  # Always one, even if a server caller accidentally passes more.
        if not isinstance(value, str) and type(value) is not int:
            raise ImageError("图片请求只接受标量字段。", code="IMAGE_INVALID_REQUEST")
        if isinstance(value, str) and (len(value) > 128_000 or "\x00" in value):
            raise ImageError("图片请求字段过长或无效。", code="IMAGE_INVALID_REQUEST")
        result[name] = value
    if any(
        not isinstance(result[name], str) or not result[name].strip()
        for name in ("model", "prompt")
    ):
        raise ImageError("模型和提示词不能为空。", code="IMAGE_INVALID_REQUEST")
    result["n"] = 1
    return result


def _request_body(
    path,
    payload,
    fields,
    images,
    mask,
    image_field="image[]",
    response_format="b64_json",
):
    if not isinstance(image_field, str) or image_field not in {"image", "image[]"}:
        raise ImageError("图片上传字段无效。", code="IMAGE_INVALID_REQUEST")
    if path not in _PATHS:
        raise ImageError("不支持该图片请求路径。", code="IMAGE_INVALID_REQUEST")
    if path.endswith("generations"):
        if fields is not None or images is not None or mask is not None:
            raise ImageError("生成请求必须使用 JSON。", code="IMAGE_INVALID_REQUEST")
        try:
            body = json.dumps(
                _parameters(payload, response_format),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (UnicodeError, ValueError) as exc:
            if isinstance(exc, ImageError):
                raise
            raise ImageError(
                "图片请求编码无效。", code="IMAGE_INVALID_REQUEST"
            ) from None
        if len(body) > MAX_UPLOAD_BYTES:
            raise ImageError("图片请求超过上传限制。", 413, "IMAGE_TOO_LARGE")
        return [body], "application/json"
    if (
        payload is not None
        or not isinstance(images, (list, tuple))
        or not 1 <= len(images) <= MAX_IMAGES
    ):
        raise ImageError(
            "编辑请求必须包含 1 至 4 张图片。", code="IMAGE_INVALID_REQUEST"
        )
    parameters = _parameters(fields, response_format)
    # Reject aggregate size BEFORE decoding/copying up to four individual files.
    records = list(images) + ([] if mask is None else [mask])
    if any(
        not isinstance(item, dict) or not isinstance(item.get("data"), bytes)
        for item in records
    ):
        raise ImageError("图片记录无效。", code="IMAGE_INVALID_IMAGE")
    if sum(len(item["data"]) for item in records) > MAX_UPLOAD_BYTES:
        raise ImageError("图片请求超过上传限制。", 413, "IMAGE_TOO_LARGE")
    checked = [_checked_record(item) for item in images]
    if sum(item["width"] * item["height"] for item in checked) > MAX_TOTAL_PIXELS:
        raise ImageError("参考图片总像素数超过限制。", 413, "IMAGE_TOO_MANY_PIXELS")
    checked_mask = None if mask is None else _checked_record(mask, mask=True)
    if checked_mask is not None:
        first = checked[0]
        if (first["width"], first["height"]) != (
            checked_mask["width"],
            checked_mask["height"],
        ):
            raise ImageError(
                "遮罩必须与第一张图片尺寸相同。", code="IMAGE_INVALID_MASK"
            )
        if first["mime"] != "image/png":
            with Image.open(io.BytesIO(first["data"])) as image:
                first["data"] = _encode_raster(image, "PNG", MAX_INPUT_BYTES)
            first.update(mime="image/png", filename="image.png")
    boundary = "cockpit-" + os.urandom(24).hex()
    # These are bounded byte chunks, NOT an additional joined 24 MiB copy.
    parts = []
    for name, value in parameters.items():
        try:
            parts.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                ).encode("utf-8")
            )
        except UnicodeError:
            raise ImageError(
                "图片请求编码无效。", code="IMAGE_INVALID_REQUEST"
            ) from None
    for name, item in [(image_field, item) for item in checked] + (
        [] if checked_mask is None else [("mask", checked_mask)]
    ):
        parts.extend(
            [
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{item["filename"]}"\r\nContent-Type: {item["mime"]}\r\n\r\n'
                ).encode("ascii"),
                item["data"],
                b"\r\n",
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    if sum(map(len, parts)) > MAX_UPLOAD_BYTES:
        raise ImageError("图片请求超过上传限制。", 413, "IMAGE_TOO_LARGE")
    return parts, "multipart/form-data; boundary=" + boundary


def _configuration(key):
    raw = os.environ.get("NEW_API_INTERNAL_URL", "http://new-api:3000")
    try:
        if (
            not raw
            or len(raw) > 2048
            or any(ord(c) <= 32 or ord(c) >= 127 or c == "\\" for c in raw)
        ):
            raise ValueError()
        base = urlsplit(raw)
        if (
            base.scheme not in {"http", "https"}
            or not base.hostname
            or base.username is not None
            or base.password is not None
            or base.query
            or base.fragment
            or "?" in raw
            or "#" in raw
            or base.port == 0
            or not re.fullmatch(r"[A-Za-z0-9.:_-]+", base.hostname)
            or not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*/?", base.path)
            or any(segment in {".", ".."} for segment in base.path.split("/"))
        ):
            raise ValueError()
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 3500
            or any(not 33 <= ord(c) <= 126 for c in key)
        ):
            raise ValueError()
        return base
    except ValueError:
        raise ImageError(
            "New API 内部连接配置或凭据无效。", 503, "IMAGE_CONFIGURATION_ERROR"
        ) from None


class _SocketWatchdog:
    """One cancellable timer per network phase; shuts down even buffered reads.

    Socket timeouts alone are inactivity limits: a peer can drip header/chunk
    lines forever. Hold the actual socket even if HTTPConnection detaches it
    for Connection: close. Timers only close sockets; they never send requests.
    """

    def __init__(self, deadline):
        self.deadline = deadline
        self.phase_deadline = deadline
        self.sock = None
        self.expired = False
        self.timer = None
        self.lock = threading.Lock()
        self.closed = False

    def remaining(self):
        remaining = self.phase_deadline - time.monotonic()
        if self.expired or remaining <= 0:
            raise TimeoutError()
        return remaining

    def arm(self, seconds=None):
        with self.lock:
            self.remaining()  # Never extend an already-expired earlier phase.
            if self.timer is not None:
                self.timer.cancel()
            self.phase_deadline = (
                min(self.deadline, time.monotonic() + seconds)
                if seconds is not None
                else self.deadline
            )
            self.remaining()
            self.timer = threading.Timer(self.remaining(), self._expire)
            self.timer.daemon = True
            self.timer.start()

    def attach(self, sock):
        with self.lock:
            self.remaining()
            self.sock = sock
            sock.settimeout(self.remaining())

    def _expire(self):
        with self.lock:
            if self.closed or time.monotonic() < self.phase_deadline:
                return
            self.expired = True
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()

    def close(self):
        with self.lock:
            self.closed = True
            if self.timer is not None:
                self.timer.cancel()
            if self.sock is not None:
                self.sock.close()


def _resolve_address(host, port, watchdog):
    # Numeric addresses require no resolver process or DNS at all.
    try:
        addresses = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST
        )
    except socket.gaierror:
        # getaddrinfo has no timeout/cancellable socket in this process. Keep
        # NSS/DNS in a short-lived synchronous child, killed and reaped by run()
        # on timeout. It returns ONE fixed-size socket address, never a body,
        # receives no credential, and cannot outlive this request as a task.
        try:
            resolved = subprocess.run(
                [sys.executable, "-I", "-c", _RESOLVER_CODE, host, str(port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={},
                timeout=watchdog.remaining(),
                check=True,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError() from None
        except subprocess.CalledProcessError:
            raise OSError() from None
        if len(resolved.stdout) > 256:
            raise OSError()
        family, kind, protocol, address = json.loads(resolved.stdout)
        if (
            family not in (socket.AF_INET, socket.AF_INET6)
            or kind != socket.SOCK_STREAM
        ):
            raise OSError()
        addresses = [(family, kind, protocol, "", tuple(address))]
    watchdog.remaining()
    if not addresses:
        raise OSError()
    return addresses[0]


def _connect(base, watchdog, *, address=None):
    port = base.port or (443 if base.scheme == "https" else 80)
    # Resolve the configured POST host OR use the already-validated result IP.
    # Never consult proxy environment variables/netrc or fall back to DNS.
    family, kind, protocol, _, address = (
        _resolve_address(base.hostname, port, watchdog) if address is None else address
    )
    sock = socket.socket(family, kind, protocol)
    try:
        watchdog.attach(sock)
        sock.connect(address)  # No address fallback or second attempt.
        if base.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(
                sock, server_hostname=base.hostname, do_handshake_on_connect=False
            )
            watchdog.attach(sock)
            sock.do_handshake()
        watchdog.remaining()
        connection = HTTPConnection(base.hostname, port)
        connection.auto_open = 0  # A closed socket must never cause a new POST.
        connection.sock = sock
        return connection
    except BaseException:
        sock.close()
        raise


def _response_error(
    message="New API 图片响应无效，未自动重试。", code="IMAGE_INVALID_RESPONSE"
):
    return ImageError(message, 502, code, uncertain=True)


def _read_response(response, watchdog, *, max_bytes=None, raster=False):
    max_bytes = MAX_JSON_BYTES if max_bytes is None else min(max_bytes, MAX_JSON_BYTES)
    content_types = response.headers.get_all("Content-Type", [])
    encodings = response.headers.get_all("Content-Encoding", [])
    lengths = response.headers.get_all("Content-Length", [])
    transfers = response.headers.get_all("Transfer-Encoding", [])
    if (
        len(content_types) != 1
        or not re.fullmatch(
            r"image/(?:png|jpeg|webp)"
            if raster
            else r'application/json(?:\s*;\s*charset\s*=\s*"?utf-8"?)?',
            content_types[0].strip(),
            re.I,
        )
        or len(encodings) > 1
        or (encodings and encodings[0].strip().lower() != "identity")
        or len(lengths) > 1
        or len(transfers) > 1
        or (lengths and transfers)
        or (transfers and transfers[0].strip().lower() != "chunked")
    ):
        raise _response_error()
    expected = None
    if lengths:
        if not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
            raise _response_error()
        expected = int(lengths[0])
        if expected > max_bytes:
            raise _response_error(
                "New API 图片响应超过大小限制。", "IMAGE_RESPONSE_TOO_LARGE"
            )
    body = bytearray()
    # HTTPResponse.read1 closes its file on the final declared byte or EOF. For
    # Connection: close this also releases the socket: do not set its timeout
    # again after a complete read. Length/truncation checks still run below.
    while not response.isclosed():
        watchdog.sock.settimeout(watchdog.remaining())
        chunk = response.read1(min(_CHUNK, max_bytes + 1 - len(body)))
        watchdog.remaining()
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > max_bytes:
            raise _response_error(
                "New API 图片响应超过大小限制。", "IMAGE_RESPONSE_TOO_LARGE"
            )
    if expected is not None and len(body) != expected:
        raise _response_error()
    return body


def _upstream_rejection(response, watchdog, identifier, secrets):
    body = None
    try:
        watchdog.arm(2)
        body = image_results.parse_json(
            _read_response(response, watchdog, max_bytes=8192)
        )
    except (ImageError, OSError, HTTPException, ValueError, RecursionError):
        pass  # A slow/unreadable error cannot replace the known upstream status.
    details = image_results.error_details(body, response.status, identifier, secrets)
    certain = response.status in {400, 401, 403, 404, 413, 415, 422, 429}
    code = (
        "IMAGE_UPSTREAM_DENIED"
        if response.status in {401, 403}
        else "IMAGE_UPSTREAM_RATE_LIMIT"
        if response.status == 429
        else "IMAGE_UPSTREAM_ERROR"
    )
    return ImageError(
        image_results.rejection_message(response.status, details),
        403 if response.status in {401, 403} else response.status if certain else 502,
        code,
        uncertain=not certain,
        details=details,
    )


def request_image(
    path,
    key,
    *,
    payload=None,
    fields=None,
    images=None,
    mask=None,
    image_field="image[]",
    response_contract=None,
):
    """One synchronous POST. Deadline includes preparation, upload and response.

    Explicit 4xx rejection is certain; transport/protocol/5xx failures after
    sending headers are uncertain. A timeout/cancellation does NOT imply free.
    HTTP errors and decoder exceptions never expose upstream bodies or keys.
    image_field is exactly image[] (default) or image; mask is optional and is
    never silently removed/retried. response_contract is a server-only mapping
    {format: b64_json|url, allowed_hosts: [...], allowed_networks: [CIDR, ...]}.
    It permits URL resolution but does not inject/alter request parameters.
    """
    started = time.monotonic()
    watchdog = _SocketWatchdog(started + TOTAL_TIMEOUT)
    connection = response = None
    sent = False
    phase = "prepare"
    identifier = None
    protected_credentials = (key,)
    secrets = protected_credentials + tuple(
        values.get("prompt") for values in (payload, fields) if isinstance(values, dict)
    )
    try:
        base = _configuration(key)
        contract = image_results.validate_contract(response_contract)
        parts, content_type = _request_body(
            path, payload, fields, images, mask, image_field, contract.format
        )
        watchdog.arm(CONNECT_TIMEOUT)
        phase = "connect"
        connection = _connect(base, watchdog)
        watchdog.arm(UPLOAD_TIMEOUT)
        phase = "upload"
        connection.putrequest(
            "POST", base.path.rstrip("/") + path, skip_accept_encoding=True
        )
        for name, value in {
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": content_type,
            "Content-Length": str(sum(map(len, parts))),
            "Connection": "close",
        }.items():
            connection.putheader(name, value)
        watchdog.sock.settimeout(watchdog.remaining())
        sent = True  # sendall can fail AFTER sending some/all header bytes.
        connection.endheaders()
        for part in parts:
            for offset in range(0, len(part), _CHUNK):
                watchdog.sock.settimeout(watchdog.remaining())
                connection.send(part[offset : offset + _CHUNK])
        watchdog.arm()
        watchdog.sock.settimeout(watchdog.remaining())
        phase = "response_headers"
        response = connection.getresponse()
        watchdog.remaining()
        identifier = image_results.request_id(response.headers, secrets)
        if response.status != 200:
            raise _upstream_rejection(response, watchdog, identifier, secrets)
        phase = "response_body"
        body = _read_response(response, watchdog)
        phase = "response_decode"
        result = image_results.decode_response(
            body,
            contract,
            deadline=watchdog.deadline,
            secrets=secrets,
            protected_credentials=protected_credentials,
        )
        watchdog.remaining()
        if identifier:
            result["upstream_request_id"] = identifier
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        return result
    except ImageError as exc:
        details = dict(exc.details)
        details["http_status"] = (
            response.status if response is not None else details["http_status"]
        )
        details["upstream_request_id"] = identifier or details["upstream_request_id"]
        exc.details = image_results.safe_details(details, secrets)
        logger.warning(
            "Image request failed phase=%s code=%s upstream_status=%s request_id=%s",
            phase,
            exc.code,
            response.status if response is not None else None,
            exc.details["upstream_request_id"],
        )
        if watchdog.expired and (exc.uncertain or not sent):
            raise ImageError(
                "图片请求超时，未自动重试；请勿立即重复提交。",
                504,
                "IMAGE_TIMEOUT",
                uncertain=sent,
                details=exc.details,
            ) from None
        raise
    except (TimeoutError, OSError, HTTPException, ValueError, UnicodeError) as exc:
        # No exception text, URL, credential, prompt or response body in logs.
        details = image_results.safe_details(
            {
                "http_status": response.status if response is not None else None,
                "upstream_request_id": identifier,
            },
            secrets,
        )
        logger.warning(
            "Image transport failed phase=%s error=%s errno=%s upstream_status=%s request_id=%s",
            phase,
            type(exc).__name__,
            getattr(exc, "errno", None),
            response.status if response is not None else None,
            details["upstream_request_id"],
        )
        timed_out = watchdog.expired or time.monotonic() >= watchdog.phase_deadline
        # A socket inactivity timeout also needs the explicit timeout category.
        timed_out = timed_out or isinstance(exc, TimeoutError)
        if timed_out:
            raise ImageError(
                "图片请求超时，未自动重试；请勿立即重复提交。",
                504,
                "IMAGE_TIMEOUT",
                uncertain=sent,
                details=details,
            ) from None
        raise ImageError(
            "New API 图片连接或响应异常，未自动重试。",
            502 if sent else 503,
            "IMAGE_CONNECTION_ERROR",
            uncertain=sent,
            details=details,
        ) from None
    finally:
        watchdog.close()
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()
