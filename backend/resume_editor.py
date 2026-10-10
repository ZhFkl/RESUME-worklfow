"""Editable LaTeX paired with an immutable, user-owned PDF version."""
import io
import re
import shutil
import zipfile
from pathlib import Path

from backend.agent_core import AgentError
from backend.session_store import Busy, NotFound
from backend.tex_edits import source_hash
from backend.tools import LocalTools


class ResumeEditor:
    def __init__(self, store):
        self.store = store

    def source(self, uid, file_id):
        with self.store.db() as db:
            row = db.execute(
                "SELECT f.*,t.status FROM files f JOIN tasks t ON t.id=f.task_id "
                "WHERE f.id=? AND f.user_id=?", (file_id, uid)).fetchone()
        if row is None or row['status'] != 'completed' or Path(row['path']).suffix != '.pdf':
            raise NotFound('简历版本不存在')
        root = self.store.workspace(uid).resolve()
        boundary = root / 'resumes' / row['task_id']
        pdf = root / row['path']
        tex = pdf.with_suffix('.tex')
        for path in (pdf, tex):
            if path.is_symlink() or not path.resolve().is_relative_to(boundary) or not path.is_file():
                raise NotFound('该版本的 PDF 或 LaTeX 源码不存在')
        content = tex.read_text(encoding='utf-8')
        return {'file_id': file_id, 'session_id': row['session_id'], 'name': tex.name,
                'content': content, 'revision': source_hash(content), 'path': tex}

    def bundle(self, uid, file_id):
        source = self.source(uid, file_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(source['name'], source['content'])
            photo = source['path'].parent / 'photo.jpg'
            if photo.is_file() and not photo.is_symlink():
                archive.write(photo, 'photo.jpg')
        return buffer.getvalue()

    def prepare(self, uid, file_id, content, revision):
        if not isinstance(content, str) or not content.strip() or len(content) > 500000:
            raise ValueError('LaTeX 源码需为 1～50 万个字符')
        source = self.source(uid, file_id)
        if revision != source['revision']:
            raise Busy('原版本源码已变化，请重新打开编辑器后再编译')
        sid = source['session_id']
        tid = self.store.create_task(uid, sid, '手动编辑并编译：' + source['name'],
                                     'latex', {'parent_file_id': file_id, 'name': source['name']})
        output = self.store.workspace(uid) / 'resumes' / tid
        try:
            output.mkdir(parents=True)
            (output / source['name']).write_text(content, encoding='utf-8')
            photo = source['path'].parent / 'photo.jpg'
            if photo.is_file() and not photo.is_symlink():
                shutil.copyfile(photo, output / 'photo.jpg')
        except BaseException:
            self.store.status(tid, 'failed', '源码保存失败，请重新编译')
            shutil.rmtree(output, ignore_errors=True)
            raise
        return tid, sid, source['name']

    async def run(self, uid, sid, tid, name, emit):
        root = self.store.workspace(uid)
        output = root / 'resumes' / tid
        tex = output / name
        emit({'type': 'latex_compiling'})
        async with LocalTools(root, output, snapshot_profile=False) as tools:
            result = await tools.compile_pdf(name)
            if tools.last_layout_report:
                emit({'type': 'layout_checked', **tools.last_layout_report})
            if not tex.with_suffix('.pdf').is_file():
                # Return compiler errors and line numbers, without the verbose measurement JSON.
                detail = result.split('\n排版测量')[0]
                error = re.search(r'^! .+', detail, re.MULTILINE)
                if error:
                    detail = '编译失败：\n' + '\n'.join(detail[error.start():].splitlines()[:8])
                raise AgentError(detail)
            self.store.complete(uid, sid, tid, self.store.history(uid, sid),
                                '手动编译完成，已保存新的简历版本，请检查 PDF 预览。',
                                [tex, tex.with_suffix('.pdf')])
