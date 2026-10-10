from backend.tex_edits import source_hash
from resume_fixtures import full_page_tex
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from backend.agent_core import AgentCore, AgentError
from backend.api import create_app
from backend.tools import LocalTools
from backend.session_store import Busy, NotFound, SessionStore
from backend.task_manager import TaskManager, failure_message
from backend.workspace import resolve_file


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(self.temp.name)
        self.a = self.store.create_user()
        self.b = self.store.create_user()
        self.sid = self.store.create_session(self.a['user_id'])['session_id']

    def test_persistent_shared_user_material_and_private_history(self):
        uid = self.a['user_id']
        kb = self.store.workspace(uid) / '项目经历库.md'
        kb.write_text('用户确认的项目', encoding='utf-8')
        sid2 = self.store.create_session(uid)['session_id']
        store2 = SessionStore(self.temp.name)
        self.assertEqual(store2.authenticate(self.a['token']), uid)
        self.assertEqual(store2.history(uid, sid2), [])
        self.assertEqual((store2.workspace(uid) / '项目经历库.md').read_text(), '用户确认的项目')
        self.assertNotIn('用户确认的项目', (store2.workspace(self.b['user_id']) / '项目经历库.md').read_text())
        with self.assertRaises(NotFound):
            store2.history(self.b['user_id'], self.sid)

    def test_user_lock_and_restart_recovery(self):
        uid = self.a['user_id']
        task = self.store.create_task(uid, self.sid, 'hello')
        sid2 = self.store.create_session(uid)['session_id']
        with self.assertRaises(Busy):
            self.store.create_task(uid, sid2, 'second')
        with self.store.runtime():
            with self.assertRaises(RuntimeError):
                with SessionStore(self.temp.name).runtime():
                    pass
        self.assertEqual(self.store.task(uid, self.sid, task)['status'], 'failed')
        self.store.create_task(uid, sid2, 'retry')

    def test_path_traversal_and_symlink(self):
        root = self.store.workspace(self.a['user_id'])
        output = root / 'resumes' / 'test'
        output.mkdir(parents=True)
        for name in ('../../.env', '/etc/passwd', '模板/resume.tex'):
            with self.assertRaises(ValueError):
                resolve_file(name, root, output, write=True)
        (output / 'link.tex').symlink_to(Path(self.temp.name) / 'outside.tex')
        with self.assertRaises(ValueError):
            resolve_file('link.tex', root, output, write=True)
        self.assertEqual(resolve_file('resume.tex', root, output, write=True), output / 'resume.tex')

    def test_nested_provider_error_is_safe_and_actionable(self):
        error = RuntimeError('private provider response')
        error.status_code = 403
        outer = RuntimeError('group')
        outer.exceptions = [error]
        message = failure_message(outer)
        self.assertIn('403', message)
        self.assertNotIn('private', message)


class FakeCore:
    def __init__(self, store):
        self.store = store

    async def run_turn(self, uid, sid, tid, message, emit):
        emit({'type': 'model_started'})
        await asyncio.sleep(0.15)
        if message == 'fail':
            raise AgentError('模拟失败')
        root = self.store.workspace(uid) / 'resumes' / tid
        root.mkdir(parents=True)
        path = root / 'resume.tex'
        path.write_text('test output')
        history = self.store.history(uid, sid) + [
            {'role': 'user', 'content': message}, {'role': 'assistant', 'content': '已完成'}]
        self.store.complete(uid, sid, tid, history, '已完成', [path])
        return '已完成'


class APITests(unittest.TestCase):
    def test_auth_tasks_download_history_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(directory, FakeCore, allow_legacy_users=True)) as client:
                self.assertEqual(client.get('/health').status_code, 200)
                self.assertEqual(client.post('/sessions').status_code, 401)
                a = client.post('/users').json()
                b = client.post('/users').json()
                ha, hb = ({'Authorization': 'Bearer ' + item['token']} for item in (a, b))
                sid = client.post('/sessions', headers=ha).json()['session_id']
                base = f'/sessions/{sid}'
                self.assertEqual(client.get(base + '/messages', headers=hb).status_code, 404)
                self.assertEqual(client.post(base + '/messages', headers=ha, json={'message': ''}).status_code, 400)
                result = client.post(base + '/messages', headers=ha, json={'message': 'hello'})
                self.assertEqual(result.status_code, 202)
                tid = result.json()['task_id']
                self.assertEqual(client.post(base + '/messages', headers=ha, json={'message': 'again'}).status_code, 409)
                self.assertEqual(client.get(base + '/tasks/' + tid, headers=hb).status_code, 404)
                for _ in range(100):
                    task = client.get(base + '/tasks/' + tid, headers=ha).json()
                    if task['status'] in {'completed', 'failed'}:
                        break
                    time.sleep(0.01)
                self.assertEqual(task['status'], 'completed')
                self.assertGreaterEqual(task['performance']['request_ms'], 0)
                self.assertIn('queue', task['performance']['stage_ms'])
                self.assertGreater(task['performance']['server_total_ms'], 0)
                download = base + '/files/' + task['files'][0]['file_id']
                self.assertEqual(client.get(download, headers=ha).text, 'test output')
                self.assertEqual(client.get(download, headers=hb).status_code, 404)
                tid2 = client.post(base + '/messages', headers=ha, json={'message': 'fail'}).json()['task_id']
                for _ in range(100):
                    failed = client.get(base + '/tasks/' + tid2, headers=ha).json()
                    if failed['status'] == 'failed':
                        break
                    time.sleep(0.01)
                self.assertEqual(failed['error'], '模拟失败')
            with TestClient(create_app(directory, FakeCore, allow_legacy_users=True)) as client:
                messages = client.get(base + '/messages', headers=ha).json()['messages']
                self.assertEqual([m['content'] for m in messages], ['hello', '已完成', 'fail', ''])
            self.assertTrue(messages[-1]['incomplete'])


class Message:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls

    def model_dump(self, **kwargs):
        result = {'role': 'assistant'}
        if self.content is not None:
            result['content'] = self.content
        if self.tool_calls:
            result['tool_calls'] = [{'id': c.id, 'type': 'function', 'function': {
                'name': c.function.name, 'arguments': c.function.arguments}} for c in self.tool_calls]
        return result


class MockClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=self)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            call = SimpleNamespace(id='one', function=SimpleNamespace(
                name='resume__read_file', arguments='{"path":"项目经历库.md"}'))
            message = Message(tool_calls=[call])
        else:
            assert kwargs['messages'][-1]['role'] == 'tool'
            message = Message(content='请补充你的项目经历')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class LocalToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_materials_refresh_after_tool_write(self):
        original = '# 素材库\n\n### 项目一\n第一项完整事实\n\n### 项目二\n与检索词无关的完整事实'
        changed = original + '\n\n### 项目三\n工具刚更新的新事实'

        class FullContextClient(MockClient):
            async def create(client, **kwargs):
                client.calls += 1
                names = {tool['function']['name'] for tool in kwargs['tools']}
                self.assertEqual(names, {'resume__read_selected_template', 'resume__read_file', 'resume__write_file', 'resume__edit_file', 'resume__compile_pdf', 'resume__preview_pdf'})
                context = kwargs['messages'][1]
                material = json.loads(context['content'].split('\n', 1)[1])['markdown']
                self.assertEqual(material, original if client.calls == 1 else changed)
                self.assertEqual(kwargs['reasoning_effort'], 'medium')
                if client.calls == 1:
                    message = Message(tool_calls=[SimpleNamespace(id='update', function=SimpleNamespace(
                        name='resume__write_file', arguments=json.dumps(
                            {'path': '项目经历库.md', 'content': changed}, ensure_ascii=False)))])
                else:
                    message = Message(content='已看到三个项目的完整素材')
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            uid = store.create_user()['user_id']
            sid = store.create_session(uid)['session_id']
            (store.workspace(uid) / '项目经历库.md').write_text(original, encoding='utf-8')
            tid = store.create_task(uid, sid, '补充第三个项目')
            with patch.dict(os.environ, AGENT_API_KEY='test', AGENT_MODEL='test', AGENT_REASONING_EFFORT='medium'):
                reply = await AgentCore(store, client_factory=FullContextClient).run_turn(uid, sid, tid, '补充第三个项目', lambda e: None)
            self.assertEqual(reply, '已看到三个项目的完整素材')
            self.assertFalse(any(m.get('content', '').startswith('最新完整素材库') for m in store.history(uid, sid)))
            output = store.workspace(uid) / 'resumes' / 'file-tools'
            output.mkdir()
            async with LocalTools(store.workspace(uid), output) as tools:
                with self.assertRaises(ValueError):
                    await tools.call('unknown__tool', {})

    async def test_local_tools_with_mock_model(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            uid = store.create_user()['user_id']
            sid = store.create_session(uid)['session_id']
            tid = store.create_task(uid, sid, 'hello')
            events = []
            with patch.dict(os.environ, AGENT_API_KEY='test', AGENT_MODEL='test'):
                core = AgentCore(store, client_factory=MockClient)
                reply = await core.run_turn(uid, sid, tid, 'hello', events.append)
            self.assertEqual(reply, '请补充你的项目经历')
            self.assertEqual(store.task(uid, sid, tid)['status'], 'completed')
            self.assertIn('tool_completed', [event['type'] for event in events])
            self.assertEqual(store.history(uid, sid)[-2]['role'], 'tool')

    async def test_real_tools_read_write_and_compile(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            uid = store.create_user()['user_id']
            root = store.workspace(uid)
            output = root / 'resumes' / 'test'
            output.mkdir(parents=True)
            async with LocalTools(root, output) as tools:
                await tools.call('resume__write_file', {'path': '项目经历库.md', 'content': ''})
                self.assertEqual('', await tools.call('resume__read_file', {'path': '项目经历库.md'}))
                await tools.call('resume__write_file', {'path': '项目经历库.md', 'content': '# 项目\n用户确认的独特项目XYZ'})
                self.assertIn('独特项目XYZ', await tools.call('resume__read_file', {'path': '项目经历库.md'}))
                denied = await tools.call('resume__read_file', {'path': '../../.env'})
                self.assertIn('工具执行出错', denied)
                tex = full_page_tex()
                await tools.call('resume__write_file', {'path': 'resume.tex', 'content': tex})
                compiled = await tools.call('resume__compile_pdf', {'tex_file': 'resume.tex'})
                self.assertIn('编译成功', compiled)
                self.assertTrue((output / 'resume.pdf').exists())
                await tools.call('resume__edit_file', {'path': 'resume.tex', 'base_sha256': source_hash(tex), 'edits_json': json.dumps([{'old':r'\begin{document}', 'new':r'\begin{document}\invalidcommand'}])})
                failed = await tools.call('resume__compile_pdf', {'tex_file': 'resume.tex'})
                self.assertIn('编译失败', failed)
                self.assertFalse((output / 'resume.pdf').exists())
                self.assertFalse(list(output.glob('*.log')))
                two_pages = r'\documentclass{article}\begin{document}One\newpage Two\end{document}'
                await tools.call('resume__write_file', {'path': 'two.tex', 'content': two_pages})
                result = await tools.call('resume__compile_pdf', {'tex_file': 'two.tex'})
                self.assertIn('2 页', result)
                self.assertFalse((output / 'resume.pdf').exists())
                await tools.call('resume__write_file', {'path': '项目经历库.md', 'content': 'Latency reduced to 100 ms.'})
                tex = full_page_tex(first_item='Latency: 100 ms.')
                await tools.call('resume__write_file', {'path': 'quality.tex', 'content': tex})
                result = await tools.call('resume__compile_pdf', {'tex_file': 'quality.tex'})
                self.assertIn('质量检查未通过', result)
                self.assertFalse((output / 'quality.pdf').exists())
                await tools.call('resume__edit_file', {'path': 'quality.tex', 'base_sha256': source_hash(tex), 'edits_json': json.dumps([{'old':'100 ms', 'new':r'\textbf{100 ms}'}])})
                self.assertIn('编译成功', await tools.call('resume__compile_pdf', {'tex_file': 'quality.tex'}))

    async def test_timeout_marks_terminal_failure(self):
        class SlowCore:
            async def run_turn(self, *args):
                await asyncio.sleep(10)
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            uid = store.create_user()['user_id']
            sid = store.create_session(uid)['session_id']
            manager = TaskManager(store, SlowCore(), timeout=0.01)
            tid = manager.submit(uid, sid, 'hello')
            await asyncio.gather(*manager.running)
            self.assertEqual(store.task(uid, sid, tid)['status'], 'failed')
            self.assertIn('超时', store.task(uid, sid, tid)['error'])


if __name__ == '__main__':
    unittest.main()
