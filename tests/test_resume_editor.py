import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from starlette.testclient import TestClient
from backend.api import create_app
from backend.resume_editor import ResumeEditor
from resume_fixtures import full_page_tex
from test_version_delete import seed


class ResumeEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(self.temp.name, allow_legacy_users=True))
        self.client.__enter__()
        self.store = self.client.app.state.store
        account = self.client.post('/users').json()
        self.uid = account['user_id']
        self.headers = {'Authorization': 'Bearer ' + account['token']}
        self.sid = self.client.post('/sessions', headers=self.headers).json()['session_id']
        self.tid, self.output, records = seed(self.store, self.uid, self.sid, ('original',))
        (self.output / 'original.tex').write_text(full_page_tex(), encoding='utf-8')
        self.fid = records['original.pdf']['id']
        self.route = '/versions/' + self.fid

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def compile(self, content):
        source = self.client.get(self.route + '/source', headers=self.headers).json()
        response = self.client.post(self.route + '/compile', headers=self.headers,
                                    json={'content': content, 'revision': source['revision']})
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()
        for _ in range(400):
            result = self.client.get('/sessions/' + self.sid + '/tasks/' + task['task_id'], headers=self.headers).json()
            if result['status'] in ('completed', 'failed'):
                return result
            time.sleep(.05)
        self.fail('Compilation did not finish')

    def test_source_bundle_ownership_revision_and_original_photo(self):
        self.assertEqual(self.client.get(self.route + '/source').status_code, 401)
        other = self.client.post('/users').json()
        headers = {'Authorization': 'Bearer ' + other['token']}
        for suffix in ('/source', '/source?download=1'):
            self.assertEqual(self.client.get(self.route + suffix, headers=headers).status_code, 404)
        self.assertEqual(self.client.post(self.route + '/compile', headers=headers,
                         json={'content': 'text', 'revision': 'x'}).status_code, 404)
        source = self.client.get(self.route + '/source', headers=self.headers).json()
        self.assertEqual(source['content'], full_page_tex())
        self.assertNotIn('path', source)
        self.assertEqual(self.client.post(self.route + '/compile', headers=self.headers,
                         json={'content': source['content'], 'revision': 'stale'}).status_code, 409)
        self.assertEqual(self.client.post(self.route + '/compile', headers=self.headers,
                         json={'content': '', 'revision': source['revision']}).status_code, 400)
        (self.output / 'photo.jpg').write_bytes(b'historical photo')
        root = self.store.workspace(self.uid)
        (root / 'assets').mkdir()
        (root / 'assets/photo.jpg').write_bytes(b'current photo')
        bundle = self.client.get(self.route + '/source?download=1', headers=self.headers)
        self.assertEqual(bundle.headers['content-type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            self.assertEqual(set(archive.namelist()), {'original.tex', 'photo.jpg'})
            self.assertEqual(archive.read('photo.jpg'), b'historical photo')
        tid, _, _ = ResumeEditor(self.store).prepare(self.uid, self.fid, source['content'], source['revision'])
        self.assertEqual((root / 'resumes' / tid / 'photo.jpg').read_bytes(), b'historical photo')
        self.assertEqual(self.client.post(self.route + '/compile', headers=self.headers,
                         json={'content': source['content'], 'revision': source['revision']}).status_code, 409)

    def test_real_compile_pairs_new_source_pdf_without_changing_original_or_materials(self):
        original = {path.name: path.read_bytes() for path in self.output.iterdir()}
        materials = self.store.materials(self.uid)
        # An old resume without a photo must not gain the user's newly uploaded photo.
        root = self.store.workspace(self.uid)
        (root / 'assets').mkdir()
        (root / 'assets/photo.jpg').write_bytes(b'not the historical photo')
        revised = full_page_tex('Manually revised resume')
        task = self.compile(revised)
        self.assertEqual(task['status'], 'completed', task['error'])
        self.assertEqual(len(self.store.versions(self.uid)), 2)
        pdf = next(item for item in task['files'] if item['name'].endswith('.pdf'))
        source = self.client.get('/versions/' + pdf['file_id'] + '/source', headers=self.headers).json()
        self.assertEqual(source['content'], revised)
        from pypdf import PdfReader
        pdf_path = self.store.file(self.uid, self.sid, pdf['file_id'])
        self.assertIn('Manually revised resume', PdfReader(pdf_path).pages[0].extract_text())
        self.assertFalse((pdf_path.parent / 'photo.jpg').exists())
        self.assertEqual({path.name: path.read_bytes() for path in self.output.iterdir()}, original)
        self.assertEqual(self.store.materials(self.uid), materials)
        self.assertEqual({p.suffix for p in pdf_path.parent.iterdir()}, {'.tex', '.pdf'})

    def test_errors_and_whitespace_failure_do_not_replace_old_pdf(self):
        for content, expected in [(r'\documentclass{article}\begin{document}\badcommand\end{document}', '编译失败'),
                                  (r'\documentclass{article}\pagestyle{empty}\begin{document}Too short.\end{document}', '质量检查未通过')]:
            task = self.compile(content)
            self.assertEqual(task['status'], 'failed')
            self.assertIn(expected, task['error'])
            self.assertEqual(task['files'], [])
            self.assertEqual(len(self.store.versions(self.uid)), 1)
            self.assertEqual((self.output / 'original.pdf').read_bytes(), b'%PDF-test')
            self.assertEqual(self.client.post('/sessions/' + self.sid + '/tasks/' + task['id'] + '/retry',
                                             headers=self.headers).status_code, 409)

    def test_source_path_cannot_escape_task(self):
        with self.store.db() as db:
            db.execute('UPDATE files SET path=? WHERE id=?', ('../outside.pdf', self.fid))
        self.assertEqual(self.client.get(self.route + '/source', headers=self.headers).status_code, 404)

    def test_plain_text_edit_api_and_real_pdf(self):
        content = full_page_tex(first_item=r'Created \textbf{Backend} APIs. Cost 5\%.')
        response = self.client.post(self.route + '/text', headers=self.headers, json={'content': content})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fields'][0]['text'], 'Created Backend APIs. Cost 5%.')
        response = self.client.post(self.route + '/text', headers=self.headers,
                                   json={'content': content, 'edits': {'0': 'Created Backend & data APIs. Cost 8%.'}})
        revised = response.json()['content']
        self.assertIn(r'\textbf{Backend}', revised)
        self.assertIn(r'\&', revised)
        task = self.compile(revised)
        self.assertEqual(task['status'], 'completed', task['error'])
        from pypdf import PdfReader
        pdf = next(item for item in task['files'] if item['name'].endswith('.pdf'))
        text = PdfReader(self.store.file(self.uid, self.sid, pdf['file_id'])).pages[0].extract_text()
        self.assertIn('Backend & data APIs. Cost 8%', text)
        other = self.client.post('/users').json()
        headers = {'Authorization': 'Bearer ' + other['token']}
        self.assertEqual(self.client.post(self.route + '/text', headers=headers, json={'content': content}).status_code, 404)
        self.assertEqual(self.client.post(self.route + '/text', headers=self.headers, json={'content': content, 'edits': {'missing': 'x'}}).status_code, 400)

    def test_bold_and_unbold_are_drawn_in_pdf(self):
        from pypdf import PdfReader
        content = full_page_tex('Editor fixture')
        for bold in (True, False):
            response = self.client.post(self.route + '/text', headers=self.headers,
                json={'content': content, 'formatting': {'field_id': '0', 'start': 0, 'end': 6, 'bold': bold}})
            self.assertEqual(response.status_code, 200, response.text)
            content = response.json()['content']
            self.assertEqual(response.json()['fields'][0]['bold'], [[0, 6]] if bold else [])
            task = self.compile(content)
            self.assertEqual(task['status'], 'completed', task['error'])
            pdf = next(item for item in task['files'] if item['name'].endswith('.pdf'))
            fonts = []
            PdfReader(self.store.file(self.uid, self.sid, pdf['file_id'])).pages[0].extract_text(
                visitor_text=lambda text,cm,tm,font,size: fonts.append(str(font.get('/BaseFont',''))) if 'Editor' in text else None)
            self.assertTrue(fonts)
            self.assertEqual(any('CMBX' in name or 'Bold' in name for name in fonts), bold)


if __name__ == '__main__':
    unittest.main()
