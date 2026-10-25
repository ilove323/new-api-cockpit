"""Composition root: connect host identity and external adapters to features."""

import threading

from .features.image_studio import http as image_http
from .features.image_studio import service as image_service
from .integrations.new_api.source_db import SourceDb
from .integrations.new_api.token_provider import TokenProvider

_chat_services = {}
_chat_lock = threading.Lock()


class VerifiedSession:
    def __init__(self, verify):
        self.verify = verify

    def revalidate(self, access):
        from .features.chat.errors import ChatError
        from .auth import LoginRequired, LoginForbidden

        try:
            user = self.verify(access.credential, allow_user=True)
        except LoginRequired:
            raise ChatError(
                "登录会话已失效或暂不可验证。", 401, "AUTH_REQUIRED"
            ) from None
        except LoginForbidden:
            raise ChatError("当前登录账号不可用。", 403, "AUTH_FORBIDDEN") from None
        except Exception:
            raise ChatError("登录服务暂不可验证。", 503, "AUTH_UNAVAILABLE") from None
        if (
            user["id"] != access.principal.user_id
            or user.get("session_id") != access.principal.session_id
        ):
            raise ChatError("登录账号已变化。", 409, "AUTH_SESSION_CHANGED")
        return access


def get_chat_service(verify):
    from .features.chat.config import Settings
    from .features.chat.errors import ChatError
    from .features.chat.service import ChatService

    settings = Settings.from_env()
    if not settings.enabled:
        raise ChatError("聊天功能未启用。", 404, "CHAT_NOT_FOUND")
    with _chat_lock:
        if settings not in _chat_services:
            _chat_services[settings] = ChatService(
                settings, TokenProvider(), VerifiedSession(verify)
            )
        return _chat_services[settings]


def register_chat(app, host, verify):
    from .features.chat.http import create_blueprint

    app.register_blueprint(create_blueprint(host, lambda: get_chat_service(verify)))


def shutdown_chat_services():
    with _chat_lock:
        services = list(_chat_services.values())
    for service in services:
        service.shutdown(timeout=20)


def register_image(app, host):
    image_service.configure(TokenProvider(), SourceDb())
    app.register_blueprint(image_http.create_blueprint(host))
