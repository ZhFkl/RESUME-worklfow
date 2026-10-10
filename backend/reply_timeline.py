"""Persist public reply text and operation markers, separate from model context/metrics."""
import json

PUBLIC_EVENTS = {'model_started', 'tool_started', 'tool_completed', 'template_selected',
    'preview_started', 'visual_review_started', 'attachment_processing', 'import_started',
    'materials_saving', 'materials_saved', 'layout_checked'}


def record(store, task_id, event):
    kind = event['type']
    if kind not in PUBLIC_EVENTS | {'assistant_delta', 'completed', 'failed'}:
        return None
    with store.db() as db:
        task = db.execute('SELECT session_id,kind,status,reply FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task or task['kind'] not in {'chat', 'import'}:
            return None
        row = db.execute('SELECT * FROM reply_timelines WHERE task_id=?', (task_id,)).fetchone()
        if row is None and kind in {'completed','failed'}:
            return None
        history = (json.loads(db.execute('SELECT history FROM sessions WHERE id=?', (task['session_id'],)).fetchone()[0])
                   if row is None or kind in {'completed','failed'} else [])
        start = row['history_start'] if row else len(history)
        items = json.loads(row['items']) if row else []
        end = row['history_end'] if row else start
        if kind == 'assistant_delta':
            text = event.get('text', '')
            if items and items[-1]['type'] == 'text':
                items[-1]['text'] += text
            elif text:
                items.append({'type': 'text', 'text': text})
        elif kind in {'completed', 'failed'}:
            end = len(history)
            if kind == 'completed' and task['reply'] and not any(i['type']=='text' and i['text'].strip()==task['reply'].strip() for i in items):
                items.append({'type':'text', 'text':task['reply']})
        else:
            # Never persist tool arguments, outputs, model reasoning, or provider errors.
            items.append({k: event[k] for k in ('type', 'tool') if k in event})
        db.execute('INSERT INTO reply_timelines VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET items=excluded.items,history_end=excluded.history_end',
                   (task_id, start, end, json.dumps(items, ensure_ascii=False)))
    return items


def display_history(store, uid, sid):
    history = store.history(uid, sid)  # ownership check; model history stays unchanged
    with store.db() as db:
        rows = db.execute("SELECT t.*,r.history_start,r.history_end,r.items FROM tasks t JOIN reply_timelines r ON r.task_id=t.id WHERE t.user_id=? AND t.session_id=? AND t.status IN ('completed','failed') ORDER BY t.created_at,t.rowid", (uid,sid)).fetchall()
    result, cursor = [], 0
    for row in rows:
        start, end = row['history_start'], row['history_end']
        result.extend(history[cursor:start])
        payload = json.loads(row['payload'])
        result.append({'role':'user','content':row['message'],'attachments':payload.get('attachments',[])})
        items = json.loads(row['items'])
        result.append({'role':'assistant','content':'\n\n'.join(i['text'] for i in items if i['type']=='text'),'timeline':items,'incomplete':row['status']=='failed'})
        cursor = max(cursor, end, start)
    result.extend(history[cursor:])
    return result
