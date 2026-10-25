"""Own-token tutorials: delegate ownership to New API, without PAT provisioning.

Only fixed token-management and model-list endpoints are called. No generation,
database writes, key persistence, automatic retries or user-supplied upstreams.
"""

from http.client import HTTPConnection, HTTPSConnection

from .integrations.new_api import json_client, personal_tokens
from .integrations.new_api.token_provider import TokenProvider
from .integrations.new_api.personal_tokens import TokenError as ManualError  # noqa: F401 -- public exception alias
from .platform.identity import Principal, RequestAccess


def upstream(path, credential, user_id, *, body=None, model_key=False):
    return json_client.upstream(
        path,
        credential,
        user_id,
        body=body,
        model_key=model_key,
        connection_types=(HTTPConnection, HTTPSConnection),
    )


# Resolve the transport at call time, preserving bounded client/test injection.
_provider = TokenProvider(lambda *args, **kwargs: upstream(*args, **kwargs))


def _access(user, credential):
    return RequestAccess(
        Principal(user["id"], user.get("username", ""), role=user.get("role", 1)),
        credential,
    )


def token_id(body):
    return personal_tokens.token_id(body)


def token_summary(row, user):
    return personal_tokens.summary(row, user)


def list_tokens(user, credential):
    return {
        "tokens": _provider.list_tokens(_access(user, credential)),
        "token_manager_path": "/keys",
    }


def reveal_key(user, credential, body):
    return personal_tokens.key_for(user, credential, body, upstream=upstream)


def models(user, credential, body):
    return {"models": _provider.list_models(_access(user, credential), token_id(body))}
