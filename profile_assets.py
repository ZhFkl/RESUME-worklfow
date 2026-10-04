"""用户明确上传的证件照；与文字素材分开保存，每个生成任务固定一份副本。"""
import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from uploaded_files import convert_image

MAX_PHOTO_BYTES = 5 * 1024 * 1024


def photo_path(workspace):
    return Path(workspace) / "assets" / "photo.jpg"


def photo_info(workspace):
    path = photo_path(workspace)
    return {"has_photo": path.is_file(),
            "revision": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}


def normalize_photo(raw, destination):
    from PIL import Image
    png = destination.with_suffix(".png")
    convert_image(raw, png)
    with Image.open(png) as image:
        image.convert("RGB").save(destination, "JPEG", quality=95)


async def save_photo(store, uid, upload):
    from session_store import Busy
    workspace = store.workspace(uid)
    if store.active_task(uid):
        raise Busy("任务正在处理，请完成后再更新证件照")
    raw = await upload.read(MAX_PHOTO_BYTES + 1)
    if not raw or len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("证件照需为不超过 5 MB 的 JPG、PNG 或 WebP 图片")
    if Path(upload.filename or "").suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("证件照仅支持 JPG、PNG 或 WebP 图片")
    destination = photo_path(workspace)
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="photo-", dir=destination.parent) as directory:
        prepared = Path(directory) / "photo.jpg"
        await asyncio.to_thread(normalize_photo, raw, prepared)
        # 转换期间允许 HTTP 继续服务；落盘前再次检查，不改变正在生成任务的照片。
        if store.active_task(uid):
            raise Busy("任务已开始，请完成后再更新证件照")
        os.replace(prepared, destination)
    return photo_info(workspace)


def snapshot_photo(workspace, output):
    source = photo_path(workspace)
    target = Path(output) / "photo.jpg"
    if source.is_file() and not target.exists():
        shutil.copyfile(source, target)
    return target.is_file()


def photo_digest(directory):
    path = Path(directory) / "photo.jpg"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def photo_issues(document, photo):
    """检查 PDF 中实际绘制的照片，不能只依靠 tex 中存在文件名。"""
    from PIL import Image
    from pypdf.generic import ContentStream
    if not photo.is_file():
        return []
    original_bytes = photo.read_bytes()
    with Image.open(photo) as source:
        source = source.convert("RGB")
        size, pixels = source.size, source.tobytes()
    for page in document.pages:
        painted = set()
        exact_matches = []
        def walk(stream, resources, seen):
            for operands, operator in ContentStream(stream, document).operations:
                if operator != b"Do":
                    continue
                objects = resources.get("/XObject", {})
                objects = objects.get_object() if hasattr(objects, "get_object") else objects
                ref = objects.get(operands[0])
                if ref is None:
                    continue
                obj = ref.get_object()
                key = (getattr(ref, "idnum", None), getattr(ref, "generation", None))
                if obj.get("/Subtype") == "/Image":
                    painted.add(key)
                    if obj.get("/Filter") == "/DCTDecode" and obj.get_data() == original_bytes:
                        exact_matches.append(key)
                elif obj.get("/Subtype") == "/Form" and key not in seen:
                    walk(obj, obj.get("/Resources", resources), seen | {key})
        walk(page.get_contents(), page["/Resources"], set())
        if exact_matches:
            return []
        for image in page.images:
            ref = image.indirect_reference
            if ref and (ref.idnum, ref.generation) in painted:
                actual = image.image.convert("RGB")
                if actual.size == size and actual.tobytes() == pixels:
                    return []
    return ["已保存用户证件照，但 PDF 未绘制本次 photo.jpg。请在页眉用 \\includegraphics[width=2.4cm,height=3.2cm,keepaspectratio]{photo.jpg} 加入照片，并重新编译、查看截图。"]
