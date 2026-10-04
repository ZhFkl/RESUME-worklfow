"""单进程后台任务；SQLite 保证同一用户不会并发修改素材。"""
import asyncio
import logging

from agent_core import AgentError
from session_store import Busy
from task_metrics import tracing, span

logger = logging.getLogger(__name__)


def failure_message(exc):
    # 兼容异步任务组包装的模型异常。
    nested = getattr(exc, "exceptions", None)
    if nested:
        return failure_message(nested[0])
    if isinstance(exc, (AgentError, Busy)):
        return str(exc)
    if isinstance(exc, asyncio.TimeoutError):
        return "处理超时，请缩小任务后重试"
    status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return f"模型接口拒绝请求（HTTP {status}），请检查 API 配置和访问权限"
    if status == 429:
        return "模型接口限流或额度不足，请稍后重试并检查额度"
    return "处理失败，请检查模型配置和工具运行环境后重试"


class TaskManager:
    def __init__(self, store, core, concurrency=3, timeout=600, importer=None, template_importer=None):
        self.store, self.core = store, core
        self.semaphore = asyncio.Semaphore(concurrency)
        self.timeout = timeout
        self.running = set()
        self.importer = importer
        self.template_importer = template_importer

    def submit(self, user_id, session_id, message, attachments=None, template_id=None):
        attachments = attachments or []
        for ident in attachments:
            self.store.upload(user_id, session_id, ident)
        from template_library import TemplateLibrary
        library = TemplateLibrary(self.store)
        template = library.get(user_id, template_id) if template_id else library.selected(user_id)
        if template["status"] != "ready":
            raise Busy("模板尚未转换完成")
        task_id = self.store.create_task(user_id, session_id, message,
            payload={"attachments": attachments, "template_id": template["id"]})
        task = asyncio.create_task(self._run(user_id, session_id, task_id, message, attachments=attachments))
        self.running.add(task)
        task.add_done_callback(self.running.discard)
        return task_id

    def submit_template(self, user_id, session_id, upload_id, name):
        from template_library import TemplateLibrary
        self.store.upload(user_id, session_id, upload_id)
        payload = {"upload_id": upload_id, "name": name}
        tid = self.store.create_task(user_id, session_id, "转换模板：" + name, "template", payload)
        try:
            TemplateLibrary(self.store).register(user_id, session_id, tid, upload_id, name)
        except Exception:
            self.store.status(tid, "failed", "模板登记失败，请重新上传")
            raise
        task = asyncio.create_task(self._run_template(user_id, session_id, tid, payload))
        self.running.add(task)
        task.add_done_callback(self.running.discard)
        return tid

    async def _run_template(self, uid, sid, tid, payload):
        emit = lambda event: self.store.event(tid, event)
        with tracing(emit):
            await self._execute(uid, sid, tid, "", emit, None, None, payload)

    def submit_import(self, user_id, session_id, documents):
        message = "上传简历并自动整理到素材库：" + "、".join(doc["name"] for doc in documents)
        task_id = self.store.create_task(user_id, session_id, message, "import", {"documents": documents})
        task = asyncio.create_task(self._run(user_id, session_id, task_id, message, documents))
        self.running.add(task)
        task.add_done_callback(self.running.discard)
        return task_id

    async def _run(self, user_id, session_id, task_id, message, documents=None, attachments=None):
        emit = lambda event: self.store.event(task_id, event)
        with tracing(emit):
            await self._execute(user_id, session_id, task_id, message, emit, documents, attachments)

    async def _execute(self, user_id, session_id, task_id, message, emit, documents, attachments, template_payload=None):
        try:
            with span("queue"):
                await self.semaphore.acquire()
            try:
                self.store.status(task_id, "running")
                kwargs = {"attachments": attachments} if attachments else {}
                work = (self.template_importer.run_import(user_id, session_id, task_id, template_payload, emit)
                        if template_payload is not None else self.importer.run_import(user_id, session_id, task_id, documents, emit)
                        if documents is not None else self.core.run_turn(user_id, session_id, task_id, message, emit, **kwargs))
                await asyncio.wait_for(work, timeout=self.timeout)
                emit({"type": "completed"})
            finally:
                self.semaphore.release()
        except asyncio.CancelledError:
            self.store.status(task_id, "failed", "服务停止，任务中断；请重新提交")
            emit({"type": "failed", "reason": "cancelled"})
            raise
        except Exception as exc:
            # 不把 SDK 异常中的配置、服务器响应等直接回传给浏览器。
            error = failure_message(exc)
            logger.error("Task %s failed (%s)", task_id, type(exc).__name__)
            self.store.status(task_id, "failed", error)
            emit({"type": "failed", "message": error})

    async def close(self):
        tasks = list(self.running)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.store.recover_interrupted("服务停止，任务中断；请重新提交")
