"""Explicit session-only chat contract. Documentation never auto-runs these APIs."""

import re


def _obj(fields):
    return {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }


UUID = {"type": "string", "format": "uuid"}
INT = {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
STRING = {"type": "string"}
CONTENT = {"type": "string", "description": "UTF-8正文；不接受凭据，不执行代码。"}
REV = {"context_revision": INT, "expected_revision": INT}
RUN = _obj(
    {
        "client_request_id": UUID,
        "expected_revision": INT,
        "token_id": {"type": "integer", "minimum": 1, "maximum": 2147483647},
        "model": STRING,
        "capability_revision": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
        "context_revision": INT,
        "skill_ids": {
            "type": "array",
            "items": UUID,
            "maxItems": 3,
            "uniqueItems": True,
        },
        "mode": {
            "type": "string",
            "enum": ["chat", "files"],
            "description": "界面默认 files 完整助手，chat 仅作旧 API 无工具请求兼容。两者均由服务器读取引用附件正文；不要求逐模型工具声明。",
        },
        "text": CONTENT,
        "attachment_ids": {
            "type": "array",
            "description": "仅当前聊天的文件 ID；两种模式均可引用。附件正文与历史一起计入上下文预算，过大在模型调用前拒绝，不截断。",
            "items": UUID,
            "maxItems": 8,
            "uniqueItems": True,
        },
    }
)
RUN["properties"].update(
    {
        "candidate_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 4,
            "default": 1,
        },
        "web_enabled": {"type": "boolean", "default": False},
        "auto_memory": {"type": "boolean", "default": False},
        "context_strategy": {
            "type": "string",
            "enum": ["recent", "adaptive"],
            "default": "recent",
        },
    }
)


def paths():
    root = "/cockpit/api/chat"
    entries = [
        ("/config", "get", "客户端限制", None),
        ("/tokens", "get", "本人KEY摘要", None),
        (
            "/models",
            "post",
            "所选KEY模型能力",
            _obj({"token_id": {"type": "integer", "minimum": 1}}),
        ),
        ("/conversations", "get", "本人全部聊天分页", None),
        (
            "/conversations",
            "post",
            "创建空聊天",
            _obj({"conversation_id": UUID, "title": STRING}),
        ),
        ("/conversations/{cid}", "get", "本人聊天", None),
        (
            "/conversations/{cid}",
            "patch",
            "重命名聊天或保存持续载入的 Skills",
            {
                "oneOf": [
                    _obj({"title": STRING, "expected_revision": INT}),
                    _obj(
                        {
                            **REV,
                            "loaded_skill_ids": {
                                "type": "array",
                                "items": UUID,
                                "maxItems": 3,
                                "uniqueItems": True,
                                "description": "当前聊天持续使用的有序 Skills；空数组移除全部，不删除个人库或历史快照。",
                            },
                        }
                    ),
                ]
            },
        ),
        (
            "/conversations/{cid}",
            "delete",
            "删除聊天及其文件",
            _obj(
                {
                    "expected_revision": INT,
                    "confirm": {"type": "boolean", "const": True},
                }
            ),
        ),
        ("/conversations/{cid}/events", "get", "已提交完整历史事件分页", None),
        (
            "/conversations/{cid}/sync",
            "get",
            "单次恢复聊天状态、事件和变化的文件列表，不续跑生成",
            None,
        ),
        ("/conversations/{cid}/files", "get", "本聊天文件及所有输出版本", None),
        (
            "/conversations/{cid}/files",
            "post",
            "上传文本、PDF、Word、Excel或图片附件（单文件不超过50 MiB）",
            _obj(
                {"client_file_id": UUID, "file": {"type": "string", "format": "binary"}}
            ),
        ),
        ("/conversations/{cid}/files/{fid}/download", "get", "本人聊天附件下载", None),
        (
            "/conversations/{cid}/files/{fid}",
            "delete",
            "删除本人上传附件；历史保留文件名和删除标记，不撤回已发送内容",
            _obj({"confirm": {"type": "boolean", "const": True}}),
        ),
        ("/conversations/{cid}/runs", "post", "发起有限收费轮次；永不自动重试", RUN),
        ("/conversations/{cid}/runs/{rid}", "get", "查看运行状态，不续跑", None),
        (
            "/conversations/{cid}/runs/{rid}/candidate",
            "post",
            "选择本轮候选回答供后续上下文使用",
            _obj(
                {
                    "expected_revision": INT,
                    "candidate_index": {"type": "integer", "minimum": 0, "maximum": 3},
                }
            ),
        ),
        (
            "/conversations/{cid}/runs/{rid}/memory/undo",
            "post",
            "撤销本人自动记忆更新；后续手工编辑优先",
            _obj({"context_revision": INT, "expected_revision": INT}),
        ),
        (
            "/conversations/{cid}/runs/{rid}/cancel",
            "post",
            "请求停止，不撤销上游计费",
            _obj({}),
        ),
        ("/context", "get", "本人三份个人文档与Skill摘要", None),
        (
            "/profiles/{name}",
            "patch",
            "替换本人的MEMORY/SOUL/IDENTITY",
            _obj({**REV, "content": CONTENT}),
        ),
        ("/skills", "get", "本人Skill目录", None),
        (
            "/skills",
            "post",
            "创建单文件提示Skill",
            _obj({"context_revision": INT, "content": CONTENT}),
        ),
        ("/skills/{sid}", "get", "查看本人Skill正文", None),
        ("/skills/{sid}", "patch", "编辑本人Skill", _obj({**REV, "content": CONTENT})),
        (
            "/skills/{sid}",
            "delete",
            "删除本人Skill，历史快照不变",
            _obj({**REV, "confirm": {"type": "boolean", "const": True}}),
        ),
        (
            "/conversations/{cid}/runs/{rid}/context",
            "get",
            "本轮实际个人上下文快照",
            None,
        ),
    ]
    result = {}
    for suffix, method, summary, body in entries:
        parameters = [
            {
                "in": "header",
                "name": "X-Cockpit-Session",
                "required": True,
                "schema": STRING,
            }
        ]
        for name in re.findall(r"\{([^}]+)\}", suffix):
            schema = (
                {"type": "string", "enum": ["MEMORY.md", "SOUL.md", "IDENTITY.md"]}
                if name == "name"
                else UUID
            )
            parameters.append(
                {"in": "path", "name": name, "required": True, "schema": schema}
            )
        if suffix == "/conversations" and method == "get":
            parameters.extend(
                [
                    {
                        "in": "query",
                        "name": "limit",
                        "schema": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    {"in": "query", "name": "cursor", "schema": STRING},
                ]
            )
        if suffix.endswith("/events"):
            parameters.extend(
                {"in": "query", "name": name, "schema": INT}
                for name in ("after_seq", "until_seq", "limit")
            )
        if suffix.endswith("/sync"):
            parameters.extend(
                {"in": "query", "name": name, "schema": INT}
                for name in ("after_seq", "limit", "context_revision")
            )
            parameters.append(
                {
                    "in": "query",
                    "name": "files_revision",
                    "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                }
            )
        if suffix == "/skills/{sid}" and method == "get":
            parameters.append(
                {"in": "query", "name": "context_revision", "schema": INT}
            )
        headers = {"X-Cockpit-Chat": "1"} if method != "get" else {}
        for name, value in headers.items():
            parameters.append(
                {
                    "in": "header",
                    "name": name,
                    "required": True,
                    "schema": {"type": "string", "const": value},
                }
            )
        response = {
            "description": "本人范围的结果；不返回原始KEY。",
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
        operation = {
            "operationId": "chat_"
            + method
            + "_"
            + re.sub(r"[^a-zA-Z0-9]+", "_", suffix).strip("_"),
            "summary": summary,
            "tags": ["AI助手"],
            "parameters": parameters,
            "description": "仅本人官方登录会话/JWT，强制会话stamp；管理员PAT不支持，管理员也不能查看他人内容。历史不依赖旧KEY。文档不开放试运行。",
            "security": [{"browserSession": []}, {"browserJWT": []}],
            "x-cockpit-effect": "read"
            if method == "get" or suffix == "/models"
            else "write",
            "x-cockpit-testable": False,
            "x-cockpit-pat-only": False,
            "x-cockpit-confirm-headers": headers,
            "responses": {"200": response},
        }
        if body is not None:
            mime = (
                "multipart/form-data"
                if suffix.endswith("/files")
                else "application/json"
            )
            operation["requestBody"] = {
                "required": True,
                "content": {mime: {"schema": body}},
            }
        if suffix.endswith("/runs") and method == "post":
            response["content"]["text/event-stream"] = {
                "schema": {
                    "type": "string",
                    "description": "已持久化事件流；重复请求返回原run JSON，不重新生成。",
                }
            }
        if suffix.endswith("/download"):
            response["content"] = {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        if suffix == "/conversations/{cid}" and method == "delete":
            operation["responses"] = {"204": {"description": "已明确删除"}}
        if suffix == "/conversations/{cid}" and method in {"get", "patch"}:
            operation["description"] += (
                " conversation.loaded_skill_ids 为持久载入顺序，旧聊天默认空数组。"
                " 保存 Skills 不生成模型回答、不计费；需要聊天与个人设置版本，"
                "运行中或版本冲突时拒绝，不自动覆盖。发送和刷新不清空载入状态。"
            )
        if method == "post" and suffix in {
            "/conversations",
            "/skills",
            "/conversations/{cid}/files",
        }:
            operation["responses"]["201"] = response
        if suffix.endswith("/cancel"):
            operation["responses"]["202"] = {
                "description": "停止请求已接受，不保证未计费",
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        for code in (
            400,
            401,
            403,
            404,
            409,
            410,
            413,
            415,
            422,
            429,
            502,
            503,
            504,
            507,
        ):
            operation["responses"][str(code)] = {
                "description": "请求拒绝或执行失败；不自动重试收费操作。",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Error"}
                    }
                },
            }
        result.setdefault(root + suffix, {})[method] = operation
    return result
