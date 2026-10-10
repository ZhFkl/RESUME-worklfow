"""Disposable, real-compiler browser fixture; contains only synthetic resume text."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / 'tests')]
os.environ['RESUME_PUBLIC_ORIGIN'] = ''
os.environ['RESUME_COOKIE_SECURE'] = 'false'

import uvicorn
from backend.api import create_app
from backend.account_store import AccountStore
from backend.session_store import SessionStore
from backend.tools import LocalTools
from resume_fixtures import full_page_tex
from test_backend import FakeCore

scratch = tempfile.TemporaryDirectory(prefix='resume-editor-ui-')
store = SessionStore(scratch.name)
uid = AccountStore(store).create('editor_test', 'Editor-test-2026!')
sid = store.create_session(uid)['session_id']
tid = store.create_task(uid, sid, 'Synthetic editor fixture')
output = store.workspace(uid) / 'resumes' / tid
output.mkdir(parents=True)
(output / 'example.tex').write_text(full_page_tex('Editor fixture'), encoding='utf-8')


async def seed():
    async with LocalTools(store.workspace(uid), output) as tools:
        result = await tools.compile_pdf('example.tex')
        assert (output / 'example.pdf').is_file(), result
    store.complete(uid, sid, tid, [], 'Fixture ready', [output / 'example.tex', output / 'example.pdf'])


asyncio.run(seed())
uvicorn.run(create_app(scratch.name, core_factory=FakeCore), host='127.0.0.1', port=18801, log_level='warning')
