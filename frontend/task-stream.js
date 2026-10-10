// Read SSE through fetch so account guards and normal authentication still apply.
export async function readTaskStream(response, {onText, onTask, signal}) {
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('流式连接不可用');
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let pending = '';
  const cancel = () => void reader.cancel().catch(() => {});
  signal.addEventListener('abort', cancel, {once:true});
  try {
    while (!signal.aborted) {
      const {done, value} = await reader.read();
      if (done) break;
      pending += decoder.decode(value, {stream:true});
      let end;
      while ((end = pending.indexOf('\n\n')) !== -1) {
        const frame = pending.slice(0, end); pending = pending.slice(end + 2);
        const lines = frame.split('\n');
        const event = lines.find(line => line.startsWith('event:'))?.slice(6).trim();
        const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        const payload = JSON.parse(data);
        if (event === 'text') onText(payload);
        if (event === 'task') {
          onTask(payload);
          if (['completed', 'failed'].includes(payload.status)) return payload;
        }
      }
    }
    throw new Error('流式连接中断');
  } finally {
    signal.removeEventListener('abort', cancel);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
