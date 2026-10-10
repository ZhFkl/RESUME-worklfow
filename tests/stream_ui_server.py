"""Temporary streaming browser fixture, isolated from local accounts/materials."""
import os
import asyncio
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
from test_streaming import StreamingCore
class ProgressCore(StreamingCore):
    async def run_turn(self, uid, sid, tid, message, emit):
        if message.startswith('progress'):
            for event in [
                {'type':'model_started'},
                {'type':'tool_started','tool':'resume__read_file'},
                {'type':'tool_started','tool':'resume__compile_pdf'},
                {'type':'visual_review_started'},
            ]:
                emit(event)
                await asyncio.sleep(1.4)
        if message == 'timeline':
            emit({'type':'model_started'})
            emit({'type':'assistant_delta','text':'先保留这一段说明。'})
            await asyncio.sleep(1.3)
            emit({'type':'tool_started','tool':'resume__read_selected_template'})
            await asyncio.sleep(1.3)
            emit({'type':'tool_completed','tool':'resume__read_selected_template'})
            emit({'type':'model_started'})
            for part in ['后续', '正文', '正在追加。']:
                emit({'type':'assistant_delta','text':part})
                await asyncio.sleep(1.3)
            reply='后续正文正在追加。'
            self.store.complete(uid,sid,tid,self.store.history(uid,sid)+[{'role':'user','content':message},{'role':'assistant','content':'先保留这一段说明。'},{'role':'assistant','content':reply}],reply,[])
            return
        if message == 'markdown':
            reply = '**整理完成**\n\n- 第一项成果\n- 第二项成果\n\n<script>window.unsafeReply=1</script><img src=x onerror="window.unsafeReply=1">'
            emit({'type':'model_started'})
            for piece in reply.split('\n'):
                emit({'type':'assistant_delta','text':piece+'\n'})
                await asyncio.sleep(.08)
            self.store.complete(uid,sid,tid,self.store.history(uid,sid)+[{'role':'user','content':message},{'role':'assistant','content':reply}],reply,[])
            return
        await super().run_turn(uid,sid,tid,message,emit)

scratch = tempfile.TemporaryDirectory(prefix='resume-stream-ui-')
store = SessionStore(scratch.name)
AccountStore(store).create('stream_test', 'Stream-test-2026!')
uvicorn.run(create_app(scratch.name, core_factory=ProgressCore), host='127.0.0.1', port=18802, log_level='warning')
