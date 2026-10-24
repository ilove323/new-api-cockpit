"""External capabilities supplied by the composition root, never by a feature."""

from contextlib import AbstractContextManager
import re
from typing import Protocol

from ...platform.identity import RequestAccess


class TokenProvider(Protocol):
    """Model policy/catalogue errors use safe ``reason`` diagnostic identifiers.

    ``model_limits_enabled``, ``model_limits`` and ``model_catalogue`` let this
    feature preserve its original public errors. Ownership/key errors retain
    the adapter's ordinary status/code and do not get reclassified.
    """

    def list_tokens(self, access: RequestAccess, *, group=None) -> list[dict]: ...
    def routing_groups(self, access: RequestAccess, token_id: int) -> list[str]: ...
    def list_models(
        self, access: RequestAccess, token_id: int, *, group=None
    ) -> list[dict]: ...
    def lease_key(
        self, access: RequestAccess, token_id: int, *, group=None
    ) -> AbstractContextManager[str]: ...


class SourceDb(Protocol):
    def read_connection(self) -> AbstractContextManager: ...


class TokenSelectionError(ValueError):
    """Preserve the old selector's public error shape without an adapter import."""

    status = 400
    code = "MANUAL_ERROR"


class StudioError(ValueError):
    def __init__(
        self,
        message,
        status=400,
        code="IMAGE_STUDIO_ERROR",
        uncertain=False,
        details=None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.uncertain = bool(uncertain)
        self.details = _safe_details(details)


def _safe_details(details):
    """Keep only bounded, non-secret diagnostic identifiers from the transport."""
    if not isinstance(details, dict):
        return {}
    result = {}
    for key in ("category", "provider_code", "param", "upstream_request_id"):
        value = details.get(key)
        if (
            isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_.:/\[\]-]{1,128}", value)
            and not value.lower().startswith(("sk-", "bearer"))
        ):
            result[key] = value
    status = details.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    return result


def token_id(body):
    # Same bounded token selector as the former owner-only primitive. Identity
    # and ownership are still rechecked by the injected TokenProvider.
    if (
        not isinstance(body, dict)
        or set(body) != {"token_id"}
        or type(body["token_id"]) is not int
        or not 1 <= body["token_id"] <= 2147483647
    ):
        raise TokenSelectionError("请选择有效的令牌；不接受用户 ID 或上游地址。")
    return body["token_id"]
