"""Short-lived dispatch receipts, not jobs or an image/result store.

Only an atomic first claim permits a paid POST. Never release a claim after a
transport error: even a connection failure can follow a partially sent request.
Redis must use AOF/always and noeviction. A changed server epoch fails closed
until the worker restarts; persistence/TTL remain the boundary of deduplication.
"""

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
from threading import Lock
import time
from urllib.parse import urlsplit

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

_guard = Lock()
_epochs = {}
_failed = set()
_CLAIM = """
local current = redis.call('GET', KEYS[1])
if current then return current end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return ''
"""
_FINISH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
return 1
"""


class ReceiptError(ValueError):
    def __init__(
        self,
        message,
        *,
        code="IMAGE_RECEIPTS_UNAVAILABLE",
        status=503,
        uncertain=False,
        details=None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.uncertain = uncertain
        self.details = details or {"category": "receipts"}


def _settings():
    url = os.environ.get("IMAGE_STUDIO_REDIS_URL", "")
    namespace = os.environ.get(
        "IMAGE_STUDIO_RECEIPT_NAMESPACE", "cockpit:image-receipts:v1"
    )
    try:
        parsed = urlsplit(url)
        ttl = int(os.environ.get("IMAGE_STUDIO_RECEIPT_TTL", "86400"))
        if (
            parsed.scheme not in {"redis", "rediss"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/[0-9]{1,2}", parsed.path)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{8,96}", namespace)
            or not 3600 <= ttl <= 604800
        ):
            raise ValueError()
        parsed.port
    except ValueError:
        raise ReceiptError("图片请求回执存储未配置，请联系管理员。") from None
    return url, namespace, ttl


def _client(url):
    # URL query parameters cannot override these bounds or enable retries.
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
        max_connections=4,
        protocol=2,
        health_check_interval=0,
    )


def _ready(client, identity):
    server = client.info("server")
    persistence = client.info("persistence")
    config = client.config_get("maxmemory-policy", "appendfsync")
    epoch = server.get("run_id")
    if (
        not isinstance(epoch, str)
        or not epoch
        or persistence.get("aof_enabled") != 1
        or persistence.get("aof_last_write_status") != "ok"
        or config.get("maxmemory-policy") != "noeviction"
        or config.get("appendfsync") != "always"
    ):
        raise ReceiptError("图片请求回执需要 AOF always、noeviction 的 Redis。")
    with _guard:
        if identity in _failed or (_epochs.get(identity, epoch) != epoch):
            _failed.add(identity)
            raise ReceiptError("回执存储已重启，请重启图片请求服务后再试。")
        _epochs[identity] = epoch


def health():
    try:
        url, namespace, _ = _settings()
        with _client(url) as client:
            _ready(client, hashlib.sha256((url + namespace).encode()).digest())
        return True
    except (ReceiptError, redis.RedisError, OSError, ValueError):
        return False


def digest_request(body, *, images=None, mask=None):
    """Canonical input identity; hash file bytes without retaining another copy."""
    digest = hashlib.sha256()

    def add(value):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    add(
        json.dumps(
            body,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    add(b"edit" if images is not None else b"generate")
    for image in images or []:
        add(image["data"])
    add(b"mask" if mask is not None else b"no-mask")
    if mask is not None:
        add(mask["data"])
    return digest.hexdigest()


@dataclass(frozen=True, repr=False)
class Receipt:
    url: str
    key: str
    claimed: str

    def finish(self, state):
        if state not in {"succeeded", "failed", "unknown"}:
            raise ValueError("Invalid receipt state")
        value = json.loads(self.claimed)
        value.update(state=state, ended_at=int(time.time()))
        try:
            with _client(self.url) as client:
                result = client.eval(
                    _FINISH,
                    1,
                    self.key,
                    self.claimed,
                    json.dumps(value, separators=(",", ":")),
                )
            if result != 1:
                raise ReceiptError("回执无法更新。")
        except (redis.RedisError, ReceiptError, OSError, ValueError):
            # Keep the dispatch receipt. A known image result must not be lost
            # just because updating its terminal metadata failed.
            logging.getLogger(__name__).warning(
                "Studio receipt finalization unavailable"
            )
            return False
        return True


def claim(user_id, request_id, digest):
    if (
        type(user_id) is not int
        or not 1 <= user_id <= 2147483647
        or not isinstance(request_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ReceiptError("请求回执标识无效。", status=400)
    url, namespace, ttl = _settings()
    key = f"{namespace}:{user_id}:" + hashlib.sha256(request_id.encode()).hexdigest()
    value = json.dumps(
        {
            "owner_id": user_id,
            "digest": digest,
            "state": "dispatched",
            "started_at": int(time.time()),
        },
        separators=(",", ":"),
    )
    try:
        with _client(url) as client:
            _ready(client, hashlib.sha256((url + namespace).encode()).digest())
            previous = client.eval(_CLAIM, 1, key, value, ttl)
        if previous:
            if not isinstance(previous, str) or len(previous) > 2048:
                raise ValueError()
            previous = json.loads(previous)
            if not isinstance(previous, dict):
                raise ValueError()
            if previous.get("digest") != digest:
                raise ReceiptError(
                    "此请求 ID 已用于其他输入，请重新发起。",
                    status=409,
                    code="IMAGE_REQUEST_CONFLICT",
                )
            state = previous.get("state")
            if state not in {"dispatched", "succeeded", "failed", "unknown"}:
                raise ValueError()
            raise ReceiptError(
                "此请求已提交，不会重复生成。",
                status=409,
                code="IMAGE_REQUEST_DUPLICATE",
                uncertain=state in {"dispatched", "unknown"},
                details={"category": "duplicate", "receipt_state": state},
            )
    except (redis.RedisError, OSError, ValueError) as exc:
        if isinstance(exc, ReceiptError):
            raise
        raise ReceiptError("图片请求回执暂不可用，未发送图片请求。") from None
    return Receipt(url, key, value)
