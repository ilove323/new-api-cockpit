"""Chat HTTP boundary; authorization belongs to the verified host context."""

from io import BytesIO
import json
import re

from flask import Blueprint, jsonify, render_template, request, Response, send_file
from werkzeug.exceptions import HTTPException

from . import config, schemas, web_tools
from .errors import ChatError
from .user_context import validate_credentials

PAGE_ENDPOINTS = frozenset({"chat.page", "chat.settings_page"})
API_ENDPOINTS = frozenset(
    "chat." + name
    for name in (
        "config",
        "tokens",
        "models",
        "conversations",
        "conversation",
        "events",
        "files",
        "file_download",
        "runs",
        "run",
        "cancel_run",
        "select_candidate",
        "undo_memory",
        "context_get",
        "profile_update",
        "skills_list",
        "skill_create",
        "skill_get",
        "skill_update",
        "skill_delete",
        "run_context_get",
    )
)
USER_ENDPOINTS = PAGE_ENDPOINTS | API_ENDPOINTS
ROLE_ENDPOINTS = {"web": PAGE_ENDPOINTS, "chat": API_ENDPOINTS, "image": frozenset()}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def json_body(host, *, settings=False):
    host.validate_write("X-Cockpit-Chat", "application/json")
    request.max_content_length = (
        config.SETTINGS_REQUEST_BYTES if settings else config.JSON_REQUEST_BYTES
    )
    try:
        result = json.loads(
            request.get_data(cache=False),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ChatError("请求必须是有效的JSON对象，不能有重复字段。") from None
    if not isinstance(result, dict):
        raise ChatError()
    return result


def query(allowed=()):
    if set(request.args) - set(allowed) or any(
        len(request.args.getlist(k)) != 1 for k in request.args
    ):
        raise ChatError("查询参数无效。")
    result = request.args.to_dict()
    for key in ("limit", "after_seq", "until_seq", "context_revision"):
        if key in result:
            if not re.fullmatch(r"0|[1-9][0-9]{0,15}", result[key]):
                raise ChatError("分页数值无效。")
            result[key] = schemas.integer(int(result[key]))
    return result


def create_blueprint(host, service_factory):
    blueprint = Blueprint("chat", __name__)

    def service():
        return service_factory()

    def scope(cid):
        return host.access().principal, schemas.identifier(cid)

    @blueprint.get("/cockpit/chat", endpoint="page")
    @blueprint.get("/cockpit/chat/", endpoint="page")
    def page():
        return render_template("chat/index.html", site_name=host.site_name())

    @blueprint.get("/cockpit/chat/settings", endpoint="settings_page")
    @blueprint.get("/cockpit/chat/settings/", endpoint="settings_page")
    def settings_page():
        return render_template("chat/settings.html", site_name=host.site_name())

    @blueprint.get("/cockpit/api/chat/config", endpoint="config")
    def public_config():
        query()
        return jsonify(
            schema_version=1,
            limits=config.public_limits(),
            allowed_file_extensions=sorted(config.ALLOWED_EXTENSIONS),
            modes=["chat", "files"],
            features={
                "protocols": ["chat_completions", "responses", "anthropic"],
                "multiple_candidates": True,
                "auto_memory": True,
                "context_strategies": ["recent", "adaptive"],
                "web": web_tools.availability(),
            },
        )

    @blueprint.get("/cockpit/api/chat/tokens", endpoint="tokens")
    def tokens():
        query()
        return jsonify(service().tokens(host.access()))

    @blueprint.post("/cockpit/api/chat/models", endpoint="models")
    def models():
        body = schemas.object_fields(json_body(host), {"token_id"})
        token_id = schemas.integer(body["token_id"], 1, 2147483647)
        return jsonify(service().models(host.access(), token_id))

    @blueprint.route(
        "/cockpit/api/chat/conversations",
        methods=["GET", "POST"],
        endpoint="conversations",
    )
    def conversations():
        principal = host.access().principal
        if request.method == "GET":
            return jsonify(
                service().list_conversations(principal, query({"limit", "cursor"}))
            )
        body = schemas.object_fields(json_body(host), {"conversation_id"}, {"title"})
        schemas.identifier(body["conversation_id"])
        body["title"] = schemas.title(body.get("title", ""))
        result, status = service().create_conversation(principal, body)
        return jsonify(result), status

    @blueprint.route(
        "/cockpit/api/chat/conversations/<cid>",
        methods=["GET", "PATCH", "DELETE"],
        endpoint="conversation",
    )
    def conversation(cid):
        principal, cid = scope(cid)
        if request.method == "GET":
            query()
            return jsonify(conversation=service().get_conversation(principal, cid))
        body = json_body(host)
        if request.method == "PATCH":
            if "loaded_skill_ids" in body:
                return jsonify(
                    conversation=service().set_conversation_skills(
                        host.access(), cid, body
                    )
                )
            schemas.object_fields(body, {"title", "expected_revision"})
            body["title"] = schemas.title(body["title"])
            schemas.integer(body["expected_revision"], 1)
            return jsonify(
                conversation=service().rename_conversation(principal, cid, body)
            )
        schemas.object_fields(body, {"expected_revision", "confirm"})
        schemas.integer(body["expected_revision"], 1)
        if body["confirm"] is not True:
            raise ChatError("请明确确认删除。")
        service().delete_conversation(principal, cid, body)
        return "", 204

    @blueprint.get("/cockpit/api/chat/conversations/<cid>/events", endpoint="events")
    def events(cid):
        principal, cid = scope(cid)
        return jsonify(
            service().events(principal, cid, query({"after_seq", "until_seq", "limit"}))
        )

    @blueprint.route(
        "/cockpit/api/chat/conversations/<cid>/files",
        methods=["GET", "POST"],
        endpoint="files",
    )
    def files(cid):
        principal, cid = scope(cid)
        if request.method == "GET":
            query()
            return jsonify(service().files(principal, cid))
        host.validate_write("X-Cockpit-Chat", "multipart/form-data")
        with service().upload_request(principal, cid):
            request.max_content_length = (
                config.UPLOAD_FILE_BYTES + config.UPLOAD_REQUEST_OVERHEAD_BYTES
            )
            # Werkzeug buffers 64 KiB parser chunks even for disk-spooled files.
            # An 8 KiB limit rejects ordinary files before our file-size check.
            request.max_form_memory_size = config.UPLOAD_FORM_MEMORY_BYTES
            request.max_form_parts = 2
            if (
                set(request.files) != {"file"}
                or len(request.files.getlist("file")) != 1
                or set(request.form) != {"client_file_id"}
                or len(request.form.getlist("client_file_id")) != 1
            ):
                raise ChatError("请仅上传一个文件及其标识。")
            fid = schemas.identifier(request.form["client_file_id"])
            upload = request.files["file"]
            try:
                data = upload.read(config.UPLOAD_FILE_BYTES + 1)
                result, status = service().upload(
                    principal, cid, fid, upload.filename or "", data
                )
                return jsonify(file=result), status
            finally:
                upload.close()

    @blueprint.get(
        "/cockpit/api/chat/conversations/<cid>/files/<fid>/download",
        endpoint="file_download",
    )
    def download(cid, fid):
        principal, cid = scope(cid)
        query()
        data, info = service().download(principal, cid, schemas.identifier(fid))
        response = send_file(
            BytesIO(data),
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=info["name"],
            max_age=0,
            conditional=False,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @blueprint.post("/cockpit/api/chat/conversations/<cid>/runs", endpoint="runs")
    def runs(cid):
        access = host.access()
        schemas.identifier(cid)
        current = service()
        accepted = current.accept_run(access, cid, schemas.parse_run(json_body(host)))
        if isinstance(accepted, dict):
            return jsonify(accepted)
        return Response(
            current.stream(accepted),
            content_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @blueprint.get("/cockpit/api/chat/conversations/<cid>/runs/<rid>", endpoint="run")
    def run(cid, rid):
        principal, cid = scope(cid)
        query()
        return jsonify(run=service().get_run(principal, cid, schemas.identifier(rid)))

    @blueprint.post(
        "/cockpit/api/chat/conversations/<cid>/runs/<rid>/cancel", endpoint="cancel_run"
    )
    def cancel(cid, rid):
        principal, cid = scope(cid)
        schemas.object_fields(json_body(host), set())
        result, status = service().cancel_run(principal, cid, schemas.identifier(rid))
        return jsonify(result), status

    @blueprint.post(
        "/cockpit/api/chat/conversations/<cid>/runs/<rid>/candidate",
        endpoint="select_candidate",
    )
    def select_candidate(cid, rid):
        principal, cid = scope(cid)
        body = schemas.object_fields(
            json_body(host), {"expected_revision", "candidate_index"}
        )
        schemas.integer(body["expected_revision"], 1)
        schemas.integer(body["candidate_index"], 0, config.MAX_CANDIDATES - 1)
        return jsonify(
            service().select_candidate(principal, cid, schemas.identifier(rid), body)
        )

    @blueprint.post(
        "/cockpit/api/chat/conversations/<cid>/runs/<rid>/memory/undo",
        endpoint="undo_memory",
    )
    def undo_memory(cid, rid):
        body = schemas.object_fields(
            json_body(host), {"context_revision", "expected_revision"}
        )
        schemas.integer(body["context_revision"])
        schemas.integer(body["expected_revision"], 1)
        return jsonify(
            service().undo_memory(
                host.access(), schemas.identifier(cid), schemas.identifier(rid), body
            )
        )

    @blueprint.get("/cockpit/api/chat/context", endpoint="context_get")
    def context_get():
        query()
        return jsonify(service().user_context.read_context(host.access().principal))

    @blueprint.patch("/cockpit/api/chat/profiles/<name>", endpoint="profile_update")
    def profile_update(name):
        access = host.access()
        body = schemas.object_fields(
            json_body(host, settings=True),
            {"context_revision", "expected_revision", "content"},
        )
        validate_credentials(body["content"], access.credential)
        return jsonify(
            service().user_context.update_profile(
                access.principal,
                name,
                body["context_revision"],
                body["expected_revision"],
                body["content"],
            )
        )

    @blueprint.get("/cockpit/api/chat/skills", endpoint="skills_list")
    def skills_list():
        query()
        return jsonify(service().user_context.list_skills(host.access().principal))

    @blueprint.post("/cockpit/api/chat/skills", endpoint="skill_create")
    def skill_create():
        access = host.access()
        body = schemas.object_fields(
            json_body(host, settings=True), {"context_revision", "content"}
        )
        validate_credentials(body["content"], access.credential)
        return jsonify(
            service().user_context.create_skill(
                access.principal, body["context_revision"], body["content"]
            )
        ), 201

    @blueprint.get("/cockpit/api/chat/skills/<sid>", endpoint="skill_get")
    def skill_get(sid):
        principal = host.access().principal
        args = query({"context_revision"})
        return jsonify(
            service().user_context.get_skill(
                principal, schemas.identifier(sid), args.get("context_revision")
            )
        )

    @blueprint.patch("/cockpit/api/chat/skills/<sid>", endpoint="skill_update")
    def skill_update(sid):
        access = host.access()
        body = schemas.object_fields(
            json_body(host, settings=True),
            {"context_revision", "expected_revision", "content"},
        )
        validate_credentials(body["content"], access.credential)
        return jsonify(
            service().user_context.update_skill(
                access.principal,
                schemas.identifier(sid),
                body["context_revision"],
                body["expected_revision"],
                body["content"],
            )
        )

    @blueprint.delete("/cockpit/api/chat/skills/<sid>", endpoint="skill_delete")
    def skill_delete(sid):
        principal = host.access().principal
        body = schemas.object_fields(
            json_body(host, settings=True),
            {"context_revision", "expected_revision", "confirm"},
        )
        if body["confirm"] is not True:
            raise ChatError("请明确确认删除。")
        return jsonify(
            service().user_context.delete_skill(
                principal,
                schemas.identifier(sid),
                body["context_revision"],
                body["expected_revision"],
            )
        )

    @blueprint.get(
        "/cockpit/api/chat/conversations/<cid>/runs/<rid>/context",
        endpoint="run_context_get",
    )
    def run_context(cid, rid):
        principal, cid = scope(cid)
        query()
        return jsonify(
            context_snapshot=service().user_context.get_run_context(
                principal, cid, schemas.identifier(rid)
            )
        )

    @blueprint.errorhandler(ChatError)
    def chat_error(exc):
        return jsonify(
            error=str(exc),
            code=exc.code,
            uncertain=exc.uncertain,
            run_id=exc.run_id,
            details=exc.details,
        ), exc.status

    @blueprint.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(
            error="请求体过大或格式无效。" if exc.code == 413 else "请求无法处理。",
            code="CHAT_REQUEST_TOO_LARGE"
            if exc.code == 413
            else "CHAT_INVALID_REQUEST",
            uncertain=False,
            run_id=None,
            details={},
        ), exc.code

    return blueprint
