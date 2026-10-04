import asyncio
from datetime import datetime, timezone
import json
import tempfile
from types import SimpleNamespace
import unittest

from session_store import SessionStore
from task_manager import TaskManager
from task_metrics import measured, model_request, span, summary, tracing


class MetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_contexts_nested_totals_and_safe_errors(self):
        async def run(label):
            events = []
            def emit(event):
                events.append({**event, "time": datetime.now(timezone.utc).isoformat()})
            with tracing(emit):
                with span("tool", tool=label):
                    await measured("process", asyncio.sleep, .01)
                try:
                    with span("model"):
                        raise RuntimeError("secret provider body")
                except RuntimeError:
                    pass
            task = dict(status="completed", created_at=events[0]["time"],
                        updated_at=events[-1]["time"], events=events)
            p = summary(task)
            self.assertEqual(set(p["stage_ms"]), {"tool", "model"})
            self.assertEqual(p["spans"][0]["tool"], label)
            self.assertEqual(p["spans"][1]["parent_id"], p["spans"][0]["span_id"])
            self.assertEqual(p["spans"][-1]["status"], "error")
            self.assertNotIn("secret", json.dumps(events))
            return events
        a, b = await asyncio.gather(run("a"), run("b"))
        self.assertTrue({e["span_id"] for e in a}.isdisjoint({e["span_id"] for e in b}))

    async def test_queue_and_timeout_are_visible_and_persisted(self):
        class Core:
            async def run_turn(self, uid, sid, tid, message, emit):
                await measured("model", asyncio.sleep, 10)
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            user = store.create_user()["user_id"]
            sid = store.create_session(user)["session_id"]
            manager = TaskManager(store, Core(), concurrency=1, timeout=.02)
            await manager.semaphore.acquire()
            tid = manager.submit(user, sid, "test")
            await asyncio.sleep(.02)
            queued = store.task(user, sid, tid)["performance"]
            self.assertEqual(queued["current"]["stage"], "queue")
            self.assertGreater(queued["current"]["duration_ms"], 0)
            manager.semaphore.release()
            await asyncio.gather(*manager.running)
            result = SessionStore(directory).task(user, sid, tid)
            self.assertEqual(result["status"], "failed")
            p = result["performance"]
            self.assertIsNone(p["current"])
            self.assertEqual(p["spans"][-1]["status"], "cancelled")
            self.assertGreater(p["stage_ms"]["queue"], 0)
            self.assertEqual(store.recent_tasks(user, sid)[0]["performance"], p)
            await manager.close()

    async def test_usage_and_payload_counts_do_not_save_content(self):
        async def create(**kwargs):
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3,
                total_tokens=10), choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        events = []
        with tracing(events.append):
            await model_request(client, messages=[{"content":[{"type":"text", "text":"secret"},
                {"type":"image_url", "image_url":{"url":"private base64"}}]}])
        end = events[-1]
        self.assertEqual((end["text_chars"], end["image_count"], end["total_tokens"]), (6, 1, 10))
        self.assertNotIn("secret", json.dumps(events))
        self.assertNotIn("base64", json.dumps(events))

    def test_legacy_estimates_and_interrupted_spans_stop_at_terminal_time(self):
        task = dict(status="failed", created_at="2026-10-04T00:00:00+00:00",
            updated_at="2026-10-04T00:00:10+00:00", events=[
                {"type":"model_started", "time":"2026-10-04T00:00:01+00:00"}])
        p = summary(task)
        self.assertTrue(p["estimated"])
        self.assertEqual(p["stage_ms"]["model"], 9000)
        task["events"][0].update(type="span_started", stage="model", span_id="s", parent_id=None)
        p = summary(task)
        self.assertFalse(p["estimated"])
        self.assertEqual(p["spans"][0]["status"], "interrupted")
        self.assertEqual(p["stage_ms"]["model"], 9000)
        self.assertIsNone(p["current"])
