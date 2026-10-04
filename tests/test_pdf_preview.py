from resume_fixtures import full_page_tex
import base64
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_core import AgentCore
from tools import LocalTools, PreviewResult
from tex_edits import source_hash
from session_store import SessionStore
from task_manager import TaskManager
from test_backend import Message, MockClient


def actions(*items):
    return Message(tool_calls=[SimpleNamespace(id=f'call-{i}', function=SimpleNamespace(
        name='resume__' + name, arguments=json.dumps(args))) for i, (name, args) in enumerate(items)])


def response(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def content(number=1):
    return full_page_tex('Revision ' + str(number))


def generate(number=1):
    change = (('write_file', {'path': 'resume.tex', 'content': content(number)}) if number == 1 else
              ('edit_file', {'path': 'resume.tex', 'base_sha256':source_hash(content(number-1)),
                             'edits_json':json.dumps([{'old':f'Revision {number-1}', 'new':f'Revision {number}'}])}))
    return actions(change,
                   ('compile_pdf', {'tex_file': 'resume.tex'}))


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_draft_images_and_stale_preview_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            workspace = store.workspace(store.create_user()['user_id'])
            output = workspace / 'resumes' / 'test'; output.mkdir(parents=True)
            async with LocalTools(workspace, output) as tools:
                await tools.call('resume__write_file', {'path': 'resume.tex', 'content': content().replace('Revision 1', r'One\newpage Two')})
                result = await tools.call('resume__compile_pdf', {'tex_file': 'resume.tex'})
                self.assertIn('2 页', result)
                self.assertFalse((output / 'resume.pdf').exists())
                preview = await tools.call('resume__preview_pdf', {'tex_file': 'resume.tex'})
                self.assertIsInstance(preview, PreviewResult)
                self.assertEqual(len(preview.images), 2)
                data = base64.b64decode(preview.images[0]['image_url']['url'].split(',', 1)[1])
                self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
                self.assertTrue(preview.current(workspace, output))
                await tools.call('resume__edit_file', {'path': 'resume.tex',
                    'base_sha256':source_hash(content().replace('Revision 1', r'One\newpage Two')),
                    'edits_json':json.dumps([{'old':r'One\newpage Two','new':'Revision 2'}])})
                self.assertFalse(preview.current(workspace, output))
                self.assertIsInstance(await tools.call('resume__preview_pdf', {'tex_file': 'resume.tex'}), str)
                await tools.call('resume__compile_pdf', {'tex_file': 'resume.tex'})
                preview = await tools.call('resume__preview_pdf', {'tex_file': 'resume.tex'})
                self.assertTrue(preview.current(workspace, output, output / 'resume.pdf'))
                (workspace / '项目经历库.md').write_text('updated material')
                self.assertFalse(preview.current(workspace, output, output / 'resume.pdf'))
                self.assertIsInstance(await tools.call('resume__preview_pdf', {'tex_file': 'resume.tex'}), str)
                self.assertIsInstance(await tools.call('resume__preview_pdf', {'tex_file': '../../other.tex'}), str)
            self.assertEqual({p.name for p in output.iterdir()}, {'resume.tex', 'resume.pdf'})

    async def run_model(self, client, tools_factory=LocalTools):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        store = SessionStore(directory.name)
        uid = store.create_user()['user_id']; sid = store.create_session(uid)['session_id']
        core = AgentCore(store, tools_factory=tools_factory, client_factory=client)
        manager = TaskManager(store, core)
        with patch.dict(os.environ, AGENT_API_KEY='test', AGENT_MODEL='test'):
            tid = manager.submit(uid, sid, 'generate resume and inspect preview')
            import asyncio
            await asyncio.gather(*manager.running)
        return store, uid, sid, tid

    async def test_real_images_reach_model_iteration_bounded_and_not_persisted(self):
        test = self
        class VisualClient(MockClient):
            async def create(client, **kwargs):
                client.calls += 1
                messages = kwargs['messages']
                if client.calls == 1:
                    return response(generate())
                preview = messages[-1]
                test.assertEqual(preview['role'], 'user')
                images = [p for p in preview['content'] if p['type'] == 'image_url']
                test.assertEqual(len(images), 1)
                test.assertTrue(images[0]['image_url']['url'].startswith('data:image/png;base64,'))
                # Both tool responses precede the synthetic image message, preserving protocol order.
                test.assertEqual(messages[-2]['role'], 'tool')
                if client.calls <= 7:
                    return response(generate(client.calls))
                test.assertIn('6 轮截图检查上限', messages[-2]['content'])
                return response(Message(content='Reviewed latest image; retained revision 6.'))
        store, uid, sid, tid = await self.run_model(VisualClient)
        task = store.task(uid, sid, tid)
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertEqual(sum(e['type'] == 'preview_started' for e in task['events']), 6)
        output = store.workspace(uid) / 'resumes' / tid
        self.assertIn('Revision 6', (output / 'resume.tex').read_text())
        self.assertNotIn('data:image', json.dumps(store.history(uid, sid)))
        self.assertEqual(len(task['files']), 2)

    async def test_rejected_image_request_does_not_publish_pdf(self):
        class RejectingClient(MockClient):
            async def create(client, **kwargs):
                client.calls += 1
                if client.calls == 1:
                    return response(generate())
                error = RuntimeError('sensitive provider response')
                error.status_code = 400
                raise error
        store, uid, sid, tid = await self.run_model(RejectingClient)
        task = store.task(uid, sid, tid)
        self.assertEqual(task['status'], 'failed')
        self.assertIn('含截图', task['error'])
        self.assertNotIn('sensitive', task['error'])
        self.assertEqual(task['files'], [])

    async def test_missing_preview_cannot_publish_unreviewed_pdf(self):
        class MissingPreview(LocalTools):
            async def call(self, name, arguments):
                if name == 'resume__preview_pdf':
                    return 'screenshot unavailable'
                return await super().call(name, arguments)
        class FinalClient(MockClient):
            async def create(client, **kwargs):
                client.calls += 1
                return response(generate() if client.calls == 1 else Message(content='Done'))
        store, uid, sid, tid = await self.run_model(FinalClient, MissingPreview)
        task = store.task(uid, sid, tid)
        self.assertEqual(task['status'], 'failed')
        self.assertIn('截图检查', task['error'])
        self.assertEqual(task['files'], [])

    async def test_sparse_draft_is_reviewed_and_repaired_before_delivery(self):
        test = self

        class RepairClient(MockClient):
            async def create(client, **kwargs):
                client.calls += 1
                if client.calls == 1:
                    return response(actions(
                        ('write_file', {'path': 'resume.tex', 'content':
                            r'\documentclass{article}\pagestyle{empty}\begin{document}Sparse draft\end{document}'}),
                        ('compile_pdf', {'tex_file': 'resume.tex'})))
                messages = kwargs['messages']
                result = messages[-2]['content']
                metadata = json.loads(result.split('\n')[-1])
                layout = metadata['layout_checks'][0]
                test.assertEqual(layout['max_bottom_blank_percent'], 4.0)
                test.assertTrue(any(part['type'] == 'image_url' for part in messages[-1]['content']))
                if client.calls == 2:
                    test.assertIn('质量检查未通过', result)
                    test.assertFalse(layout['within_whitespace_limit'])
                    sparse = r'\documentclass{article}\pagestyle{empty}\begin{document}Sparse draft\end{document}'
                    preamble, body = content().split(r'\begin{document}')
                    return response(actions(('edit_file', {'path':'resume.tex', 'base_sha256':source_hash(sparse),
                        'edits_json':json.dumps([
                            {'old':r'\documentclass{article}\pagestyle{empty}', 'new':preamble},
                            {'old':'Sparse draft', 'new':body.removesuffix(r'\end{document}')},
                        ])}), ('compile_pdf', {'tex_file':'resume.tex'})))
                test.assertIn('编译成功', result)
                test.assertTrue(layout['within_whitespace_limit'])
                return response(Message(content='Reviewed the repaired full page.'))

        store, uid, sid, tid = await self.run_model(RepairClient)
        task = store.task(uid, sid, tid)
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertEqual(len(task['files']), 2)

    async def test_final_reply_cannot_turn_sparse_pdf_into_success(self):
        class SparseClient(MockClient):
            async def create(client, **kwargs):
                client.calls += 1
                if client.calls == 1:
                    return response(actions(
                        ('write_file', {'path': 'resume.tex', 'content':
                            r'\documentclass{article}\pagestyle{empty}\begin{document}Sparse draft\end{document}'}),
                        ('compile_pdf', {'tex_file': 'resume.tex'})))
                return response(Message(content='Looks great, completed!'))

        store, uid, sid, tid = await self.run_model(SparseClient)
        task = store.task(uid, sid, tid)
        self.assertEqual(task['status'], 'failed')
        self.assertIn('没有可交付 PDF', task['error'])
        self.assertIn('页底留白', task['error'])
        self.assertEqual(task['files'], [])
        output = store.workspace(uid) / 'resumes' / tid
        self.assertFalse((output / 'resume.pdf').exists())


if __name__ == '__main__':
    unittest.main()
