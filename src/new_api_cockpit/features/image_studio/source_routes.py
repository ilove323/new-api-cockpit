"""Restricted read-only image routing projections, with an injected SourceDb."""

import hashlib
import json
import re

import psycopg
from psycopg import sql

from .ports import StudioError


def _source_columns(conn, table, names):
    """Resolve the visible table, and distinguish absent columns from no SELECT.

    information_schema hides unprivileged columns; the catalog existence check
    prevents treating a missing grant as an old-version default. Neither query
    reads source data or discovers credential columns.
    """
    params = (table, list(names))
    actual = {
        r["column_name"]
        for r in conn.execute(
            "SELECT attname AS column_name FROM pg_catalog.pg_attribute "
            "WHERE attrelid=to_regclass(%s) AND attname=ANY(%s) "
            "AND attnum>0 AND NOT attisdropped",
            params,
        ).fetchall()
    }
    readable = {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE to_regclass(format('%%I.%%I',table_schema,table_name))=to_regclass(%s) "
            "AND column_name=ANY(%s) "
            "AND has_column_privilege(format('%%I.%%I',table_schema,table_name),column_name,'SELECT')",
            params,
        ).fetchall()
    }
    if actual - readable:
        raise StudioError(
            "源库角色缺少图片路由配置列的 SELECT 权限。", 503, "IMAGE_CONTRACT_SOURCE"
        )
    return actual


def _gateway_policy(conn):
    # rc.40 also merges affinity templates into ParamOverride. Read only paths
    # and whether a template exists, never its values/headers/credentials.
    row = conn.execute(
        "SELECT (SELECT value FROM options WHERE key='global.pass_through_request_enabled') AS pass_through,"
        "(SELECT value FROM options WHERE key='channel_affinity_setting.enabled') AS affinity_enabled,"
        "(SELECT jsonb_agg(jsonb_build_object('paths',r->'path_regex','override',"
        "COALESCE(r->'param_override_template','{}'::jsonb) NOT IN ('{}'::jsonb,'null'::jsonb))) "
        "FROM jsonb_array_elements(COALESCE((SELECT NULLIF(value,'')::jsonb FROM options "
        "WHERE key='channel_affinity_setting.rules'),'[]'::jsonb)) AS r) AS affinity_rules"
    ).fetchone()
    if any(
        row[k] not in (None, "true", "false")
        for k in ("pass_through", "affinity_enabled")
    ):
        raise StudioError("图片网关全局开关无效。", 503, "IMAGE_CONTRACT_SOURCE")
    rules = row["affinity_rules"] or []
    if not isinstance(rules, list) or len(rules) > 256:
        raise StudioError("图片网关亲和规则无效。", 503, "IMAGE_CONTRACT_SOURCE")
    safe = True
    for rule in rules:
        if not isinstance(rule, dict) or type(rule.get("override")) is not bool:
            raise StudioError("图片网关亲和规则无效。", 503, "IMAGE_CONTRACT_SOURCE")
        if not rule["override"]:
            continue
        paths = rule.get("paths")
        # Do not execute arbitrary administrator regex with Python backtracking.
        # Only literal non-image endpoint exclusions are provably safe here.
        if (
            not isinstance(paths, list)
            or not paths
            or any(
                not isinstance(path, str)
                or not re.fullmatch(r"\^?/v1/[a-z/]+\$?", path)
                or any(
                    path.strip("^$") in endpoint
                    for endpoint in ("/v1/images/edits", "/v1/images/generations")
                )
                for path in paths
            )
        ):
            safe = False
    # Absent DB rules use rc.40 defaults restricted to /responses and /messages.
    enabled = row["affinity_enabled"] != "false"
    return {
        "global_pass_through_request_enabled": row["pass_through"] == "true",
        "no_affinity_override": not enabled or safe,
        "affinity_revision": hashlib.sha256(
            json.dumps({"enabled": enabled, "rules": rules}, sort_keys=True).encode()
        ).hexdigest(),
    }


def load_routes(source_db):
    """Read every GPT priority/retry path with explicit, quoted column access.

    Do not select c.*, to_jsonb(c), KEY, raw header/setting/proxy values. Only
    optional known columns found by metadata inspection enter this SQL. Absent
    rc.40 pass-through flags/options have the upstream's false default.
    """
    try:
        with source_db.read_connection() as conn:
            gateway_policy = _gateway_policy(conn)
            columns = _source_columns(
                conn,
                "channels",
                {
                    "model_mapping",
                    "base_url",
                    "setting",
                    "settings",
                    "param_override",
                    "header_override",
                },
            )
            ability_columns = _source_columns(conn, "abilities", {"priority", "weight"})

            def column(name):
                return sql.Identifier("c", name) if name in columns else sql.SQL("NULL")

            def json_object(name):
                return sql.SQL(
                    "COALESCE(NULLIF({}::text,'')::jsonb,'{{}}'::jsonb)"
                ).format(column(name))

            projections = [
                sql.SQL("a.model"),
                sql.SQL("a.channel_id"),
                sql.SQL("c.type"),
            ]
            expressions = {
                "model_mapping": column("model_mapping"),
                "base_url_revision": sql.SQL("md5(COALESCE({}::text,''))").format(
                    column("base_url")
                ),
                "settings_revision": sql.SQL(
                    "md5(({} - 'task_plugin_key')::text || COALESCE({}::text,''))"
                ).format(json_object("setting"), column("settings")),
                "pass_through_body_enabled": sql.SQL(
                    "{}->'pass_through_body_enabled'"
                ).format(json_object("setting")),
                "no_param_override": sql.SQL("{}='{{}}'::jsonb").format(
                    json_object("param_override")
                ),
                "no_header_override": sql.SQL("{}='{{}}'::jsonb").format(
                    json_object("header_override")
                ),
                **{
                    name: sql.Identifier("a", name)
                    if name in ability_columns
                    else sql.SQL("NULL")
                    for name in ("priority", "weight")
                },
            }
            projections.extend(
                sql.SQL("{} AS {}").format(expr, sql.Identifier(name))
                for name, expr in expressions.items()
            )
            query = sql.SQL(
                "SELECT {} FROM abilities a JOIN channels c ON c.id=a.channel_id "
                'WHERE a."group"=%s AND a.model LIKE %s AND a.enabled=true AND c.status=1 '
                "ORDER BY a.model,a.channel_id LIMIT 10001"
            ).format(sql.SQL(",").join(projections))
            rows = conn.execute(query, ("GPT", "gpt-image-%")).fetchall()
    except psycopg.errors.InsufficientPrivilege:
        raise StudioError(
            "源库角色需要图片路由配置和指定 options 的只读权限。",
            503,
            "IMAGE_CONTRACT_SOURCE",
        ) from None
    except psycopg.Error:
        raise StudioError(
            "图片路由配置读取失败，请检查源库连接及 JSON 设置。",
            503,
            "IMAGE_CONTRACT_SOURCE",
        ) from None
    if len(rows) > 10000:
        raise StudioError("图片渠道目录过大，未返回不完整清单。", 503)
    for row in rows:
        row.update(gateway_policy)
        channel_flag = row.get("pass_through_body_enabled")
        if channel_flag is not None and type(channel_flag) is not bool:
            raise StudioError(
                "图片渠道透传设置无效。", 503, "IMAGE_CONTRACT_SOURCE"
            ) from None
        row["pass_through_body_enabled"] = channel_flag is True
    return rows
