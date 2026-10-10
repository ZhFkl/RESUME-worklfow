"""后端本地工具：声明模型工具并直接调用 Python 函数，不使用 MCP 通信。"""
import asyncio
import inspect
import json
import os
import tempfile
import shutil
from pathlib import Path

from backend.workspace import resolve_file
from backend.resume_review import layout_metrics, quality_issues
from backend.pdf_preview import PreviewResult, invalidate, save_draft, render_preview
from backend.process_runner import run_process
from backend.task_metrics import span
from backend.profile_assets import snapshot_photo, photo_issues
from backend.layout_tuning import apply_parameters, parameters, guidance, LIMITS
from backend.tex_edits import apply_edits, source_hash


class LocalTools:
    def __init__(self, workspace, output, *, snapshot_profile=True):
        self.workspace, self.output = Path(workspace).resolve(), Path(output).resolve()
        self.snapshot_profile = snapshot_profile
        self.selected_template_source = None
        self.selected_template_path = None
        self.resolve("项目经历库.md")  # 在创建任何临时资源前验证任务目录。
        self.preview_dir = None
        self._temporary = None
        self.layout_history = {}
        self.last_layout_report = None
        self._functions = {name: getattr(self, name) for name in
                           ("read_selected_template", "read_file", "write_file", "edit_file", "compile_pdf", "preview_pdf")}
        # 保留模型可见名称，以兼容已保存的会话；前缀不涉及协议通信。
        self.tools = []
        for name, function in self._functions.items():
            parameters = inspect.signature(function).parameters
            self.tools.append({"type": "function", "function": {
                "name": "resume__" + name, "description": inspect.getdoc(function),
                "parameters": {"type": "object", "properties": {
                    key: {"type": "string"} for key in parameters},
                    "required": [key for key, value in parameters.items() if value.default is inspect.Parameter.empty],
                    "additionalProperties": False}}})

    def resolve(self, name, write=False):
        return resolve_file(name, self.workspace, self.output, write=write)

    async def __aenter__(self):
        if self.snapshot_profile:
            snapshot_photo(self.workspace, self.output)
        self._temporary = tempfile.TemporaryDirectory(prefix="resume-preview-")
        self.preview_dir = Path(self._temporary.name)
        return self

    async def __aexit__(self, *exc):
        self._temporary.cleanup()
        self.preview_dir = None

    async def call(self, name, arguments):
        function = self._functions.get(name.removeprefix("resume__")) if name.startswith("resume__") else None
        if function is None:
            raise ValueError("未知工具")
        parameters = inspect.signature(function).parameters
        required = {key for key, value in parameters.items() if value.default is inspect.Parameter.empty}
        if (not isinstance(arguments, dict) or not required <= set(arguments) or set(arguments) - set(parameters)
                or any(not isinstance(value, str) for value in arguments.values())):
            raise ValueError("工具参数不匹配：需提供 " + "、".join(parameters) + "，且均为字符串")
        if self.preview_dir is None:
            raise RuntimeError("工具任务尚未启动或已经结束")
        try:
            result = function(**arguments)
            return await result if inspect.isawaitable(result) else result
        except (ValueError, OSError) as exc:
            return "工具执行出错：" + str(exc)

    def read_selected_template(self) -> str:
        """读取后端绑定的本次所选模板完整源码。无需参数，不自行选择模板或填写路径；返回 path、content 和 base_sha256。"""
        if self.selected_template_path is None:
            raise ValueError("当前任务未绑定模板")
        path = self.selected_template_path.relative_to(self.workspace).as_posix()
        target = self.resolve(path)
        content = target.read_text(encoding="utf-8")
        if self.selected_template_source is not None and content != self.selected_template_source:
            raise ValueError("本次模板快照发生变化，请重新发起任务")
        return json.dumps({"path": path, "base_sha256": source_hash(content), "content": content}, ensure_ascii=False)

    def read_file(self, path: str) -> str:
        """读取素材或本轮 .tex。模板使用无参数的 read_selected_template。LaTeX 返回 JSON，包含 content 和 base_sha256，供 edit_file 原样引用版本。"""
        target = self.resolve(path)
        if self.selected_template_path and target.is_relative_to(self.workspace / "模板"):
            # 兼容历史工具调用，但模板来源始终由后端绑定，不能通过参数切换。
            result = json.loads(self.read_selected_template())
            if target != self.selected_template_path:
                result.update(requested_path=path, redirected=True,
                    message="已返回后端绑定的本次模板，请使用 content 继续。后续读取调用无参数的 read_selected_template，无需用户重新选择或上传。")
            return json.dumps(result, ensure_ascii=False)
        content = target.read_text(encoding="utf-8")
        return (json.dumps({"path": path, "base_sha256": source_hash(content), "content": content}, ensure_ascii=False)
                if target.suffix == ".tex" else content)

    def write_file(self, path: str, content: str) -> str:
        """更新素材库或写入本轮完整 .tex，不能修改模板。支持创建初稿和覆盖已有文件；小改优先 edit_file，大改可自行选择全文写回。写入后须重新编译并检查截图。行距/下边距可用 compile_pdf 的 layout_json。"""
        target = self.resolve(path, write=True)
        self._save(target, content)
        return f"已保存 {path}" + ("\nbase_sha256: " + source_hash(content) if target.suffix == ".tex" else "")

    def edit_file(self, path: str, base_sha256: str, edits_json: str) -> str:
        """局部修改本轮已有 .tex。base_sha256 原样使用最近读取/写入的版本。edits_json 是数组字符串，例如 [{"old":"原代码","new":"新代码"}]；1～20 项，old 必须唯一逐字匹配，区域不可重叠，全部针对同一原文件。失败不写入；成功后必须重新编译并检查截图。"""
        target = self.resolve(path, write=True)
        if target.suffix != ".tex" or not target.is_file():
            raise ValueError("只能局部修改本轮已存在的 .tex 文件")
        source = target.read_text(encoding="utf-8")
        edits = json.loads(edits_json)
        revised = apply_edits(source, {"base_sha256": base_sha256, "edits": edits})
        self._save(target, revised)
        return json.dumps({"path": path, "edit_count": len(edits), "base_sha256": source_hash(revised),
                           "message": "局部修改已保存，旧 PDF 和截图已失效；请重新编译并检查截图"}, ensure_ascii=False)

    def _save(self, target, content):
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(content)
            os.replace(temporary, target)
            if target.suffix == ".tex":
                invalidate(target, self.preview_dir)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    async def compile_pdf(self, tex_file: str, layout_json: str = "") -> str:
        """编译并返回页数、留白、参数、历史测量及下一步建议。可选 layout_json 由你指定本轮微调，例如 {"line_spread":1.08,"bottom_mm":10}；line_spread 限 1.0～1.15，bottom_mm 限 8～14 mm。只应用这一组参数，保持正文不变。必须一页、留白≤5%、照片与内容验收通过，并查看截图后交付。"""
        self.last_layout_report = None
        tex = self.resolve(tex_file, write=True)
        if tex.suffix != ".tex" or not tex.is_file():
            raise ValueError("需要已存在的 .tex 文件")
        if self.selected_template_source is not None:
            from backend.template_guard import verify_template
            try:
                verify_template(tex.read_text(encoding="utf-8"), self.selected_template_source)
            except ValueError:
                invalidate(tex, self.preview_dir)
                raise
        if layout_json:
            revised = apply_parameters(tex.read_text(encoding="utf-8"), layout_json)
            self._save(tex, revised)
        if tex.parent != self.output and (self.output / "photo.jpg").is_file():
            target = tex.parent / "photo.jpg"
            if target.is_symlink():
                raise ValueError("证件照路径不能使用符号链接")
            shutil.copyfile(self.output / "photo.jpg", target)
        pdf = tex.with_suffix(".pdf")
        invalidate(tex, self.preview_dir)
        successful = False
        env = {"openin_any": "p", "openout_any": "p"}
        try:
            for _ in range(2):
                result = await run_process(
                    ["xelatex", "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode", tex.name],
                    cwd=tex.parent, env=env, timeout=120)
                if result.returncode:
                    return "编译失败：\n" + result.stdout[-2000:]
            if not pdf.is_file():
                return "编译失败：没有生成 PDF"
            from pypdf import PdfReader
            document = PdfReader(pdf)
            save_draft(tex, pdf, document, self.preview_dir, self.resolve("项目经历库.md"))
            pages = len(document.pages)
            source = tex.read_text(encoding="utf-8")
            layout = layout_metrics(document.pages[0]) if pages else None
            with span("quality"):
                issues = (quality_issues(source, self.resolve("项目经历库.md").read_text(encoding="utf-8"), document.pages[0])
                          if pages == 1 else [f"编译结果为 {pages} 页，须调整到一页"])
                issues.extend(photo_issues(document, self.output / "photo.jpg"))
            current = parameters(source)
            history = self.layout_history.setdefault(tex_file, [])
            history.append({"attempt": len(history) + 1, "parameters": current, "pages": pages,
                            "bottom_blank_mm": layout["bottom_blank_mm"] if layout else None,
                            "bottom_blank_percent": layout["bottom_blank_percent"] if layout else None,
                            "accepted": not issues})
            report = {**history[-1], "allowed_parameters": LIMITS, "layout": layout,
                      "issues": issues, "history": history[-6:], "next_step": guidance(pages, layout, current)}
            self.last_layout_report = report
            details = "\n排版测量（根据结果选择下一组参数，不要机械重复）：\n" + json.dumps(report, ensure_ascii=False)
            if issues:
                return ("编译为 1 页，但质量检查未通过，PDF 未交付。" if pages == 1 else f"编译结果为 {pages} 页，PDF 未交付。") + "\n" + "\n".join(issues) + details
            successful = True
            return (f"编译成功：{pdf.name}（1 页）；页底留白 {layout['bottom_blank_percent']:.2f}%"
                    f"（{layout['bottom_blank_mm']:.2f} mm），上限 {layout['max_bottom_blank_percent']:.2f}%。") + details
        except FileNotFoundError:
            return "编译失败：未安装 xelatex，请安装 LaTeX 环境"
        except asyncio.TimeoutError:
            return "编译失败：超过 120 秒"
        finally:
            if not successful:
                pdf.unlink(missing_ok=True)
            for suffix in (".aux", ".log", ".out", ".fls", ".fdb_latexmk", ".synctex.gz"):
                tex.with_suffix(suffix).unlink(missing_ok=True)

    async def preview_pdf(self, tex_file: str) -> PreviewResult:
        """查看最近编译的真实 PDF 整页截图（含未通过一页/质量检查的草稿）；修改后须先重新编译。"""
        tex = self.resolve(tex_file, write=True)
        if tex.suffix != ".tex" or not tex.is_file():
            raise ValueError("需要本轮已编译的 .tex 文件")
        metadata, images = await render_preview(tex, self.preview_dir, self.resolve("项目经历库.md"))
        return PreviewResult(json.dumps(metadata, ensure_ascii=False), images, tex_file, metadata)
