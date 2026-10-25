"""MCP identity projection over the shared, fixed-origin account transport."""

from ...integrations.new_api import account
from ...integrations.new_api.account import AccountError, NoRedirect, read, request  # noqa: F401


def authenticate(credential):
    return account.public_user(read("/api/user/self", credential))
