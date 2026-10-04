"""本地 API：python3 -m uvicorn api:app --host 127.0.0.1 --port 8000"""
import json
import os
import hashlib
import asyncio
from time import perf_counter
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.datastructures import UploadFile

from agent_core import AgentCore
from session_store import BASE_DIR, Busy, NotFound, SessionStore
from task_manager import TaskManager
from material_importer import MaterialImporter, normalize_documents
from uploaded_files import save_upload
from profile_assets import photo_info, photo_path, save_photo
from template_library import TemplateLibrary
from template_importer import TemplateImporter
from pathlib import Path
from starlette.middleware import Middleware
from account_store import AccountStore, AccountError
from account_api import AccountProtection, current, routes as account_routes, error as account_error
from sms_gateway import SmsGateway


def create_app(data_dir=None, core_factory=AgentCore, importer_factory=MaterialImporter, template_importer_factory=TemplateImporter,
               allow_legacy_users=False, sms_factory=SmsGateway):
    def backend_revision():
        digest = hashlib.sha256()
        for path in sorted(BASE_DIR.glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    loaded_revision = backend_revision()

    def require_current_backend():
        if backend_revision() != loaded_revision:
            raise HTTPException(503, "后端代码已更新，请重启后端再生成，避免使用旧的验收规则")

    @asynccontextmanager
    async def lifespan(app):
        load_dotenv(BASE_DIR / ".env")
        app.state.store = SessionStore(data_dir)
        app.state.accounts = AccountStore(app.state.store)
        app.state.auth_workers = asyncio.Semaphore(4)
        app.state.sms = sms_factory()
        app.state.allow_legacy_users = allow_legacy_users
        with app.state.store.runtime():
            app.state.manager = TaskManager(app.state.store, core_factory(app.state.store),
                                            importer=importer_factory(app.state.store),
                                            template_importer=template_importer_factory(app.state.store))
            try:
                yield
            finally:
                await app.state.manager.close()

    def user(request):
        if request.cookies.get("resume_session") or not allow_legacy_users:
            return current(request)["user_id"]
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(401, "需要用户凭据")
        try:
            return request.app.state.store.authenticate(auth[7:])
        except NotFound:
            raise HTTPException(401, "用户凭据无效")

    async def health(request):
        return JSONResponse({"status": "ok", "mode": "local-development", "app": "resume-agent",
                             "backend_revision": loaded_revision[:12],
                             "restart_required": backend_revision() != loaded_revision,
                             "model_configured": bool(os.getenv("AGENT_API_KEY") and os.getenv("AGENT_MODEL"))})

    async def json_body(request):
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(400, "请求必须是 JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "请求必须是 JSON 对象")
        return body

    async def materials(request):
        uid, store = user(request), request.app.state.store
        if request.method == "GET":
            return JSONResponse(store.materials(uid), headers={"Cache-Control": "no-store"})
        body = await json_body(request)
        markdown, revision = body.get("markdown"), body.get("revision")
        if not isinstance(markdown, str) or len(markdown) > 500000 or not isinstance(revision, str):
            raise HTTPException(400, "markdown 需为不超过 50 万字符的文本，并提供 revision")
        return JSONResponse(store.save_materials(uid, markdown, revision))

    async def profile(request):
        store = request.app.state.store
        return JSONResponse(photo_info(store.workspace(user(request))), headers={"Cache-Control": "no-store"})

    async def profile_photo(request):
        uid, store = user(request), request.app.state.store
        if request.method == "GET":
            path = photo_path(store.workspace(uid))
            if not path.is_file():
                raise NotFound("尚未上传证件照")
            return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
        if request.method == "DELETE":
            if store.active_task(uid):
                raise Busy("任务正在处理，请完成后再更新证件照")
            photo_path(store.workspace(uid)).unlink(missing_ok=True)
            return JSONResponse(photo_info(store.workspace(uid)))
        try:
            async with request.form(max_files=1, max_fields=0, max_part_size=5 * 1024 * 1024) as form:
                upload = form.get("photo")
                if len(form) != 1 or not isinstance(upload, UploadFile):
                    raise ValueError("请上传一张证件照，字段名为 photo")
                return JSONResponse(await save_photo(store, uid, upload))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    async def imports(request):
        require_current_backend()
        uid, sid = user(request), request.path_params["session_id"]
        request.app.state.store.history(uid, sid)
        body = await json_body(request)
        try:
            documents = normalize_documents(body.get("documents"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        tid = request.app.state.manager.submit_import(uid, sid, documents)
        return JSONResponse({"task_id": tid}, status_code=202)

    async def templates(request):
        return JSONResponse(TemplateLibrary(request.app.state.store).catalog(user(request)), headers={"Cache-Control": "no-store"})

    async def select_template(request):
        body = await json_body(request)
        if not isinstance(body.get("template_id"), str):
            raise HTTPException(400, "请指定模板")
        item = TemplateLibrary(request.app.state.store).select(user(request), body["template_id"])
        return JSONResponse({"selected": item})

    async def template_file(request):
        path, name = await TemplateLibrary(request.app.state.store).file(user(request), request.path_params["template_id"], request.path_params["kind"])
        return FileResponse(path, filename=name, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    async def upload_template(request):
        require_current_backend()
        uid, sid = user(request), request.path_params["session_id"]
        store = request.app.state.store
        store.history(uid, sid)
        if store.active_task(uid):
            raise Busy("请等待当前任务完成后再上传模板")
        upload_id = None
        try:
            async with request.form(max_files=1, max_fields=1, max_part_size=15 * 1024 * 1024) as form:
                upload = form.get("file")
                if not isinstance(upload, UploadFile) or Path(upload.filename or "").suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("请上传 PDF、PNG、JPG 或 WebP 模板，最大 15 MB")
                name = form.get("name") or Path(upload.filename).stem
                if not isinstance(name, str) or not name.strip() or len(name) > 100:
                    raise ValueError("模板名称需为 1～100 个字符")
                upload_id = await save_upload(store, uid, sid, upload)
            tid = request.app.state.manager.submit_template(uid, sid, upload_id, name.strip())
        except BaseException as exc:
            if upload_id:
                store.remove_upload(uid, sid, upload_id)
            if isinstance(exc, ValueError):
                raise HTTPException(400, str(exc))
            raise
        return JSONResponse({"task_id": tid, "template_id": "custom-" + tid}, status_code=202)

    async def active_task(request):
        return JSONResponse({"task": request.app.state.store.active_task(user(request))},
                            headers={"Cache-Control": "no-store"})

    async def versions(request):
        return JSONResponse({"files": request.app.state.store.versions(user(request))},
                            headers={"Cache-Control": "no-store"})

    async def delete_version(request):
        try:
            result = request.app.state.store.delete_version(user(request), request.path_params["file_id"])
        except OSError:
            raise HTTPException(503, "删除未完成，请稍后重试")
        return JSONResponse(result)

    async def users(request):
        if not allow_legacy_users:
            raise HTTPException(410, "请通过注册或登录使用工作空间")
        return JSONResponse(request.app.state.store.create_user(), status_code=201)

    async def sessions(request):
        uid = user(request)
        store = request.app.state.store
        if request.method == "POST":
            return JSONResponse(store.create_session(uid), status_code=201)
        return JSONResponse({"sessions": store.list_sessions(uid)})

    async def messages(request):
        received_at = perf_counter()
        uid, sid = user(request), request.path_params["session_id"]
        if request.method == "GET":
            store = request.app.state.store
            history = store.history(uid, sid)
            return JSONResponse({"messages": [
                {"role": m["role"], "content": m["content"],
                 "attachments": [store.upload_info(uid, sid, ident) for ident in m.get("attachments", [])]} for m in history
                if m["role"] in {"user", "assistant"} and m.get("content")]})
        require_current_backend()
        store = request.app.state.store
        store.history(uid, sid)
        saved = []
        try:
            if request.headers.get("content-type", "").startswith("multipart/form-data"):
                if store.active_task(uid):
                    raise Busy("该用户已有任务正在处理，请等待完成后再发送")
                async with request.form(max_files=3, max_fields=1, max_part_size=15 * 1024 * 1024) as form:
                    message = form.get("message", "")
                    if not isinstance(message, str) or len(message) > 30000:
                        raise HTTPException(400, "消息需为不超过 30000 字符的文字")
                    for key, upload in form.multi_items():
                        if key == "message":
                            continue
                        if key != "files" or not isinstance(upload, UploadFile):
                            raise HTTPException(400, "附件字段必须为 files")
                        saved.append(await save_upload(store, uid, sid, upload))
            else:
                message = (await json_body(request)).get("message")
            if not isinstance(message, str) or len(message) > 30000 or (not message.strip() and not saved):
                raise HTTPException(400, "请填写消息或添加附件，文字不得超过 30000 字符")
            if not message.strip():
                message = "请查看附件：如果是我的简历，请把其中明确的个人信息和经历完整整理到素材库；如果是岗位说明或参考材料，请先分析并说明用途。"
            request_ms = round((perf_counter() - received_at) * 1000, 2)
            task_id = request.app.state.manager.submit(uid, sid, message.strip(), attachments=saved)
            store.event(task_id, {"type": "request_received", "attachment_count": len(saved),
                                  "duration_ms": request_ms})
        except BaseException as exc:
            for ident in saved:
                store.remove_upload(uid, sid, ident)
            if isinstance(exc, ValueError):
                raise HTTPException(400, str(exc))
            raise
        return JSONResponse({"task_id": task_id}, status_code=202)

    async def task(request):
        p = request.path_params
        return JSONResponse(request.app.state.store.task(user(request), p["session_id"], p["task_id"]))

    async def tasks(request):
        return JSONResponse({"tasks": request.app.state.store.recent_tasks(user(request), request.path_params["session_id"])})

    async def retry(request):
        require_current_backend()
        uid, p, store = user(request), request.path_params, request.app.state.store
        kind, payload, message = store.retry_input(uid, p["session_id"], p["task_id"])
        manager = request.app.state.manager
        tid = (manager.submit_template(uid, p["session_id"], payload["upload_id"], payload["name"]) if kind == "template"
               else manager.submit_import(uid, p["session_id"], payload["documents"]) if kind == "import"
               else manager.submit(uid, p["session_id"], message, attachments=payload.get("attachments"), template_id=payload.get("template_id")))
        return JSONResponse({"task_id": tid}, status_code=202)

    async def file(request):
        p = request.path_params
        path = request.app.state.store.file(user(request), p["session_id"], p["file_id"])
        return FileResponse(path, filename=path.name, headers={"Cache-Control": "private, no-store"})

    async def upload_file(request):
        p = request.path_params
        record = request.app.state.store.upload(user(request), p["session_id"], p["upload_id"])
        return FileResponse(record["file"], filename=record["name"],
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    async def known_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409 if isinstance(exc, Busy) else 404)

    async def http_error(request, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return Starlette(lifespan=lifespan, max_body_size=48 * 1024 * 1024,
                    middleware=[Middleware(AccountProtection)], routes=[*account_routes,
        Route("/health", health),
        Route("/users", users, methods=["POST"]),
        Route("/materials", materials, methods=["GET", "PUT"]),
        Route("/templates", templates),
        Route("/templates/selection", select_template, methods=["PUT"]),
        Route("/templates/{template_id}/files/{kind}", template_file),
        Route("/sessions/{session_id}/templates", upload_template, methods=["POST"]),
        Route("/profile", profile),
        Route("/profile/photo", profile_photo, methods=["GET", "PUT", "DELETE"]),
        Route("/tasks/active", active_task),
        Route("/versions", versions),
        Route("/versions/{file_id}", delete_version, methods=["DELETE"]),
        Route("/sessions", sessions, methods=["GET", "POST"]),
        Route("/sessions/{session_id}/messages", messages, methods=["GET", "POST"]),
        Route("/sessions/{session_id}/imports", imports, methods=["POST"]),
        Route("/sessions/{session_id}/tasks/{task_id}", task),
        Route("/sessions/{session_id}/tasks", tasks),
        Route("/sessions/{session_id}/tasks/{task_id}/retry", retry, methods=["POST"]),
        Route("/sessions/{session_id}/files/{file_id}", file),
        Route("/sessions/{session_id}/uploads/{upload_id}", upload_file),
        Mount("/", StaticFiles(directory=BASE_DIR / "prototype", html=True)),
    ], exception_handlers={NotFound: known_error, Busy: known_error, HTTPException: http_error, AccountError: account_error})


app = create_app()
