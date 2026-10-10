"""系统模板动态发现；个人模板和选中状态按用户隔离。"""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from backend.session_store import BASE_DIR, NotFound, Busy, now
from backend.process_runner import run_process

TEMPLATE_CATEGORIES = ("engineering", "product", "design", "marketing", "business")


def template_categories(value):
    """Read optional multi-role metadata without guessing from template names."""
    if not isinstance(value, list):
        return []
    return [category for category in TEMPLATE_CATEGORIES if category in value]


class TemplateLibrary:
    def __init__(self, store):
        self.store = store

    def builtins(self):
        result = []
        for path in sorted((BASE_DIR / "模板").glob("*.tex")):
            if path.is_symlink():
                continue
            defaults = {"resume-中文模板": ("经典中文", "黑白排版，清晰展示教育、技能、项目与经历。"),
                        "resume-en-template": ("Classic English", "英文简历，简洁标题与紧凑的经历列表。")}
            name, description = defaults.get(path.stem, (path.stem, "系统提供的可复用简历模板。"))
            categories = ["engineering"] if path.stem in defaults else []
            meta = path.with_suffix(".json")
            if meta.is_file() and not meta.is_symlink():
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    data = {}
                if isinstance(data, dict):
                    name, description = str(data.get("name", name)), str(data.get("description", description))
                    if "categories" in data:
                        categories = template_categories(data["categories"])
            result.append({"id": "builtin-" + hashlib.sha256(path.name.encode()).hexdigest()[:16],
                           "name": name, "description": description, "source": "system", "status": "ready",
                           "categories": categories, "path": path, "preview": path.with_suffix(".pdf").is_file()})
        return sorted(result, key=lambda item: (item["name"] != "经典中文", item["name"]))

    def directory(self, uid, task_id):
        return self.store.workspace(uid) / "模板" / "custom" / task_id

    def get(self, uid, ident):
        self.store.workspace(uid)
        for item in self.builtins():
            if item["id"] == ident:
                return item
        with self.store.db() as db:
            row = db.execute("SELECT c.*, t.status,t.error,u.name AS original_name FROM custom_templates c JOIN tasks t ON t.id=c.task_id "
                             "JOIN uploads u ON u.id=c.upload_id "
                             "WHERE c.user_id=? AND c.task_id=?", (uid, ident.removeprefix("custom-"))).fetchone()
        if row is None or ident != "custom-" + row["task_id"]:
            raise NotFound("模板不存在")
        notes = self.directory(uid, row["task_id"]) / "notes.txt"
        description = (notes.read_text(encoding="utf-8")[:2000] if row["status"] == "completed" and notes.is_file() else "")
        return {**dict(row), "id": ident, "source": "custom",
                "status": "ready" if row["status"] == "completed" else row["status"],
                "description": description or "根据上传文件重建的个人模板，使用前可预览转换结果。",
                "path": self.directory(uid, row["task_id"]) / "template.tex",
                "preview": row["status"] == "completed"}

    def public(self, item):
        result = {k: v for k, v in item.items() if k not in {"path", "user_id", "upload_id"}}
        result["categories"] = template_categories(item.get("categories"))
        base = "/templates/" + item["id"] + "/files/"
        result.update(tex_url=base + "tex" if item["status"] == "ready" else None,
                      pdf_url=base + "pdf" if item["preview"] else None,
                      thumbnail_url=base + "png" if item["preview"] else None,
                      original_url=base + "original" if item["source"] == "custom" else None)
        return result

    def selected(self, uid):
        with self.store.db() as db:
            row = db.execute("SELECT template_id FROM template_selection WHERE user_id=?", (uid,)).fetchone()
        if row:
            item = self.get(uid, row["template_id"])
            if item["status"] != "ready":
                raise Busy("所选模板不可用，请重新选择；不会自动改用默认模板")
            return item
        items = self.builtins()
        if not items:
            raise NotFound("尚无可用系统模板")
        return items[0]

    def catalog(self, uid):
        self.store.workspace(uid)
        with self.store.db() as db:
            rows = db.execute("SELECT task_id FROM custom_templates WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
        items = self.builtins() + [self.get(uid, "custom-" + row["task_id"]) for row in rows]
        return {"templates": [self.public(item) for item in items], "selected_id": self.selected(uid)["id"]}

    def select(self, uid, ident):
        item = self.get(uid, ident)
        if item["status"] != "ready":
            raise Busy("模板尚未转换成功，请完成后再选择")
        with self.store.db() as db:
            db.execute("INSERT INTO template_selection VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET template_id=excluded.template_id", (uid, ident))
        return self.public(item)

    def register(self, uid, sid, tid, upload_id, name):
        with self.store.db() as db:
            db.execute("INSERT INTO custom_templates VALUES (?,?,?,?,?,?)", (tid, uid, sid, upload_id, name, now()))

    def snapshot(self, uid, sid, tid):
        with self.store.db() as db:
            row = db.execute("SELECT payload FROM tasks WHERE id=? AND user_id=? AND session_id=?", (tid, uid, sid)).fetchone()
        if row is None:
            raise NotFound("任务不存在")
        payload = json.loads(row["payload"])
        locked = payload.get("selected_template")
        if locked:
            target = self.store.workspace(uid) / locked["path"]
            if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != locked["sha256"]:
                raise Busy("本次模板快照已变化，请重新提交任务")
            return locked
        ident = payload.get("template_id")
        item = self.get(uid, ident) if ident else self.selected(uid)
        if item["status"] != "ready":
            raise Busy("所选模板尚不可用")
        root = self.store.workspace(uid)
        target = root / "模板" / "tasks" / (tid + ".tex")
        target.parent.mkdir(exist_ok=True)
        if item["path"].is_symlink() or not item["path"].is_file():
            raise NotFound("模板源码不存在")
        shutil.copyfile(item["path"], target)
        locked = {"id": item["id"], "name": item["name"], "path": str(target.relative_to(root)),
                  "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        payload.update(template_id=item["id"], selected_template=locked)
        with self.store.db() as db:
            db.execute("UPDATE tasks SET payload=? WHERE id=? AND user_id=?", (json.dumps(payload, ensure_ascii=False), tid, uid))
        return locked

    async def file(self, uid, ident, kind):
        item = self.get(uid, ident)
        if kind == "original" and item["source"] == "custom":
            record = self.store.upload(uid, item["session_id"], item["upload_id"])
            return record["file"], record["name"]
        if item["status"] != "ready" or kind not in {"tex", "pdf", "png"}:
            raise NotFound("模板文件不可用")
        path = item["path"].with_suffix("." + kind)
        if kind == "png" and item["source"] == "system":
            pdf = item["path"].with_suffix(".pdf")
            if not pdf.is_file():
                raise NotFound("该模板尚无预览")
            cache = self.store.root / "template_previews"
            cache.mkdir(exist_ok=True)
            path = cache / (hashlib.sha256(pdf.read_bytes()).hexdigest() + ".png")
            if not path.exists():
                with tempfile.TemporaryDirectory(dir=cache) as directory:
                    prefix = Path(directory) / "preview"
                    result = await run_process(["pdftoppm", "-f", "1", "-singlefile", "-scale-to", "1100", "-png", str(pdf), str(prefix)], timeout=45)
                    if result.returncode or not prefix.with_suffix(".png").exists():
                        raise NotFound("预览生成失败")
                    prefix.with_suffix(".png").replace(path)
        if not path.is_file() or path.is_symlink():
            raise NotFound("模板文件不存在")
        return path, item["name"] + "." + kind
