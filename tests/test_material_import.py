import asyncio
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient
from api import create_app
from agent_core import AgentError
from material_importer import MaterialImporter, merge_additions, normalize_documents, validate_result
from session_store import Busy, SessionStore
from task_manager import TaskManager


class ExtractClient:
    response = {"additions": [
        {"section": "personal", "text": "- 姓名：测试甲"},
        {"section": "projects", "text": "### 素材管理项目\n- 使用 Python 整理文档。"}]}
    calls = 0

    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def create(self, **kwargs):
        type(self).calls += 1
        await asyncio.sleep(.03)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(self.response, ensure_ascii=False)))])


class NoChat:
    def __init__(self, store):
        pass

    async def run_turn(self, *args):
        raise AgentError("用于验证失败恢复")


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_preserves_existing_deduplicates_and_isolates(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, AGENT_API_KEY="test", AGENT_MODEL="test"):
            store = SessionStore(directory)
            a, b = store.create_user(), store.create_user()
            uid = a['user_id']
            sid = store.create_session(uid)['session_id']
            original = store.materials(uid)
            store.save_materials(uid, original['markdown'] + '\n用户手写的独特事实XYZ\n', original['revision'])
            docs = normalize_documents([{'name': '简历.pdf', 'text': '测试甲，素材管理项目，使用 Python。'}])
            manager = TaskManager(store, NoChat(store), importer=MaterialImporter(store, ExtractClient))
            tid = manager.submit_import(uid, sid, docs)
            with self.assertRaises(Busy):
                current = store.materials(uid)
                store.save_materials(uid, '覆盖', current['revision'])
            await asyncio.gather(*manager.running)
            self.assertEqual(store.task(uid, sid, tid)['status'], 'completed')
            actual = store.materials(uid)['markdown']
            self.assertIn('用户手写的独特事实XYZ', actual)
            self.assertIn('### 素材管理项目', actual)
            self.assertNotIn('测试甲', store.materials(b['user_id'])['markdown'])
            calls = ExtractClient.calls
            manager.submit_import(uid, sid, docs)
            await asyncio.gather(*manager.running)
            self.assertEqual(store.materials(uid)['markdown'], actual)
            self.assertEqual(ExtractClient.calls, calls)
            self.assertTrue((store.workspace(uid) / 'snapshots' / (tid + '-source.json')).exists())
            with self.assertRaises(Busy):
                store.save_materials(uid, '旧草稿', original['revision'])
            await manager.close()

    async def test_invalid_model_output_never_writes(self):
        class BadClient(ExtractClient):
            response = {'additions': [{'section': 'projects', 'text': '# 覆盖文档'}]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, AGENT_API_KEY='test', AGENT_MODEL='test'):
            store = SessionStore(directory)
            uid = store.create_user()['user_id']
            sid = store.create_session(uid)['session_id']
            before = store.materials(uid)
            manager = TaskManager(store, NoChat(store), importer=MaterialImporter(store, BadClient))
            tid = manager.submit_import(uid, sid, normalize_documents([{'name': '简历.docx', 'text': '测试资料'}]))
            await asyncio.gather(*manager.running)
            self.assertEqual(store.task(uid, sid, tid)['status'], 'failed')
            self.assertEqual(before, store.materials(uid))
            self.assertEqual(store.history(uid, sid), [])
            await manager.close()

    def test_validation_and_non_destructive_merge(self):
        with self.assertRaises(ValueError):
            normalize_documents([{'name': '../file.pdf', 'text': ''}])
        with self.assertRaises(AgentError):
            validate_result('{"additions":null}')
        text = '# 经历\n\n## 项目经历\n旧项目内容\n\n## 技能\n原技能\n'
        merged = merge_additions(text, [{'section': 'projects', 'text': '### 新项目\n- 新事实'}])
        self.assertIn('旧项目内容', merged)
        self.assertIn('## 技能\n原技能', merged)
        self.assertLess(merged.index('新项目'), merged.index('## 技能'))


class ImportAPITests(unittest.TestCase):
    def test_materials_import_auth_restart_retry_static_and_versions(self):
        def factory(store):
            return MaterialImporter(store, ExtractClient)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, AGENT_API_KEY='test', AGENT_MODEL='test'):
            with TestClient(create_app(directory, NoChat, factory, allow_legacy_users=True)) as client:
                a, b = client.post('/users').json(), client.post('/users').json()
                headers = {'Authorization': 'Bearer ' + a['token']}
                other = {'Authorization': 'Bearer ' + b['token']}
                sid = client.post('/sessions', headers=headers).json()['session_id']
                base = '/sessions/' + sid
                self.assertEqual(client.get('/materials').status_code, 401)
                self.assertEqual(client.get('/').status_code, 200)
                self.assertEqual(client.get('/.env').status_code, 404)
                self.assertEqual(client.get('/session_store.py').status_code, 404)
                self.assertEqual(client.get('/versions', headers=headers).json()['files'], [])
                before = client.get('/materials', headers=headers).json()
                updated = client.put('/materials', headers=headers, json={**before, 'markdown': before['markdown'] + '\n真实用户编辑\n'})
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(client.put('/materials', headers=headers, json=before).status_code, 409)
                docs = {'documents': [{'name': 'old.pdf', 'text': '测试甲，使用 Python 整理文档'}]}
                self.assertEqual(client.post(base + '/imports', headers=other, json=docs).status_code, 404)
                result = client.post(base + '/imports', headers=headers, json=docs)
                self.assertEqual(result.status_code, 202)
                tid = result.json()['task_id']
                for _ in range(100):
                    result = client.get(base + '/tasks/' + tid, headers=headers).json()
                    if result['status'] in {'completed', 'failed'}:
                        break
                    time.sleep(.02)
                self.assertEqual(result['status'], 'completed')
                self.assertNotIn('payload', result)
                self.assertIn('测试甲', client.get('/materials', headers=headers).json()['markdown'])
                self.assertEqual(client.get('/tasks/active', headers=other).json()['task'], None)
                failed = client.post(base + '/messages', headers=headers, json={'message':'hello'}).json()['task_id']
                for _ in range(100):
                    if client.get(base + '/tasks/' + failed, headers=headers).json()['status'] == 'failed':
                        break
                    time.sleep(.01)
                retry = client.post(base + '/tasks/' + failed + '/retry', headers=headers)
                self.assertEqual(retry.status_code, 202)
            with TestClient(create_app(directory, NoChat, factory, allow_legacy_users=True)) as client:
                self.assertIn('测试甲', client.get('/materials', headers=headers).json()['markdown'])
                self.assertIn('真实用户编辑', client.get('/materials', headers=headers).json()['markdown'])
                self.assertEqual(len(client.get(base + '/messages', headers=headers).json()['messages']), 2)


if __name__ == '__main__':
    unittest.main()
