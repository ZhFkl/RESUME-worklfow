import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace

from openai.types.chat import ChatCompletionChunk
from starlette.testclient import TestClient
from backend.api import create_app
from backend.model_stream import streamed_completion
from backend.task_metrics import model_request, tracing


class FakeStream:
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False
    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield ChatCompletionChunk(id='test', object='chat.completion.chunk', created=0,
                                      model='test', choices=[chunk])
    async def close(self):
        self.closed = True


def choice(delta, finish=None):
    return {'index': 0, 'delta': delta, 'finish_reason': finish}


class ModelStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_tool_fragments_and_metrics(self):
        stream = FakeStream([
            choice({'content': '你😀', 'reasoning_content': 'private thought'}),
            choice({'content': '好', 'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function',
                    'function': {'name': 'resume__read_file', 'arguments': '{"pa'}}]}),
            choice({'tool_calls': [{'index': 1, 'id': 'call_2', 'type': 'function',
                    'function': {'name': 'second', 'arguments': '{}'}},
                    {'index': 0, 'function': {'arguments': 'th":"secret"}'}}]}, 'tool_calls')])
        async def create(**kwargs):
            self.assertTrue(kwargs['stream'])
            return stream
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        deltas, metrics = [], []
        with tracing(metrics.append):
            result = await model_request(client, on_delta=deltas.append, messages=[])
        self.assertEqual(deltas, ['你😀', '好'])
        msg = result.choices[0].message
        self.assertEqual(msg.content, '你😀好')
        self.assertEqual(msg.tool_calls[0].function.arguments, '{"path":"secret"}')
        self.assertEqual(msg.tool_calls[1].id, 'call_2')
        self.assertTrue(stream.closed)
        self.assertNotIn('secret', json.dumps(metrics))
        self.assertNotIn('private thought', json.dumps(deltas))

    async def test_incomplete_and_truncated_streams_fail_and_close(self):
        for finish in (None, 'length', 'content_filter'):
            stream = FakeStream([choice({'content': 'partial'}, finish)])
            async def create(**kwargs): return stream
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with self.assertRaises(RuntimeError):
                await streamed_completion(client, lambda _: None)
            self.assertTrue(stream.closed)


class StreamingCore:
    def __init__(self, store): self.store = store
    async def run_turn(self, uid, sid, tid, message, emit):
        emit({'type': 'model_started'})
        reply = '你好，这是一条逐步输出的回复😀。正在整理你的经历，请稍等。'
        for letter in reply:
            emit({'type': 'assistant_delta', 'text': letter})
            await asyncio.sleep(.07)
        if message == 'fail':
            raise RuntimeError('private provider error')
        await asyncio.sleep(.6)
        history = self.store.history(uid, sid) + [
            {'role': 'user', 'content': message}, {'role': 'assistant', 'content': reply}]
        self.store.complete(uid, sid, tid, history, reply, [])


class StreamAPITests(unittest.TestCase):
    def test_sse_private_replay_and_terminal_failure(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(directory, StreamingCore, allow_legacy_users=True)) as client:
            a, b = client.post('/users').json(), client.post('/users').json()
            ha, hb = ({'Authorization': 'Bearer ' + u['token']} for u in (a, b))
            sid = client.post('/sessions', headers=ha).json()['session_id']
            base = f'/sessions/{sid}'
            tid = client.post(base+'/messages', headers=ha, json={'message':'hello'}).json()['task_id']
            path = base+'/tasks/'+tid
            self.assertEqual(client.get(path+'/stream').status_code, 401)
            self.assertEqual(client.get(path+'/stream', headers=hb).status_code, 404)
            response = client.get(path+'/stream', headers=ha)
            self.assertEqual(response.headers['x-accel-buffering'], 'no')
            self.assertIn('no-store', response.headers['cache-control'])
            frames = [frame.split('\ndata: ', 1) for frame in response.text.strip().split('\n\n')]
            parts = [json.loads(data) for event, data in frames if event == 'event: text']
            self.assertGreater(len(parts), 2)
            self.assertEqual(''.join(p['text'] for p in parts), client.get(path, headers=ha).json()['reply'])
            replay = client.get(path+'/stream', headers=ha)
            self.assertIn('completed', replay.text)
            self.assertNotIn('assistant_delta', json.dumps(client.get(path, headers=ha).json()['events']))
            tid = client.post(base+'/messages', headers=ha, json={'message':'fail'}).json()['task_id']
            failed = client.get(base+'/tasks/'+tid+'/stream', headers=ha)
            self.assertIn('"status": "failed"', failed.text)
            self.assertNotIn('private provider error', failed.text)
            saved=client.get(base+'/messages', headers=ha).json()['messages']
            self.assertEqual(len(saved), 4)
            self.assertTrue(saved[-1]['incomplete'])
            self.assertIn('逐步输出',str(saved[-1]['timeline']))

class TimelinePersistenceTests(unittest.TestCase):
    def test_rounds_failure_restart_and_history_do_not_overwrite(self):
        from backend.session_store import SessionStore
        from backend.task_manager import TaskManager
        from backend.reply_timeline import display_history
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id']
            manager=TaskManager(store,None)
            tid=store.create_task(uid,sid,'first')
            manager.emit(tid,{'type':'model_started'})
            manager.emit(tid,{'type':'assistant_delta','text':'先整理经历。'})
            manager.emit(tid,{'type':'tool_started','tool':'resume__read_file','arguments':'private'})
            manager.emit(tid,{'type':'tool_completed','tool':'resume__read_file'})
            manager.emit(tid,{'type':'model_started'})
            manager.emit(tid,{'type':'assistant_delta','text':'整理完成。'})
            history=[{'role':'user','content':'first'},{'role':'assistant','content':'先整理经历。'},{'role':'assistant','content':'整理完成。'}]
            store.complete(uid,sid,tid,history,'整理完成。',[]);manager.emit(tid,{'type':'completed'})
            tid2=store.create_task(uid,sid,'second');manager.emit(tid2,{'type':'model_started'})
            manager.emit(tid2,{'type':'assistant_delta','text':'未完成的回复。'})
            store.status(tid2,'failed','失败');manager.emit(tid2,{'type':'failed'})
            restored=SessionStore(directory)
            messages=display_history(restored,uid,sid)
            self.assertEqual([m['content'] for m in messages if m['role']=='user'],['first','second'])
            self.assertEqual([i['text'] for i in messages[1]['timeline'] if i['type']=='text'],['先整理经历。','整理完成。'])
            self.assertTrue(messages[3]['incomplete'])
            self.assertNotIn('private',json.dumps(messages))
            tid3=store.create_task(uid,sid,'third');manager.emit(tid3,{'type':'model_started'})
            manager.emit(tid3,{'type':'assistant_delta','text':'新回复'})
            store.complete(uid,sid,tid3,history+[{'role':'user','content':'third'},{'role':'assistant','content':'新回复'}],'新回复',[]);manager.emit(tid3,{'type':'completed'})
            messages=display_history(restored,uid,sid)
            self.assertEqual([m['content'] for m in messages if m['role']=='user'],['first','second','third'])
            self.assertEqual(restored.history(uid,sid)[0]['content'],'first')
