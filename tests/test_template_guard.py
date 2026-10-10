import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from starlette.testclient import TestClient
from backend.api import create_app
from backend.agent_core import AgentCore
from backend.layout_tuning import apply_parameters
from backend.session_store import SessionStore, NotFound
from backend.task_manager import TaskManager
from backend.template_library import TemplateLibrary
from backend.template_guard import verify_template
from backend.tools import LocalTools
from test_backend import FakeCore, MockClient, Message
from test_pdf_preview import actions, response

ROOT = Path(__file__).resolve().parents[1]
CN = (ROOT/'模板/resume-中文模板.tex').read_text()
EN = (ROOT/'模板/resume-en-template.tex').read_text()


class AttachmentCore(FakeCore):
    async def run_turn(self, uid, sid, tid, message, emit, attachments=None):
        return await super().run_turn(uid, sid, tid, message, emit)


class GuardTests(unittest.TestCase):
    def test_permitted_edits_and_wrong_style(self):
        verify_template(CN.replace('张明', '用户'), CN)
        verify_template(apply_parameters(CN, '{"line_spread":1.1,"bottom_mm":10}'), CN)
        verify_template(CN.replace('bottom=1.1cm','bottom=10mm'), CN)
        for wrong in [EN, CN.replace('0.9pt', '0.5pt'), CN.replace('left=1.3cm','left=2cm'),
                      CN.replace('texgyretermes-regular.otf','another-font.otf'),
                      CN.replace(r'\linespread{1.08}',r'\linespread{2}'),
                      CN.replace(r'\begin{document}',r'\begin{document}\renewcommand{\entry}[2]{#1}')]:
            with self.assertRaises(ValueError): verify_template(wrong, CN)

    def test_http_uses_visible_template_for_json_and_attachments(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(directory, AttachmentCore, allow_legacy_users=True)) as client:
            user=client.post('/users').json();headers={'Authorization':'Bearer '+user['token']}
            sid=client.post('/sessions',headers=headers).json()['session_id'];base=f'/sessions/{sid}'
            templates=client.get('/templates',headers=headers).json()['templates']
            visible,changed=templates[0]['id'],templates[1]['id']
            client.put('/templates/selection',headers=headers,json={'template_id':changed})
            for multipart in [False,True]:
                options={'data':{'message':'hello','template_id':visible},'files':{'files':('info.txt',b'test','text/plain')}} if multipart else {'json':{'message':'hello','template_id':visible}}
                sent=client.post(base+'/messages',headers=headers,**options)
                self.assertEqual(sent.status_code,202,sent.text)
                tid=sent.json()['task_id']
                result=client.get(base+'/tasks/'+tid,headers=headers).json()
                self.assertEqual(result['selected_template']['id'],visible)
                client.get(base+'/tasks/'+tid+'/stream',headers=headers)
                self.assertEqual(client.get(base+'/tasks/'+tid,headers=headers).json()['status'],'completed')
            self.assertEqual(client.post(base+'/messages',headers=headers,json={'message':'hi','template_id':'missing'}).status_code,404)
            self.assertEqual(client.post(base+'/messages',headers=headers,json={'message':'hi','template_id':[]}).status_code,400)


class LockedTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_source_is_immutable_and_in_model_context(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id']
            path=Path(directory)/'template.tex';path.write_text(CN)
            item={'id':'fixture','name':'Chosen','path':path,'status':'ready','source':'system'}
            test=self
            class Reader(MockClient):
                async def create(self,**kwargs):
                    data=json.loads(kwargs['messages'][1]['content'].split('\n',1)[1])
                    test.assertEqual(data['selected_template']['source'],CN)
                    return response(Message(content='Current template confirmed.'))
            manager=TaskManager(store,AgentCore(store,client_factory=Reader))
            await manager.semaphore.acquire()
            with patch('backend.template_library.TemplateLibrary.builtins',return_value=[item]),patch.dict(os.environ,AGENT_API_KEY='test',AGENT_MODEL='test'):
                tid=manager.submit(uid,sid,'hello')
                path.write_text(EN)
                manager.semaphore.release();await asyncio.gather(*manager.running)
            self.assertEqual(store.task(uid,sid,tid)['status'],'completed')
            await manager.close()

    async def test_model_cannot_deliver_previous_english_template(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id']
            class WrongTemplate(MockClient):
                async def create(self,**kwargs):
                    self.calls+=1
                    if self.calls==1:
                        return response(actions(('write_file',{'path':'wrong.tex','content':EN}),('compile_pdf',{'tex_file':'wrong.tex'})))
                    result=kwargs['messages'][-1]['content']
                    assert '模板不一致' in result,result
                    return response(Message(content='Done'))
            manager=TaskManager(store,AgentCore(store,client_factory=WrongTemplate))
            with patch.dict(os.environ,AGENT_API_KEY='test',AGENT_MODEL='test'):
                tid=manager.submit(uid,sid,'new resume');await asyncio.gather(*manager.running)
            result=store.task(uid,sid,tid)
            self.assertEqual(result['status'],'failed');self.assertIn('模板不一致',result['error']);self.assertEqual(result['files'],[])
            self.assertFalse((store.workspace(uid)/'resumes'/tid/'wrong.pdf').exists())
            await manager.close()

    async def test_stale_template_reads_recover_without_reading_wrong_source(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id'];tid=store.create_task(uid,sid,'test')
            selected=TemplateLibrary(store).snapshot(uid,sid,tid);workspace=store.workspace(uid)
            async with LocalTools(workspace,workspace/'resumes'/tid) as tools:
                tools.selected_template_path=(workspace/selected['path']).resolve()
                tools.selected_template_source = CN
                direct = json.loads(await tools.call('resume__read_selected_template', {}))
                self.assertEqual(direct['path'], selected['path'])
                self.assertEqual(direct['content'], CN)
                with self.assertRaises(ValueError):
                    await tools.call('resume__read_selected_template', {'path': '模板/resume-en-template.tex'})
                stale = workspace/'模板/tasks/old-task.tex'
                stale.write_text(EN)
                for requested in ['模板/resume-en-template.tex', '模板/tasks/old-task.tex', '模板/tasks/missing.tex']:
                    result = json.loads(await tools.call('resume__read_file', {'path': requested}))
                    self.assertTrue(result['redirected'])
                    self.assertEqual(result['requested_path'], requested)
                    self.assertEqual(result['path'], selected['path'])
                    self.assertEqual(result['content'], CN)
                self.assertIn('工具执行出错', await tools.call('resume__read_file', {'path': '模板/../../secret.tex'}))
                self.assertIn('content',await tools.call('resume__read_file',{'path':selected['path']}))
                self.assertNotIn('本次已锁定',await tools.call('resume__read_file',{'path':'项目经历库.md'}))

    async def test_model_recovers_from_historical_template_path(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id']
            test=self
            class StaleReader(MockClient):
                async def create(self, **kwargs):
                    self.calls += 1
                    selected=json.loads(kwargs['messages'][1]['content'].split('\n',1)[1])['selected_template']
                    read_tool=next(t['function'] for t in kwargs['tools'] if t['function']['name']=='resume__read_selected_template')
                    test.assertEqual(read_tool['parameters']['properties'], {})
                    if self.calls == 1:
                        return response(actions(('read_file', {'path':'模板/tasks/previous-task.tex'})))
                    result=json.loads(kwargs['messages'][-1]['content'])
                    test.assertEqual(result['path'], selected['path'])
                    test.assertEqual(result['content'], CN)
                    test.assertTrue(result['redirected'])
                    return response(Message(content='已读取本次模板，可以继续。'))
            manager=TaskManager(store,AgentCore(store,client_factory=StaleReader))
            with patch.dict(os.environ,AGENT_API_KEY='test',AGENT_MODEL='test'):
                tid=manager.submit(uid,sid,'继续使用所选模板');await asyncio.gather(*manager.running)
            self.assertEqual(store.task(uid,sid,tid)['status'],'completed')
            await manager.close()

    async def test_parameterless_template_read_follows_backend_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id']
            library=TemplateLibrary(store)
            items=library.builtins()
            for item in [items[0], items[1], items[1]]:
                library.select(uid, item['id'])
                tid=store.create_task(uid,sid,'test')
                selected=library.snapshot(uid,sid,tid)
                workspace=store.workspace(uid)
                async with LocalTools(workspace,workspace/'resumes'/tid) as tools:
                    tools.selected_template_path=(workspace/selected['path']).resolve()
                    tools.selected_template_source=item['path'].read_text()
                    result=json.loads(await tools.call('resume__read_selected_template', {}))
                    self.assertEqual(result['content'], item['path'].read_text())
                    self.assertEqual(selected['id'], item['id'])
                    tools.selected_template_path.write_text('tampered')
                    self.assertIn('快照发生变化', await tools.call('resume__read_selected_template', {}))
                store.status(tid, 'completed')
