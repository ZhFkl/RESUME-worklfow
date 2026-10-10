import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from starlette.datastructures import UploadFile
from backend.api import create_app
from backend.session_store import SessionStore, NotFound
from backend.template_library import TemplateLibrary
from backend.template_importer import TemplateImporter
from backend.agent_core import AgentCore
from backend.task_manager import TaskManager
from backend.uploaded_files import save_upload
from test_backend import MockClient, Message

SAMPLE = r'\documentclass{article}\usepackage[a4paper,margin=12mm]{geometry}\pagestyle{empty}\begin{document}Sample Name\section*{Education}Sample University\section*{Experience}Describe a contribution.\end{document}'


class ConversionClient(MockClient):
    seen = []
    fail = False

    async def create(self, **kwargs):
        type(self).seen.append(kwargs['messages'])
        if type(self).fail:
            raise RuntimeError('provider private error')
        if isinstance(kwargs['messages'][-1]['content'], list) and '刚编译' in kwargs['messages'][-1]['content'][0].get('text', ''):
            return SimpleNamespace(choices=[SimpleNamespace(message=Message(content=json.dumps({'approved':True,'feedback':''})))])
        return SimpleNamespace(choices=[SimpleNamespace(message=Message(content=json.dumps({'latex':SAMPLE,'notes':'Single-column sample layout.'})))])


def importer(store):
    return TemplateImporter(store, client_factory=ConversionClient)


def wait_task(client, sid, tid, headers):
    for _ in range(300):
        task=client.get(f'/sessions/{sid}/tasks/{tid}',headers=headers).json()
        if task['status'] in {'completed','failed'}:
            return task
        time.sleep(.03)
    raise AssertionError('template task did not complete')


class TemplateAPITests(unittest.TestCase):
    def test_convert_select_download_isolation_and_retry(self):
        ConversionClient.seen=[];ConversionClient.fail=False
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,AGENT_API_KEY='test',AGENT_MODEL='test'):
            with TestClient(create_app(directory,template_importer_factory=importer, allow_legacy_users=True)) as client:
                a,b=[client.post('/users').json() for _ in range(2)]
                ha,hb=[{'Authorization':'Bearer '+user['token']} for user in (a,b)]
                sid=client.post('/sessions',headers=ha).json()['session_id']
                self.assertEqual(client.get('/templates').status_code,401)
                catalog=client.get('/templates',headers=ha).json()
                self.assertEqual(len(catalog['templates']),2)
                self.assertTrue(all(item['categories']==['engineering'] for item in catalog['templates']))
                before=client.get('/materials',headers=ha).json()
                binary=Path('模板/resume-en-template.pdf').read_bytes()
                def upload():
                    return client.post(f'/sessions/{sid}/templates',headers=ha,data={'name':'My layout'},files={'file':('reference.pdf',binary,'application/pdf')})
                result=upload();self.assertEqual(result.status_code,202,result.text)
                tid=result.json()['task_id'];ident=result.json()['template_id']
                self.assertEqual(client.put('/templates/selection',headers=ha,json={'template_id':ident}).status_code,409)
                task=wait_task(client,sid,tid,ha)
                self.assertEqual(task['status'],'completed',task.get('error'))
                self.assertEqual(task['files'],[])
                self.assertEqual(client.get('/versions',headers=ha).json()['files'],[])
                self.assertEqual(client.get('/materials',headers=ha).json(),before)
                self.assertEqual(client.get(f'/sessions/{sid}/messages',headers=ha).json()['messages'],[])
                self.assertTrue(any(part['type']=='image_url' for part in ConversionClient.seen[0][1]['content']))
                self.assertEqual(client.get(f'/templates/{ident}/files/tex',headers=ha).text,SAMPLE)
                self.assertTrue(client.get(f'/templates/{ident}/files/png',headers=ha).content.startswith(b'\x89PNG'))
                self.assertEqual(client.get(f'/templates/{ident}/files/original',headers=ha).content,binary)
                self.assertEqual(client.get(f'/templates/{ident}/files/pdf',headers=hb).status_code,404)
                self.assertEqual(client.put('/templates/selection',headers=hb,json={'template_id':ident}).status_code,404)
                self.assertEqual(client.put('/templates/selection',headers=ha,json={'template_id':ident}).status_code,200)
                personal=next(item for item in client.get('/templates',headers=ha).json()['templates'] if item['id']==ident)
                self.assertEqual(personal['categories'],[])
                self.assertEqual(client.get('/templates',headers=ha).json()['selected_id'],ident)
                self.assertEqual(len(client.get('/templates',headers=hb).json()['templates']),2)
                ConversionClient.fail=True
                failed=upload().json();task=wait_task(client,sid,failed['task_id'],ha)
                self.assertEqual(task['status'],'failed')
                self.assertNotIn('private',task['error'])
                ConversionClient.fail=False
                retry=client.post(f"/sessions/{sid}/tasks/{failed['task_id']}/retry",headers=ha).json()
                self.assertEqual(wait_task(client,sid,retry['task_id'],ha)['status'],'completed')
            with TestClient(create_app(directory,template_importer_factory=importer, allow_legacy_users=True)) as client:
                self.assertEqual(client.get('/templates',headers=ha).json()['selected_id'],ident)


class TemplateSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_visual_rejection_requires_repair_before_publication(self):
        for allow_repair in (True, False):
            with self.subTest(allow_repair=allow_repair), tempfile.TemporaryDirectory() as directory:
                class VisualClient(ConversionClient):
                    reviews = 0
                    fail = False
                    async def create(self, **kwargs):
                        message = kwargs['messages'][-1]['content']
                        if isinstance(message, list) and '刚编译' in message[0].get('text', ''):
                            type(self).reviews += 1
                            return SimpleNamespace(choices=[SimpleNamespace(message=Message(content=json.dumps({
                                'approved': allow_repair and type(self).reviews > 1, 'feedback':'分隔线压住标题，请移到标题下方'})))])
                        if type(self).reviews:
                            request = json.loads(message)
                            self_test.assertEqual(request['mode'], 'repair')
                            self_test.assertIn('分隔线压住标题', request['feedback'])
                            return SimpleNamespace(choices=[SimpleNamespace(message=Message(content=json.dumps({
                                'base_sha256': request['base_sha256'],
                                'edits': [{'old':'Sample Name', 'new':f'Sample Name {type(self).reviews}'}],
                            })))])
                        return await super().create(**kwargs)
                self_test = self
                store = SessionStore(directory)
                uid = store.create_user()['user_id']; sid = store.create_session(uid)['session_id']
                upload = UploadFile(io.BytesIO(Path('模板/resume-en-template.pdf').read_bytes()), filename='reference.pdf')
                ident = await save_upload(store, uid, sid, upload)
                manager = TaskManager(store, None, template_importer=TemplateImporter(store, client_factory=VisualClient))
                with patch.dict(os.environ, AGENT_API_KEY='test', AGENT_MODEL='test'):
                    tid = manager.submit_template(uid, sid, ident, 'Visual check')
                    await asyncio.gather(*manager.running)
                self.assertEqual(store.task(uid, sid, tid)['status'], 'completed' if allow_repair else 'failed')
                self.assertEqual(VisualClient.reviews, 2 if allow_repair else 3)
                item = TemplateLibrary(store).get(uid, 'custom-'+tid)
                self.assertEqual(item['path'].exists(), allow_repair)
                if allow_repair:
                    self.assertEqual(item['path'].read_text(), SAMPLE.replace('Sample Name', 'Sample Name 1'))
                    self.assertEqual((item['path'].parent/'notes.txt').read_text(), 'Single-column sample layout.')
                await manager.close()

    async def test_queued_selection_is_fixed_and_passed_to_model(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id']
            library=TemplateLibrary(store);items=library.builtins();chosen=items[-1]
            library.select(uid,chosen['id'])
            test=self
            class Reader(MockClient):
                async def create(self,**kwargs):
                    context=json.loads(kwargs['messages'][1]['content'].split('\n',1)[1])
                    selected=context['selected_template'];test.assertEqual(selected['id'],chosen['id'])
                    test.assertEqual((store.workspace(uid)/selected['path']).read_text(),chosen['path'].read_text())
                    return SimpleNamespace(choices=[SimpleNamespace(message=Message(content='Read selected template.'))])
            manager=TaskManager(store,AgentCore(store,client_factory=Reader),concurrency=1)
            await manager.semaphore.acquire()
            with patch.dict(os.environ,AGENT_API_KEY='test',AGENT_MODEL='test'):
                tid=manager.submit(uid,sid,'check template')
                library.select(uid,items[0]['id'])
                manager.semaphore.release()
                await asyncio.gather(*manager.running)
            self.assertEqual(store.task(uid,sid,tid)['status'],'completed')
            await manager.close()

    def test_new_system_templates_are_discovered(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as catalog:
            store=SessionStore(directory);uid=store.create_user()['user_id']
            root=Path(catalog);(root/'模板').mkdir();(root/'模板/new.tex').write_text(SAMPLE)
            (root/'模板/new.json').write_text(json.dumps({'name':'New template','description':'New style'}))
            with patch('backend.template_library.BASE_DIR',root):
                data=TemplateLibrary(store).catalog(uid)
                self.assertEqual(data['templates'][0]['name'],'New template')
                self.assertIsNone(data['templates'][0]['pdf_url'])
                self.assertEqual(data['templates'][0]['categories'],[])

    def test_system_template_role_metadata(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as catalog:
            store=SessionStore(directory);uid=store.create_user()['user_id']
            root=Path(catalog);(root/'模板').mkdir();(root/'模板/new.tex').write_text(SAMPLE)
            meta=root/'模板/new.json'
            with patch('backend.template_library.BASE_DIR',root):
                for raw,expected in [(['design','product','design','invalid'],['product','design']),
                                     ('engineering',[]),(None,[]),([{},42],[]),([],[])]:
                    meta.write_text(json.dumps({'name':'Specialist','categories':raw}))
                    with self.subTest(categories=raw):
                        result=TemplateLibrary(store).catalog(uid)['templates'][0]
                        self.assertEqual(result['categories'],expected)
                        self.assertNotIn('path',result)
