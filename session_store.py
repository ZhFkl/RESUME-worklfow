"""持久存储：用户拥有素材库，会话与生成任务属于用户。"""
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from task_metrics import summary, timed

BASE_DIR = Path(__file__).resolve().parent
EMPTY_KB = """# 简历素材库

只保存本人提供和确认的事实；待确认内容不用于简历定稿。

## 1. 个人信息
## 2. 教育背景
## 3. 专业技能
## 4. 项目库
## 5. 实习与工作经历
## 6. 自我评价素材
## 7. 方向与项目组合
## 8. 待确认清单
"""


def now():
    return datetime.now(timezone.utc).isoformat()


class NotFound(Exception):
    pass


class Busy(Exception):
    pass


class SessionStore:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv("RESUME_DATA_DIR", BASE_DIR / "data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "resume.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    history TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    session_id TEXT NOT NULL REFERENCES sessions(id), status TEXT NOT NULL,
                    message TEXT NOT NULL, events TEXT NOT NULL DEFAULT '[]',
                    reply TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_task_per_user ON tasks(user_id)
                    WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    task_id TEXT NOT NULL REFERENCES tasks(id), path TEXT NOT NULL, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS imports (
                    user_id TEXT NOT NULL, fingerprint TEXT NOT NULL, task_id TEXT NOT NULL,
                    name TEXT NOT NULL, PRIMARY KEY(user_id, fingerprint));
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    name TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS custom_templates (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id), user_id TEXT NOT NULL REFERENCES users(id),
                    session_id TEXT NOT NULL, upload_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS template_selection (
                    user_id TEXT PRIMARY KEY REFERENCES users(id), template_id TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            for name, default in (("kind", "chat"), ("payload", "{}")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_user(self):
        user_id, token = uuid4().hex, secrets.token_urlsafe(32)
        workspace = self.root / "users" / user_id / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "项目经历库.md").write_text(EMPTY_KB, encoding="utf-8")
        shutil.copytree(BASE_DIR / "模板", workspace / "模板")
        with self.db() as db:
            db.execute("INSERT INTO users VALUES (?, ?, ?)",
                       (user_id, hashlib.sha256(token.encode()).hexdigest(), now()))
        return {"user_id": user_id, "token": token}

    def authenticate(self, token):
        with self.db() as db:
            row = db.execute("SELECT id FROM users WHERE token_hash=?",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None:
            raise NotFound("用户凭据无效")
        return row["id"]

    def workspace(self, user_id):
        with self.db() as db:
            if not db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
                raise NotFound("用户不存在")
        return self.root / "users" / user_id / "workspace"

    def create_session(self, user_id):
        self.workspace(user_id)
        session_id = uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO sessions(id,user_id,created_at) VALUES (?,?,?)",
                       (session_id, user_id, now()))
        return {"session_id": session_id}

    def list_sessions(self, user_id):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT id AS session_id,created_at FROM sessions WHERE user_id=? ORDER BY created_at",
                (user_id,))]

    def history(self, user_id, session_id):
        with self.db() as db:
            row = db.execute("SELECT history FROM sessions WHERE id=? AND user_id=?",
                             (session_id, user_id)).fetchone()
        if row is None:
            raise NotFound("会话不存在")
        return json.loads(row["history"])

    def upload(self, user_id, session_id, upload_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM uploads WHERE id=? AND user_id=? AND session_id=?",
                             (upload_id, user_id, session_id)).fetchone()
        if row is None:
            raise NotFound("附件不存在")
        record = dict(row)
        root = self.workspace(user_id)
        path = (root / record["path"]).resolve()
        if not path.is_relative_to(root / "uploads") or not path.is_file():
            raise NotFound("附件不存在")
        record["file"] = path
        return record

    def upload_info(self, user_id, session_id, upload_id):
        record = self.upload(user_id, session_id, upload_id)
        return {"id": record["id"], "name": record["name"], "size": record["size"]}

    def remove_upload(self, user_id, session_id, upload_id):
        record = self.upload(user_id, session_id, upload_id)
        shutil.rmtree(record["file"].parent)
        with self.db() as db:
            db.execute("DELETE FROM uploads WHERE id=? AND user_id=? AND session_id=?",
                       (upload_id, user_id, session_id))

    def create_task(self, user_id, session_id, message, kind="chat", payload=None):
        self.history(user_id, session_id)
        task_id = uuid4().hex
        try:
            with self.db() as db:
                db.execute("INSERT INTO tasks(id,user_id,session_id,status,message,created_at,updated_at,kind,payload) "
                           "VALUES (?,?,?,'queued',?,?,?,?,?)",
                           (task_id, user_id, session_id, message, now(), now(), kind,
                            json.dumps(payload or {}, ensure_ascii=False)))
        except sqlite3.IntegrityError as exc:
            raise Busy("该用户已有任务正在处理，请等待完成后再发送") from exc
        return task_id

    def event(self, task_id, event):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT events FROM tasks WHERE id=?", (task_id,)).fetchone()
            events = json.loads(row["events"])
            events.append({**event, "time": now()})
            db.execute("UPDATE tasks SET events=?,updated_at=? WHERE id=?",
                       (json.dumps(events, ensure_ascii=False), now(), task_id))

    def status(self, task_id, status, error=None):
        with self.db() as db:
            db.execute("UPDATE tasks SET status=?,error=?,updated_at=? WHERE id=?",
                       (status, error, now(), task_id))

    @timed("save")
    def complete(self, user_id, session_id, task_id, history, reply, paths):
        root = self.workspace(user_id)
        with self.db() as db:
            db.execute("UPDATE sessions SET history=? WHERE id=? AND user_id=?",
                       (json.dumps(history, ensure_ascii=False), session_id, user_id))
            for path in paths:
                relative = path.resolve().relative_to(root)
                db.execute("INSERT INTO files VALUES (?,?,?,?,?,?)",
                           (uuid4().hex, user_id, session_id, task_id, str(relative), path.name))
            db.execute("UPDATE tasks SET status='completed',reply=?,updated_at=? WHERE id=?",
                       (reply, now(), task_id))

    def task(self, user_id, session_id, task_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=? AND session_id=? AND user_id=?",
                             (task_id, session_id, user_id)).fetchone()
            if row is None:
                raise NotFound("任务不存在")
            result = dict(row)
            payload = json.loads(result.pop("payload", "{}"))
            result["events"] = json.loads(result["events"])
            result["files"] = [dict(r) for r in db.execute(
                "SELECT id AS file_id,name FROM files WHERE task_id=?", (task_id,))]
        result["performance"] = summary(result)
        result["attachments"] = [self.upload_info(user_id, session_id, ident) for ident in payload.get("attachments", [])]
        return result

    def active_task(self, user_id):
        with self.db() as db:
            row = db.execute("SELECT id,session_id FROM tasks WHERE user_id=? "
                             "AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
                             (user_id,)).fetchone()
        return self.task(user_id, row["session_id"], row["id"]) if row else None

    def retry_input(self, user_id, session_id, task_id):
        task = self.task(user_id, session_id, task_id)
        if task["status"] != "failed":
            raise Busy("仅可重试失败任务")
        with self.db() as db:
            row = db.execute("SELECT kind,payload,message FROM tasks WHERE id=?", (task_id,)).fetchone()
        return row["kind"], json.loads(row["payload"]), row["message"]

    def recent_tasks(self, user_id, session_id):
        self.history(user_id, session_id)
        with self.db() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT id,session_id,status,kind,message,error,created_at,updated_at,events FROM tasks "
                "WHERE user_id=? AND session_id=? ORDER BY created_at DESC LIMIT 10", (user_id, session_id))]
        for row in rows:
            row["events"] = json.loads(row["events"])
            row["performance"] = summary(row)
            row.pop("events")
        return rows

    def versions(self, user_id):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                "SELECT f.id AS file_id,f.session_id,f.task_id,f.name,t.created_at "
                "FROM files f JOIN tasks t ON t.id=f.task_id WHERE f.user_id=? "
                "AND f.name LIKE '%.pdf' AND t.status='completed' ORDER BY t.created_at DESC",
                (user_id,))]

    def delete_version(self, user_id, file_id):
        """删除单个已完成版本及其同名源文件；数据库失败时恢复移走的文件。"""
        root = self.workspace(user_id).resolve()
        moved = []
        temporary = None
        cleanup = False
        try:
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT f.*,t.status FROM files f JOIN tasks t ON t.id=f.task_id "
                    "WHERE f.id=? AND f.user_id=?", (file_id, user_id)).fetchone()
                if row is None or Path(row["path"]).suffix.lower() != ".pdf":
                    raise NotFound("简历版本不存在")
                if row["status"] != "completed":
                    raise Busy("简历仍在处理中，暂不能删除")
                relative = Path(row["path"])
                siblings = db.execute(
                    "SELECT id,path FROM files WHERE user_id=? AND session_id=? AND task_id=? AND path IN (?,?)",
                    (user_id, row["session_id"], row["task_id"], str(relative), str(relative.with_suffix(".tex")))).fetchall()
                paths = []
                boundary = (root / "resumes" / row["task_id"]).resolve()
                if not boundary.is_relative_to(root / "resumes"):
                    raise NotFound("简历文件路径无效")
                for entry in siblings:
                    path = root / entry["path"]
                    if path.is_symlink() or not path.resolve().is_relative_to(boundary):
                        raise NotFound("简历文件路径无效")
                    paths.append(path)
                # 先在同一文件系统内移走，再提交文件记录删除；不触及素材与上传附件。
                temporary = Path(tempfile.mkdtemp(prefix=".delete-version-", dir=root))
                for index, path in enumerate(paths):
                    if path.exists():
                        target = temporary / str(index)
                        path.rename(target)
                        moved.append((path, target))
                for entry in siblings:
                    db.execute("DELETE FROM files WHERE id=? AND user_id=?", (entry["id"], user_id))
            cleanup = True
        except BaseException:
            for path, target in reversed(moved):
                target.rename(path)
            cleanup = True
            raise
        finally:
            if temporary is not None and cleanup:
                # 提交后清除隔离文件；事务失败时文件已经移回原路径。
                shutil.rmtree(temporary, ignore_errors=True)
        return {"deleted": file_id}

    def materials(self, user_id):
        text = (self.workspace(user_id) / "项目经历库.md").read_text(encoding="utf-8")
        return {"markdown": text, "revision": hashlib.sha256(text.encode()).hexdigest()}

    @staticmethod
    def atomic_text(path, text):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
                temporary = f.name
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def save_materials(self, user_id, markdown, revision):
        path = self.workspace(user_id) / "项目经历库.md"
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM tasks WHERE user_id=? AND status IN ('queued','running')",
                          (user_id,)).fetchone():
                raise Busy("助手正在处理素材，请等待任务完成后再保存")
            before = self.materials(user_id)
            if revision != before["revision"]:
                raise Busy("素材库已更新，请先读取最新内容再合并；你的草稿仍保留")
            snapshots = path.parent / "snapshots"
            snapshots.mkdir(exist_ok=True)
            self.atomic_text(snapshots / f"manual-{uuid4().hex}-before.md", before["markdown"])
            self.atomic_text(path, markdown)
        return self.materials(user_id)

    def imported(self, user_id, fingerprint):
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM imports WHERE user_id=? AND fingerprint=?",
                                   (user_id, fingerprint)).fetchone())

    @timed("save")
    def complete_import(self, user_id, session_id, task_id, revision, markdown, documents, reply):
        before = self.materials(user_id)
        if before["revision"] != revision:
            raise Busy("素材库已变化，本次整理未写入，请重新导入")
        root = self.workspace(user_id)
        snapshots = root / "snapshots"
        snapshots.mkdir(exist_ok=True)
        self.atomic_text(snapshots / f"{task_id}-before.md", before["markdown"])
        self.atomic_text(snapshots / f"{task_id}-source.json", json.dumps(documents, ensure_ascii=False))
        history = self.history(user_id, session_id)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT message,status FROM tasks WHERE id=? AND user_id=? AND session_id=?",
                              (task_id, user_id, session_id)).fetchone()
            if not task or task["status"] not in {"queued", "running"}:
                raise Busy("任务状态已变化，未写入素材")
            history += [{"role": "user", "content": task["message"]}, {"role": "assistant", "content": reply}]
            try:
                self.atomic_text(root / "项目经历库.md", markdown)
                self.atomic_text(snapshots / f"{task_id}-after.md", markdown)
                for document in documents:
                    db.execute("INSERT OR IGNORE INTO imports VALUES (?,?,?,?)",
                               (user_id, document["fingerprint"], task_id, document["name"]))
                db.execute("UPDATE sessions SET history=? WHERE id=?",
                           (json.dumps(history, ensure_ascii=False), session_id))
                db.execute("UPDATE tasks SET status='completed',reply=?,updated_at=? WHERE id=?",
                           (reply, now(), task_id))
                db.commit()
            except BaseException:
                self.atomic_text(root / "项目经历库.md", before["markdown"])
                raise

    def file(self, user_id, session_id, file_id):
        with self.db() as db:
            row = db.execute("SELECT path FROM files WHERE id=? AND user_id=? AND session_id=?",
                             (file_id, user_id, session_id)).fetchone()
        if row is None:
            raise NotFound("文件不存在")
        root = self.workspace(user_id)
        path = (root / row["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise NotFound("文件不存在")
        return path

    def recover_interrupted(self, error="服务已重启，任务中断；请重新提交"):
        with self.db() as db:
            db.execute("UPDATE tasks SET status='failed',error=?,updated_at=? "
                       "WHERE status IN ('queued','running')",
                       (error, now()))

    @contextmanager
    def runtime(self):
        """本地单进程运行锁，防止另一进程误将活跃任务标记成重启失败。"""
        import fcntl
        with (self.root / "runtime.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("该数据目录已被使用，请停止另一终端/API或使用不同数据目录") from exc
            try:
                self.recover_interrupted()
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
