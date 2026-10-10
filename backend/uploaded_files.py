"""原始附件在服务端保存、提取与转图；图片只在请求模型时编码。"""
import base64
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
import zipfile
from uuid import uuid4

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_PAGES = 10
MAX_IMAGES = 12
SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".md"}


async def save_upload(store, uid, sid, upload):
    from backend.session_store import now
    store.history(uid, sid)
    name = re.split(r"[/\\]", upload.filename or "")[-1]
    if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
        raise ValueError("附件文件名无效")
    suffix = Path(name).suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError("支持 PDF、DOCX、PNG/JPG/WebP 图片和 TXT/MD；旧版 Word 请另存 DOCX")
    ident = uuid4().hex
    root = store.workspace(uid)
    directory = root / "uploads" / ident
    directory.mkdir(parents=True)
    path = directory / ("original" + suffix)
    size = 0
    try:
        with path.open("wb") as stream:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ValueError("单个附件不能超过 15 MB")
                stream.write(chunk)
        if not size:
            raise ValueError("不能上传空文件")
        with store.db() as db:
            db.execute("INSERT INTO uploads VALUES (?,?,?,?,?,?,?)",
                       (ident, uid, sid, name, str(path.relative_to(root)), size, now()))
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return ident


def convert_image(data, destination):
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 20_000_000
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGBA")
                background = Image.new("RGBA", image.size, "white")
                image = Image.alpha_composite(background, image).convert("RGB")
                image.thumbnail((1800, 1800))
                image.save(destination, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("图片无效或像素过大，请重新导出较小图片") from exc


def prepare_file(source, destination):
    """在受超时约束的独立进程内运行，不阻塞 HTTP 服务。"""
    source, destination = Path(source), Path(destination)
    result = {"text": "", "images": [], "warning": ""}
    suffix = source.suffix.lower()
    destination.mkdir(parents=True, exist_ok=True)
    if suffix == ".pdf":
        from pypdf import PdfReader
        try:
            document = PdfReader(source)
            if document.is_encrypted and not document.decrypt(""):
                raise ValueError("PDF 受密码保护，请解除密码后上传")
            pages = len(document.pages)
            if not 1 <= pages <= MAX_PAGES:
                raise ValueError(f"单份 PDF 需为 1 至 {MAX_PAGES} 页，请拆分后上传")
            text = []
            for page in document.pages:
                text.append(page.extract_text() or "")
                if sum(map(len, text)) > 60000:
                    raise ValueError("单份附件文本超过 6 万字符，请拆分")
            result["text"] = "\n\n".join(text)
            # 所有页面都转图，扫描页与图表不会因无法抽取文本而丢失。
            rendered = subprocess.run(["pdftoppm", "-f", "1", "-l", str(pages), "-scale-to", "1800",
                                       "-png", str(source), str(destination / "page")],
                                      capture_output=True, timeout=45)
            if rendered.returncode:
                raise ValueError("PDF 页面渲染失败，请重新导出")
            result["images"] = [p.name for p in sorted(destination.glob("page-*.png"))]
            if len(result["images"]) != pages:
                raise ValueError("PDF 页面渲染不完整")
        except ValueError:
            raise
        except FileNotFoundError as exc:
            raise ValueError("服务器缺少 pdftoppm，无法读取 PDF 页面") from exc
        except Exception as exc:
            raise ValueError("PDF 无法读取或处理超时，请确认文件完整后重试") from exc
    elif suffix == ".docx":
        from docx import Document
        from docx.oxml.ns import qn
        try:
            with zipfile.ZipFile(source) as archive:
                if sum(info.file_size for info in archive.infolist()) > 40 * 1024 * 1024:
                    raise ValueError("Word 解压内容过大，请精简后上传")
                doc = Document(source)
                # 按 XML 顺序保留段落及表格中的文字。
                paragraphs = []
                for element in doc.element.body.iter(qn("w:p")):
                    paragraphs.append("".join(node.text or "" for node in element.iter(qn("w:t"))))
                result["text"] = "\n".join(paragraphs)
                media = [info for info in archive.infolist() if info.filename.startswith("word/media/") and not info.is_dir()]
                if len(media) > MAX_IMAGES:
                    raise ValueError("Word 图片超过 12 张，请精简后上传")
                for index, info in enumerate(media):
                    target = destination / f"image-{index:02d}.png"
                    try:
                        convert_image(archive.read(info), target)
                        result["images"].append(target.name)
                    except ValueError:
                        result["warning"] = "Word 中部分内嵌图形无法转换；这些内容需核对，不能推断补全。"
                extras = [part for section in doc.sections for part in (section.header, section.footer)]
                result["text"] += "\n" + "\n".join(p.text for part in extras for p in part.paragraphs if p.text)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Word 无法读取，请上传有效 DOCX 文件") from exc
    elif suffix in {".txt", ".md"}:
        try:
            result["text"] = source.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("文本文件请保存为 UTF-8 编码") from exc
    else:
        convert_image(source.read_bytes(), destination / "image.png")
        result["images"] = ["image.png"]
    if len(result["text"]) > 60000:
        raise ValueError("单份附件文本超过 6 万字符，请拆分")
    if not result["text"].strip() and not result["images"]:
        raise ValueError("附件没有可读取的文字或图片")
    return result


async def attachment_parts(store, uid, sid, ids, emit=lambda event: None):
    from backend.agent_core import AgentError
    from backend.process_runner import run_process
    from backend.paths import PROJECT_ROOT
    parts = []
    total_images = total_text = 0
    for ident in ids:
        record = store.upload(uid, sid, ident)
        source = record["file"]
        ready = source.parent / "prepared"
        manifest = ready / "manifest.json"
        cache_hit = manifest.exists()
        if not manifest.exists():
            emit({"type": "attachment_processing", "name": record["name"]})
            with tempfile.TemporaryDirectory(prefix="prepare-", dir=source.parent) as directory:
                result = await run_process([sys.executable, "-m", "backend.uploaded_files", str(source), directory], timeout=90, cwd=PROJECT_ROOT)
                report = Path(directory) / "manifest.json"
                if result.returncode or not report.exists():
                    raise AgentError("附件处理失败，请检查文件或服务器解析环境")
                info = json.loads(report.read_text())
                if "error" in info:
                    raise AgentError(info["error"])
                if ready.exists():
                    shutil.rmtree(ready)
                shutil.copytree(directory, ready)
        data = json.loads(manifest.read_text())
        emit({"type": "attachment_prepared", "cache_hit": cache_hit,
              "image_count": len(data["images"]), "text_chars": len(data["text"])})
        total_text += len(data["text"])
        total_images += len(data["images"])
        if total_text > 120000 or total_images > MAX_IMAGES:
            raise AgentError("本条消息附件合计超过 12 万字符或 12 张图片/页面，请拆分后发送")
        parts.append({"type": "text", "text": json.dumps({"attachment": record["name"], "text": data["text"],
                                                              "warning": data["warning"]}, ensure_ascii=False)})
        for image in data["images"]:
            raw = (ready / image).read_bytes()
            parts.append({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(raw).decode(), "detail": "high"}})
    return parts


if __name__ == "__main__":
    try:
        prepared = prepare_file(sys.argv[1], sys.argv[2])
    except ValueError as exc:
        prepared = {"error": str(exc)}
    Path(sys.argv[2], "manifest.json").write_text(json.dumps(prepared, ensure_ascii=False), encoding="utf-8")
