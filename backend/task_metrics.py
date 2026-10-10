"""Task-local timing. Persist only metadata, never prompts, responses or credentials."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4
from functools import wraps

_sink = ContextVar("task_metrics_sink", default=None)
_parent = ContextVar("task_metrics_parent", default=None)


@contextmanager
def tracing(emit):
    token = _sink.set(emit)
    parent = _parent.set(None)
    try:
        yield
    finally:
        _parent.reset(parent)
        _sink.reset(token)


@contextmanager
def span(stage, **metadata):
    emit = _sink.get()
    if emit is None:
        yield metadata
        return
    ident, parent, start = uuid4().hex, _parent.get(), perf_counter()
    emit({"type": "span_started", "span_id": ident, "parent_id": parent,
          "stage": stage, **metadata})
    token = _parent.set(ident)
    status = "ok"
    try:
        yield metadata
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        metadata["error_type"] = type(exc).__name__
        if isinstance(getattr(exc, "status_code", None), int):
            metadata["http_status"] = exc.status_code
        raise
    finally:
        _parent.reset(token)
        emit({"type": "span_finished", "span_id": ident, "parent_id": parent,
              "stage": stage, **metadata, "status": status,
              "duration_ms": round((perf_counter() - start) * 1000, 2)})


async def measured(stage, function, *args, metadata=None, **kwargs):
    with span(stage, **(metadata or {})) as metrics:
        result = await function(*args, **kwargs)
        if metrics.get("tool") == "resume__compile_pdf":
            metrics["accepted"] = isinstance(result, str) and result.startswith("编译成功：")
        return result


def timed(stage):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with span(stage):
                return function(*args, **kwargs)
        return call
    return decorate


async def model_request(client, on_delta=None, **kwargs):
    messages = kwargs.get("messages", [])
    chars = images = 0
    for message in messages:
        content = message.get("content") or ""
        if isinstance(content, str):
            chars += len(content)
        else:
            for part in content:
                chars += len(part.get("text", ""))
                images += part.get("type") == "image_url"
    with span("model", message_count=len(messages), text_chars=chars, image_count=images) as metrics:
        if on_delta is None:
            response = await client.chat.completions.create(**kwargs)
        else:
            from backend.model_stream import streamed_completion
            response = await streamed_completion(client, on_delta, **kwargs)
        message = response.choices[0].message
        metrics["next_tools"] = [call.function.name for call in (getattr(message, "tool_calls", None) or [])]
        usage = getattr(response, "usage", None)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None)
            if isinstance(value, int):
                metrics[key] = value
        details = getattr(usage, "completion_tokens_details", None)
        value = getattr(details, "reasoning_tokens", None)
        if isinstance(value, int):
            metrics["reasoning_tokens"] = value
        return response


def summary(task, current_time=None):
    """Nested spans remain a timeline; only root spans contribute to totals."""
    parse = datetime.fromisoformat
    active = task["status"] in {"queued", "running"}
    end = (current_time or datetime.now(timezone.utc)) if active else parse(task["updated_at"])
    elapsed = lambda start, finish: max(0, round((finish - parse(start)).total_seconds() * 1000, 2))
    events = task["events"]
    spans = {}
    for event in events:
        if event["type"] == "span_started":
            spans[event["span_id"]] = {**event, "started_at": event["time"], "status": "running"}
        elif event["type"] == "span_finished" and event["span_id"] in spans:
            spans[event["span_id"]].update(event)
    legacy = not spans
    if legacy:
        # Historical starts only: these include intervening bookkeeping/network/retries.
        boundaries = {"tool_started", "materials_saving", "completed", "failed", "model_started"}
        for index, event in enumerate(events):
            if event["type"] == "model_started":
                following = next((e for e in events[index + 1:] if e["type"] in boundaries), None)
                finish = parse(following["time"]) if following else end
                spans[str(index)] = {"stage": "model", "started_at": event["time"],
                    "status": "estimated", "duration_ms": elapsed(event["time"], finish), "parent_id": None}
    totals, current = {}, None
    for item in spans.values():
        if item["status"] == "running":
            item["duration_ms"] = elapsed(item["started_at"], end)
            if active:
                current = item
            else:
                item["status"] = "interrupted"  # e.g. process killed before closing span
        if not item.get("parent_id"):
            stage = item["stage"]
            totals[stage] = round(totals.get(stage, 0) + item["duration_ms"], 2)
    ingestion = next((e["duration_ms"] for e in events if e["type"] == "request_received"), None)
    task_ms = elapsed(task["created_at"], end)
    return {"task_ms": task_ms, "request_ms": ingestion,
            "server_total_ms": round(task_ms + (ingestion or 0), 2),
            "estimated": legacy, "stage_ms": totals, "current": current,
            "model_calls": sum(s["stage"] == "model" for s in spans.values()),
            "layout_checks": [e for e in events if e["type"] == "layout_checked"],
            "spans": list(spans.values())}


if __name__ == "__main__":
    import argparse
    import json
    import sqlite3
    from dotenv import dotenv_values

    parser = argparse.ArgumentParser(description="只读查看最近任务耗时；历史任务模型耗时为估算")
    parser.add_argument("--data-dir")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    from backend.paths import PROJECT_ROOT
    root = PROJECT_ROOT
    configured = args.data_dir or dotenv_values(root / ".env").get("RESUME_DATA_DIR") or "data"
    database = (root / configured / "resume.sqlite3").resolve()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        print("任务ID    状态        总耗时(s) 模型(s) 次数 统计方式")
        for row in db.execute("SELECT id,status,created_at,updated_at,events FROM tasks ORDER BY created_at DESC LIMIT ?",
                              (max(1, min(args.limit, 1000)),)):
            task = dict(row)
            task["events"] = json.loads(task["events"])
            p = summary(task)
            print(f"{task['id'][:8]} {task['status']:10} {p['server_total_ms']/1000:9.2f} "
                  f"{p['stage_ms'].get('model', 0)/1000:8.2f} {p['model_calls']:4} "
                  + ("历史估算" if p["estimated"] else "实测"))
