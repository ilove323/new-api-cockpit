"""Private Images response boundary: safe diagnostics and normalized rasters.

URL retrieval is opt-in: exact allowed host + public DNS addresses, OR public
addresses in explicit CIDRs. Even explicit CIDRs never enable private, loopback,
link-local, metadata, multicast or transition addresses. Every redirect is
revalidated and gets a fresh pinned socket (at most three redirects). Only HTTPS
443 is supported; no proxy, credential, cookie or Referer is ever sent.

Network primitives/raster codecs live in image_transport; local imports below
avoid an import cycle and preserve the transport's existing resource limits.
Decoding runs in a killed-and-reaped child; argv/environment contain no provider
key or request text. Untrusted pixels/metadata reach it only on bounded stdin.
"""

import base64
from dataclasses import dataclass
from http.client import HTTPException
import ipaddress
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
from urllib.parse import quote, unquote_plus, urljoin, urlsplit

RESULT_TIMEOUT = 30.0
DECODE_TIMEOUT = 20.0
MAX_REDIRECTS = 3
MAX_DNS_ADDRESSES = 32
_CATEGORIES = {"moderation", "params", "quota", "auth", "rate", "unknown"}
_PARAMS = {
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
    "image",
    "image[]",
    "mask",
}
_CODES = {
    "moderation": {
        "content_policy_violation",
        "moderation_blocked",
        "moderation",
        "safety_violations",
        "safety_violation",
        "content_filter",
        "content_filter_error",
    },
    "params": {
        "invalid_request_error",
        "invalid_parameter",
        "unsupported_parameter",
        "invalid_value",
        "invalid_image",
        "invalid_mask",
        "model_not_found",
    }
    | {"invalid_" + field for field in _PARAMS}
    | {field + "_not_supported" for field in _PARAMS},
    "quota": {
        "insufficient_quota",
        "quota_exceeded",
        "billing_hard_limit_reached",
        "billing_not_active",
        "insufficient_balance",
        "insufficient_user_quota",
        "insufficient_token_quota",
    },
    "auth": {
        "invalid_api_key",
        "authentication_error",
        "permission_denied",
        "unauthorized",
        "access_denied",
        "ip_not_allowed",
    },
    "rate": {"rate_limit_exceeded", "rate_limit_error", "too_many_requests"},
}
_KNOWN_CODES = set().union(*_CODES.values())
_ID_HEADERS = ("X-Request-Id", "X-Oneapi-Request-Id", "Request-Id")
_DNS_CODE = """
import json, socket, sys
addresses = socket.getaddrinfo(sys.argv[1], 443, type=socket.SOCK_STREAM,
                               proto=socket.IPPROTO_TCP)
if not 1 <= len(addresses) <= 32:
    sys.exit(1)
print(json.dumps(addresses))
"""
_DECODE_CODE = """
import struct, sys
sys.path.insert(0, sys.argv[1])
from new_api_cockpit.features.image_studio.transport import _normalize_image
limit, pixels = int(sys.argv[2]), int(sys.argv[3])
raw = sys.stdin.buffer.read(limit + 1)
image = _normalize_image(raw, mask=False, max_pixels=pixels, max_bytes=limit)
kind = {'image/png': 0, 'image/jpeg': 1, 'image/webp': 2}[image['mime']]
sys.stdout.buffer.write(struct.pack('!IIB', image['width'], image['height'], kind))
sys.stdout.buffer.write(image['data'])
"""


def _contains_secret(value, secrets, *, identifiers=True):
    lowered = value.casefold()
    if not identifiers:
        # Catch partially percent-escaped secrets in provider URLs as well as
        # whole-string URL/base64 encodings; never pass the model key in a GET.
        lowered += "\n" + unquote_plus(value).casefold()
    if identifiers and (
        re.search(r"(?:^|[^a-z0-9])sk[-_]", lowered)
        or "bearer" in lowered
        or "://" in lowered
    ):
        return True
    for secret in secrets:
        if not isinstance(secret, str) or not secret:
            continue
        raw = secret.encode("utf-8", errors="replace")
        variants = (
            secret,
            quote(secret, safe=""),
            base64.b64encode(raw).decode().rstrip("="),
            base64.urlsafe_b64encode(raw).decode().rstrip("="),
        )
        if any(item.casefold() in lowered for item in variants):
            return True
    return False


def safe_request_id(value, secrets=()):
    if (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value)
        and not _contains_secret(value, secrets)
    ):
        return value
    return None


def request_id(headers, secrets=()):
    for header in _ID_HEADERS:
        values = headers.get_all(header, [])
        if len(values) == 1:
            result = safe_request_id(values[0], secrets)
            if result:
                return result
    return None


def safe_details(details=None, secrets=()):
    """Drop unknown keys and free-form strings, including arbitrary error codes."""
    source = details if isinstance(details, dict) else {}
    category = source.get("category")
    status = source.get("http_status")
    code, param = source.get("provider_code"), source.get("param")
    return {
        "category": category
        if isinstance(category, str) and category in _CATEGORIES
        else "unknown",
        "http_status": status if type(status) is int and 100 <= status <= 599 else None,
        "provider_code": code
        if isinstance(code, str)
        and code in _KNOWN_CODES
        and not _contains_secret(code, secrets)
        else None,
        "param": param
        if isinstance(param, str)
        and param in _PARAMS
        and not _contains_secret(param, secrets)
        else None,
        "upstream_request_id": safe_request_id(
            source.get("upstream_request_id"), secrets
        ),
    }


class ImageError(ValueError):
    def __init__(
        self, message, status=400, code="IMAGE_ERROR", uncertain=False, details=None
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.uncertain = bool(uncertain)
        self.details = safe_details(details)


def response_error(
    message="New API 图片响应无效，未自动重试。", code="IMAGE_INVALID_RESPONSE"
):
    return ImageError(message, 502, code, uncertain=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError()


def parse_json(body):
    try:
        return json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        raise response_error() from None


def _body_request_id(body, secrets):
    if not isinstance(body, dict):
        return None
    for name in ("request_id", "requestId"):
        value = safe_request_id(body.get(name), secrets)
        if value:
            return value
    return None


def error_details(body, http_status, upstream_request_id=None, secrets=()):
    """Messages inform classification only; never copy them into diagnostics."""
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    code, kind, param = error.get("code"), error.get("type"), error.get("param")
    code = code if isinstance(code, str) and code in _KNOWN_CODES else None
    param = param if isinstance(param, str) and param in _PARAMS else None
    category = "unknown"
    for name in ("moderation", "quota", "auth", "rate", "params"):
        codes = _CODES[name]
        if code in codes or (isinstance(kind, str) and kind in codes):
            category = name
            break
    message = error.get("message")
    message = message[:4096].lower() if isinstance(message, str) else ""
    # Specific safety/billing signals take precedence over generic invalid_request.
    if category in {"unknown", "params"}:
        if re.search(
            r"content.policy|moderation|safety.(?:system|violation|filter)|内容审核",
            message,
        ):
            category = "moderation"
        elif re.search(
            r"insufficient.(?:quota|balance|credit)|quota.exceeded|billing.hard.limit|余额不足|额度不足",
            message,
        ):
            category = "quota"
        elif re.search(r"rate.limit|too.many.requests", message):
            category = "rate"
        elif re.search(
            r"\bmask\b.{0,64}(?:not.supported|unsupported|not.allowed)|(?:unsupported|unknown|unrecognized).{0,32}\bmask\b",
            message,
        ):
            category, param = "params", "mask"
    if not param and category == "params" and code:
        for field in _PARAMS:
            if code in {"invalid_" + field, field + "_not_supported"}:
                param = field
                break
    if category == "unknown":
        category = (
            "auth"
            if http_status in {401, 403}
            else "rate"
            if http_status == 429
            else "params"
            if http_status in {400, 413, 415, 422}
            else "unknown"
        )
    return safe_details(
        {
            "category": category,
            "http_status": http_status,
            "provider_code": code,
            "param": param,
            "upstream_request_id": upstream_request_id
            or _body_request_id(error, secrets)
            or _body_request_id(body, secrets),
        },
        secrets,
    )


def rejection_message(status, details):
    label = {
        "moderation": "内容审核未通过。",
        "params": "请求参数不受支持或无效。",
        "quota": "可用额度不足。",
        "auth": "所选令牌或服务侧 IP 不被允许。",
        "rate": "请求频率受限。",
        "unknown": "",
    }[details["category"]]
    param = details["param"]
    return (
        f"New API 图片请求失败（HTTP {status}）。{label}"
        + (f"请检查参数（{param}）。" if param else "")
        + "未自动重试。"
    )


@dataclass(frozen=True)
class ResponseContract:
    format: str
    allowed_hosts: frozenset
    allowed_networks: tuple


def _hostname(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError()
    try:
        ip = ipaddress.ip_address(value)
        if "%" in value:
            raise ValueError()
        return str(ip)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*", value):
            raise ValueError() from None
        if any(
            len(part) > 63 or part.startswith("-") or part.endswith("-")
            for part in value.split(".")
        ):
            raise ValueError()
        return value.lower()


def validate_contract(value):
    if value is None:
        return ResponseContract("b64_json", frozenset(), ())
    try:
        if not isinstance(value, dict) or set(value) - {
            "format",
            "allowed_hosts",
            "allowed_networks",
        }:
            raise ValueError()
        fmt = value.get("format")
        if fmt not in ("b64_json", "url"):
            raise ValueError()
        hosts, networks = (
            value.get("allowed_hosts", []),
            value.get("allowed_networks", []),
        )
        if any(
            not isinstance(items, list) or len(items) > 64
            for items in (hosts, networks)
        ):
            raise ValueError()
        hosts = frozenset(_hostname(host) for host in hosts)
        parsed = []
        for network in networks:
            if (
                not isinstance(network, str)
                or len(network) > 64
                or "/" not in network
                or "%" in network
            ):
                raise ValueError()
            parsed.append(ipaddress.ip_network(network, strict=True))
        if fmt == "url" and not hosts and not parsed:
            raise ValueError()
        return ResponseContract(fmt, hosts, tuple(parsed))
    except (ValueError, TypeError):
        raise ImageError(
            "图片响应契约无效或缺少 URL 白名单。", 503, "IMAGE_CONFIGURATION_ERROR"
        ) from None


def _url_target(url, protected_credentials=()):
    try:
        if (
            not isinstance(url, str)
            or not 1 <= len(url) <= 8192
            or any(ord(c) <= 32 or ord(c) >= 127 or c == "\\" for c in url)
            or _contains_secret(url, protected_credentials, identifiers=False)
        ):
            raise ValueError()
        target = urlsplit(url)
        host = _hostname(target.hostname)
        if (
            target.scheme != "https"
            or target.port not in (None, 443)
            or target.username is not None
            or target.password is not None
            or "#" in url
            or target.netloc.endswith(":")
            or host
            in {
                "localhost",
                "metadata",
                "metadata.google.internal",
                "metadata.azure.internal",
            }
            or host.endswith((".localhost", ".local"))
        ):
            raise ValueError()
        return target
    except (ValueError, TypeError):
        raise response_error(
            "图片结果 URL 被安全策略拒绝。", "IMAGE_RESULT_URL_DENIED"
        ) from None


def _resolve_addresses(host, watchdog):
    try:
        ip = ipaddress.ip_address(host)
        family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
        address = (str(ip), 443) if ip.version == 4 else (str(ip), 443, 0, 0)
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)]
    except ValueError:
        pass
    try:
        child = subprocess.run(
            [sys.executable, "-I", "-c", _DNS_CODE, host],
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
    if len(child.stdout) > 8192:
        raise response_error()
    watchdog.remaining()
    return parse_json(child.stdout)


def _public_ip(ip):
    return (
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_unspecified
        and str(ip) != "168.63.129.16"  # Azure platform/metadata virtual IP.
        and not (ip.version == 6 and (ip.ipv4_mapped or ip.sixtofour or ip.teredo))
        and not (ip.version == 6 and ip in ipaddress.ip_network("64:ff9b::/96"))
    )


def _validated_address(target, contract, watchdog):
    host = _hostname(target.hostname)
    if host not in contract.allowed_hosts and not contract.allowed_networks:
        raise response_error("图片结果地址被安全策略拒绝。", "IMAGE_RESULT_URL_DENIED")
    addresses = _resolve_addresses(host, watchdog)
    try:
        if (
            not isinstance(addresses, (list, tuple))
            or not 1 <= len(addresses) <= MAX_DNS_ADDRESSES
        ):
            raise ValueError()
        checked = []
        for family, kind, protocol, _, address in addresses:
            if (
                family not in (socket.AF_INET, socket.AF_INET6)
                or kind != socket.SOCK_STREAM
                or protocol not in (0, socket.IPPROTO_TCP)
                or not isinstance(address, (list, tuple))
                or len(address) != (2 if family == socket.AF_INET else 4)
                or address[1] != 443
                or not isinstance(address[0], str)
                or "%" in address[0]
                or (family == socket.AF_INET6 and tuple(address[2:]) != (0, 0))
            ):
                raise ValueError()
            ip = ipaddress.ip_address(address[0])
            if (ip.version == 4) != (family == socket.AF_INET) or not _public_ip(ip):
                raise ValueError()
            if host not in contract.allowed_hosts and not any(
                ip in net for net in contract.allowed_networks
            ):
                raise ValueError()
            checked.append((family, kind, protocol, "", tuple(address)))
        # Never fall back/re-resolve after validation/connect failure.
        return checked[0]
    except (ValueError, TypeError, IndexError):
        raise response_error(
            "图片结果地址被安全策略拒绝。", "IMAGE_RESULT_URL_DENIED"
        ) from None


def resolve_url(url, contract, deadline, *, protected_credentials=()):
    from . import transport

    if contract.format != "url":
        raise response_error("图片结果 URL 未显式启用。", "IMAGE_RESULT_URL_DENIED")
    deadline = min(deadline, time.monotonic() + RESULT_TIMEOUT)
    seen = set()
    for hop in range(MAX_REDIRECTS + 1):
        target = _url_target(url, protected_credentials)
        if url in seen:
            raise response_error("图片结果重定向过多。", "IMAGE_RESULT_REDIRECT_LIMIT")
        seen.add(url)
        watchdog = transport._SocketWatchdog(deadline)
        connection = response = None
        try:
            watchdog.arm(transport.CONNECT_TIMEOUT)
            address = _validated_address(target, contract, watchdog)
            connection = transport._connect(target, watchdog, address=address)
            watchdog.arm()
            connection.putrequest(
                "GET",
                (target.path or "/") + ("?" + target.query if target.query else ""),
                skip_accept_encoding=True,
            )
            for name, value in {
                "Accept": "image/png, image/jpeg, image/webp",
                "Accept-Encoding": "identity",
                "Connection": "close",
            }.items():
                connection.putheader(name, value)
            watchdog.sock.settimeout(watchdog.remaining())
            connection.endheaders()
            response = connection.getresponse()
            watchdog.remaining()
            if response.status in {301, 302, 303, 307, 308}:
                locations = response.headers.get_all("Location", [])
                if hop == MAX_REDIRECTS:
                    raise response_error(
                        "图片结果重定向过多。", "IMAGE_RESULT_REDIRECT_LIMIT"
                    )
                if len(locations) != 1:
                    raise response_error()
                location = locations[0]
                if (
                    not location
                    or len(location) > 8192
                    or any(ord(c) <= 32 or ord(c) >= 127 or c == "\\" for c in location)
                ):
                    raise response_error()
                url = urljoin(url, location)
                continue  # No redirect body, cookie jar or inherited headers.
            if response.status != 200:
                raise response_error(
                    "图片结果下载失败，未自动重试。", "IMAGE_RESULT_FETCH_FAILED"
                )
            return transport._read_response(
                response, watchdog, max_bytes=transport.MAX_OUTPUT_BYTES, raster=True
            )
        except (ImageError, OSError, HTTPException):
            # A watchdog shutdown can surface as EOF/BadStatusLine, not only
            # socket.timeout. Preserve the tighter per-result/per-connect cap.
            if watchdog.expired or time.monotonic() >= watchdog.phase_deadline:
                raise TimeoutError() from None
            raise
        finally:
            watchdog.close()
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
    raise response_error()


def _normalize_bounded(raw, deadline):
    from . import transport

    if not raw or len(raw) > transport.MAX_OUTPUT_BYTES:
        raise response_error()
    remaining = min(DECODE_TIMEOUT, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError()
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _DECODE_CODE,
                str(Path(__file__).resolve().parents[3]),
                str(transport.MAX_OUTPUT_BYTES),
                str(transport.MAX_OUTPUT_PIXELS),
            ],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            timeout=remaining,
            check=True,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError() from None
    except subprocess.CalledProcessError:
        raise response_error() from None
    if time.monotonic() >= deadline:
        raise TimeoutError()
    output = child.stdout
    if not 9 < len(output) <= transport.MAX_OUTPUT_BYTES + 9:
        raise response_error()
    width, height, kind = struct.unpack("!IIB", output[:9])
    if (
        not width
        or not height
        or width * height > transport.MAX_OUTPUT_PIXELS
        or kind > 2
    ):
        raise response_error()
    return {
        "b64_json": base64.b64encode(output[9:]).decode("ascii"),
        "mime": ("image/png", "image/jpeg", "image/webp")[kind],
        "width": width,
        "height": height,
    }


def _usage(value):
    if not isinstance(value, dict):
        return None

    def counts(source, allowed):
        return {
            key: val
            for key, val in source.items()
            if key in allowed and type(val) is int and 0 <= val <= 2**63 - 1
        }

    result = counts(
        value,
        {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
        },
    )
    for name in (
        "input_tokens_details",
        "output_tokens_details",
        "prompt_tokens_details",
        "completion_tokens_details",
    ):
        if isinstance(value.get(name), dict):
            details = counts(
                value[name],
                {"text_tokens", "image_tokens", "cached_tokens", "reasoning_tokens"},
            )
            if details:
                result[name] = details
    return result or None


def _decode_result(result, contract, *, deadline, secrets, protected_credentials):
    from . import transport

    if not isinstance(result, dict):
        raise response_error()
    if "error" in result or result.get("success") is False:
        error = response_error()
        error.details = error_details(result, 200, secrets=secrets)
        raise error
    data = result.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise response_error()
    encoded = data[0].get("b64_json")
    if encoded:
        if (
            not isinstance(encoded, str)
            or len(encoded) > (transport.MAX_OUTPUT_BYTES + 2) // 3 * 4 + 32
        ):
            raise response_error()
        # Only a simple data URL wrapper is tolerated; MIME is determined by pixels.
        encoded = re.sub(
            r"\Adata:image/(?:png|jpeg|webp);base64,", "", encoded, count=1
        )
        if len(encoded) > (transport.MAX_OUTPUT_BYTES + 2) // 3 * 4:
            raise response_error()
        try:
            raw = base64.b64decode(encoded, validate=True)
            if base64.b64encode(raw).decode("ascii") != encoded:
                raise ValueError()
        except ValueError:
            raise response_error() from None
    elif isinstance(data[0].get("url"), str) and data[0]["url"]:
        if contract.format != "url":
            raise response_error(
                "New API 仅返回图片链接，未返回图片数据；请配置该型号的 Base64 响应兼容选项。未自动重试。",
                "IMAGE_URL_ONLY_RESPONSE",
            )
        raw = resolve_url(
            data[0]["url"],
            contract,
            deadline,
            protected_credentials=protected_credentials,
        )
    else:
        raise response_error()
    response = {"images": [_normalize_bounded(raw, deadline)]}
    usage = _usage(result.get("usage"))
    if usage is not None:
        response["usage"] = usage
    identifier = _body_request_id(result, secrets)
    if identifier:
        response["upstream_request_id"] = identifier
    return response


def decode_response(body, contract, *, deadline, secrets=(), protected_credentials=()):
    """Filter diagnostics with secrets; URL checks protect only credentials.

    Prompt text must not participate in URL substring rejection: ordinary short
    prompts can coincide with a legitimate CDN host, path or signed query.
    """
    result = parse_json(body)
    identifier = _body_request_id(result, secrets)
    try:
        return _decode_result(
            result,
            contract,
            deadline=deadline,
            secrets=secrets,
            protected_credentials=protected_credentials,
        )
    except ImageError as exc:
        exc.details["upstream_request_id"] = (
            exc.details["upstream_request_id"] or identifier
        )
        exc.details = safe_details(exc.details, secrets)
        raise
    except (OSError, HTTPException) as exc:
        timed_out = isinstance(exc, TimeoutError) or time.monotonic() >= deadline
        raise ImageError(
            "图片结果处理超时，未自动重试。"
            if timed_out
            else "图片结果连接或响应异常，未自动重试。",
            504 if timed_out else 502,
            "IMAGE_TIMEOUT" if timed_out else "IMAGE_CONNECTION_ERROR",
            uncertain=True,
            details={"upstream_request_id": identifier},
        ) from None
