"""Verified request identities; credentials are never serializable state."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str = ""
    role: int = 1
    session_id: str | None = None
    expires_at: int | None = None
    auth_kind: str = "session"

    def __post_init__(self):
        if type(self.user_id) is not int or not 1 <= self.user_id <= 2147483647:
            raise ValueError("Invalid verified user")
        if self.auth_kind not in {"session", "pat"}:
            raise ValueError("Invalid authentication kind")

    def as_user(self):
        return {
            "id": self.user_id,
            "username": self.username,
            "role": self.role,
            "session_id": self.session_id,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class RequestAccess:
    principal: Principal
    credential: str = field(repr=False)
