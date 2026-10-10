"""直接调用工具后的任务隔离、参数边界与子进程生命周期回归。"""
from resume_fixtures import full_page_tex

import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend.process_runner import run_process
from backend.session_store import SessionStore
from backend.tools import LocalTools, PreviewResult


class LocalToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_users_have_independent_files_and_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            roots = [store.workspace(store.create_user()['user_id']) for _ in range(2)]
            preview_dirs = []

            async def generate(root, label):
                output = root / 'resumes' / 'test'
                output.mkdir(parents=True)
                async with LocalTools(root, output) as tools:
                    preview_dirs.append(tools.preview_dir)
                    await tools.call('resume__write_file', {'path': '项目经历库.md', 'content': label})
                    await tools.call('resume__write_file', {'path': 'resume.tex', 'content':
                        full_page_tex(label)})
                    self.assertIn('编译成功', await tools.call('resume__compile_pdf', {'tex_file': 'resume.tex'}))
                    preview = await tools.call('resume__preview_pdf', {'tex_file': 'resume.tex'})
                    self.assertIsInstance(preview, PreviewResult)
                    self.assertTrue(preview.current(root, output, output / 'resume.pdf'))
                    self.assertEqual(await tools.call('resume__read_file', {'path': '项目经历库.md'}), label)
                    with self.assertRaises(ValueError):
                        await tools.call('resume__write_file', {'path': 'resume.tex', 'content': label, 'workspace': str(roots[0])})
                    with self.assertRaises(ValueError):
                        await tools.call('resume__write_file', {'path': 'resume.tex', 'content': 42})
                    self.assertIn('工具执行出错', await tools.call('resume__read_file', {'path': str(roots[0] / '项目经历库.md')}))
                    self.assertEqual({p.suffix for p in output.iterdir()}, {'.tex', '.pdf'})
            await asyncio.gather(generate(roots[0], 'Alice'), generate(roots[1], 'Bob'))
            self.assertNotEqual(*preview_dirs)
            self.assertTrue(all(not path.exists() for path in preview_dirs))

    async def test_process_timeout_and_cancellation_reap_children(self):
        create = asyncio.create_subprocess_exec
        for cancel in (False, True):
            started = asyncio.Event()
            processes = []

            async def capture(*args, **kwargs):
                process = await create(*args, **kwargs)
                processes.append(process)
                started.set()
                return process

            with patch('backend.process_runner.asyncio.create_subprocess_exec', side_effect=capture):
                task = asyncio.create_task(run_process(
                    [sys.executable, '-c', 'import time; time.sleep(30)'], timeout=30 if cancel else 0.05))
                await started.wait()
                if cancel:
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
                    await task
                self.assertIsNotNone(processes[0].returncode)

    async def test_command_does_not_inherit_model_secrets(self):
        with patch.dict(os.environ, AGENT_API_KEY='test-secret'):
            result = await run_process([sys.executable, '-c',
                'import os; print("AGENT_API_KEY" in os.environ)'], timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'False')

    async def test_cancelled_compile_cleans_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            root = store.workspace(store.create_user()['user_id'])
            output = root / 'resumes' / 'test'
            output.mkdir(parents=True)
            started = asyncio.Event()

            async def slow_compile(*args, **kwargs):
                (output / 'resume.pdf').write_bytes(b'partial')
                (output / 'resume.log').write_text('partial')
                started.set()
                await asyncio.sleep(30)

            async with LocalTools(root, output) as tools:
                await tools.call('resume__write_file', {'path': 'resume.tex', 'content': 'test'})
                with patch('backend.tools.run_process', side_effect=slow_compile):
                    task = asyncio.create_task(tools.call('resume__compile_pdf', {'tex_file': 'resume.tex'}))
                    await started.wait()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertEqual([p.name for p in output.iterdir()], ['resume.tex'])
