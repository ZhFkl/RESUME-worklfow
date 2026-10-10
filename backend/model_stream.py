"""Assemble streamed chat replies/tool calls; expose only public content deltas."""
from types import SimpleNamespace
from openai.types.chat import ChatCompletionMessage


async def streamed_completion(client, on_delta, **kwargs):
    stream = await client.chat.completions.create(**kwargs, stream=True, stream_options={'include_usage': True})
    # Some compatible adapters return an ordinary completion despite stream=True.
    if not hasattr(stream, '__aiter__'):
        content = stream.choices[0].message.content
        if content:
            on_delta(content)
        return stream
    text, calls, usage, finished = [], {}, None, False
    try:
        async for chunk in stream:
            if getattr(chunk, 'usage', None):
                usage = chunk.usage
            for choice in chunk.choices:
                if choice.index != 0:
                    continue
                delta = choice.delta
                if delta.content:
                    text.append(delta.content)
                    on_delta(delta.content)
                for part in delta.tool_calls or []:
                    call = calls.setdefault(part.index, {'id': '', 'type': 'function',
                                                         'function': {'name': '', 'arguments': ''}})
                    if part.id:
                        call['id'] += part.id
                    if part.function:
                        for key in ('name', 'arguments'):
                            call['function'][key] += getattr(part.function, key, None) or ''
                if choice.finish_reason:
                    if choice.finish_reason not in {'stop', 'tool_calls'}:
                        raise RuntimeError('Model stream did not complete normally')
                    finished = True
        if not finished:
            raise RuntimeError('Model stream ended before completion')
    finally:
        await stream.close()
    message = ChatCompletionMessage(role='assistant', content=''.join(text) or None,
                                    tool_calls=[calls[k] for k in sorted(calls)] or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)
