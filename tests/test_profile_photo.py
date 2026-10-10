import json
from backend.tex_edits import source_hash
import asyncio
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader
from starlette.testclient import TestClient

from backend.api import create_app
from backend.profile_assets import photo_path, photo_issues, snapshot_photo
from backend.session_store import SessionStore
from backend.tools import LocalTools
from backend.process_runner import run_process
from resume_fixtures import full_page_tex


def photo_bytes():
    stream = io.BytesIO()
    image = Image.new('RGB', (60, 80), 'blue')
    image.paste('white', (15, 15, 45, 45))
    image.save(stream, 'PNG')
    return stream.getvalue()


class PhotoAPITests(unittest.TestCase):
    def test_upload_ownership_replacement_busy_and_removal(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(directory, allow_legacy_users=True)) as client:
            users = [client.post('/users').json() for _ in range(2)]
            a, b = [{'Authorization':'Bearer '+u['token']} for u in users]
            self.assertEqual(client.get('/profile').status_code, 401)
            self.assertFalse(client.get('/profile', headers=a).json()['has_photo'])
            response = client.put('/profile/photo', headers=a, files={'photo':('id.png',photo_bytes(),'image/png')})
            self.assertEqual(response.status_code,200)
            revision = response.json()['revision']
            data = client.get('/profile/photo',headers=a)
            self.assertEqual(Image.open(io.BytesIO(data.content)).format,'JPEG')
            self.assertEqual(client.get('/profile/photo',headers=b).status_code,404)
            invalid = client.put('/profile/photo',headers=a,files={'photo':('bad.png',b'bad','image/png')})
            self.assertEqual(invalid.status_code,400)
            self.assertEqual(client.get('/profile',headers=a).json()['revision'],revision)
            store=client.app.state.store;uid=users[0]['user_id'];sid=store.create_session(uid)['session_id']
            tid=store.create_task(uid,sid,'queued')
            self.assertEqual(client.delete('/profile/photo',headers=a).status_code,409)
            self.assertEqual(client.put('/profile/photo',headers=a,files={'photo':('id.png',photo_bytes())}).status_code,409)
            store.status(tid,'failed')
            self.assertFalse(client.delete('/profile/photo',headers=a).json()['has_photo'])

    def test_changed_backend_refuses_new_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'frontend').mkdir()
            (root/'frontend/index.html').write_text('test workspace')
            (root/'backend').mkdir()
            source=root/'backend/rules.py'
            source.write_text('REVISION = 1\n')
            # Only a nested backend file changes; root-level files stay identical.
            with patch('backend.api.BASE_DIR',root), TestClient(create_app(root/'data', allow_legacy_users=True)) as client:
                self.assertEqual(client.get('/').text, 'test workspace')
                self.assertFalse(client.get('/health').json()['restart_required'])
                user=client.post('/users').json();headers={'Authorization':'Bearer '+user['token']}
                sid=client.post('/sessions',headers=headers).json()['session_id']
                source.write_text('REVISION = 2\n')
                self.assertTrue(client.get('/health').json()['restart_required'])
                result=client.post(f'/sessions/{sid}/messages',headers=headers,json={'message':'generate'})
                self.assertEqual(result.status_code,503)
                self.assertIn('重启',result.json()['error'])
                self.assertEqual(client.get(f'/sessions/{sid}/tasks',headers=headers).json()['tasks'],[])


class PhotoCompileTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_required_in_pdf_and_snapshot_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];root=store.workspace(uid)
            path=photo_path(root);path.parent.mkdir();Image.open(io.BytesIO(photo_bytes())).save(path,'JPEG')
            output=root/'resumes'/'test';output.mkdir(parents=True)
            snapshot_photo(root,output);original=(output/'photo.jpg').read_bytes()
            Image.new('RGB',(40,40),'red').save(path,'JPEG')
            snapshot_photo(root,output);self.assertEqual((output/'photo.jpg').read_bytes(),original)
            async with LocalTools(root,output) as tools:
                await tools.call('resume__write_file',{'path':'resume.tex','content':full_page_tex()})
                missing=await tools.call('resume__compile_pdf',{'tex_file':'resume.tex'})
                self.assertIn('未绘制',missing);self.assertFalse((output/'resume.pdf').exists())
                # Overlay a small test image without changing the fixture's bottom baseline.
                source=full_page_tex(first_item=r'Resume \rlap{\raisebox{-1cm}[0pt][0pt]{\includegraphics[width=5mm]{photo.jpg}}}').replace(
                    r'\begin{document}',r'\usepackage{graphicx}'+'\n'+r'\begin{document}')
                await tools.call('resume__edit_file',{'path':'resume.tex','base_sha256':source_hash(full_page_tex()),'edits_json':json.dumps([{'old':r'\begin{document}','new':r'\usepackage{graphicx}'+ '\n' +r'\begin{document}'},{'old':r'\item Resume','new':r'\item Resume \rlap{\raisebox{-1cm}[0pt][0pt]{\includegraphics[width=5mm]{photo.jpg}}}'}])})
                success=await tools.call('resume__compile_pdf',{'tex_file':'resume.tex'})
                self.assertIn('编译成功',success)
                preview=await tools.call('resume__preview_pdf',{'tex_file':'resume.tex'})
                self.assertTrue(preview.current(root,output))
                Image.new('RGB',(40,40),'green').save(output/'photo.jpg','JPEG')
                self.assertFalse(preview.current(root,output))

    async def test_both_templates_draw_saved_photo(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);photo=root/'photo.jpg'
            Image.open(io.BytesIO(photo_bytes())).save(photo,'JPEG')
            for source in Path('模板').glob('*.tex'):
                target=root/source.name;target.write_bytes(source.read_bytes())
                result=await run_process(['xelatex','-no-shell-escape','-halt-on-error','-interaction=nonstopmode',target.name],cwd=root,timeout=120)
                self.assertEqual(result.returncode,0,result.stdout[-1200:])
                self.assertEqual(photo_issues(PdfReader(target.with_suffix('.pdf')),photo),[])
