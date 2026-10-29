"""HTTP SSE parsing and provider integration boundary."""
import asyncio
import json
import os
from typing import ClassVar
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


class CodexAuthUnavailable(RuntimeError):
    status_code = 401


@dataclass(frozen=True)
class HermesProvider:
    delivery_protocol_version: ClassVar[int] = 1
    model: str = 'gpt-6.1-sol'

    async def stream(self, messages):
        # The supported Codex adapter returns an assembled completion, not deltas.
        def complete():
            from agent.auxiliary_client import resolve_provider_client
            for attempt in range(2):
                try:
                    client, model = resolve_provider_client(provider='openai-codex', model=self.model)
                    if client is None or not model:
                        raise CodexAuthUnavailable('Hermes openai-codex provider unavailable; sign in with Hermes or select the api backend')
                    response = client.chat.completions.create(
                        model=model, messages=messages, stream=False, max_tokens=768, timeout=60)
                except Exception as exc:
                    # Direct SDK callers bypass Hermes' higher-level auth recovery.
                    # Retry this same Codex account once, never another provider.
                    if attempt or getattr(exc, 'status_code', None) != 401:
                        raise
                    try:
                        from hermes_cli.auth import resolve_codex_runtime_credentials
                        resolve_codex_runtime_credentials(force_refresh=True)
                    except Exception:
                        raise exc from None
                    continue
                content = response.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise ValueError('Hermes completion must contain nonempty text')
                return content

        yield await asyncio.to_thread(complete)


def create_provider(environ=None):
    env = os.environ if environ is None else environ
    backend = env.get('VOICE_AI_BACKEND', 'hermes')
    if backend not in ('hermes', 'api'):
        raise ValueError('VOICE_AI_BACKEND must be hermes or api')
    if backend == 'api':
        for name in ('VOICE_API_BASE_URL', 'VOICE_API_KEY', 'VOICE_MODEL'):
            if not env.get(name, '').strip():
                raise ValueError(name + ' is required for the api backend')
        url = urlsplit(env['VOICE_API_BASE_URL'])
        if (url.scheme not in ('https', 'http') or not url.hostname
                or url.username or url.password or url.query or url.fragment):
            raise ValueError('VOICE_API_BASE_URL must be an HTTP(S) URL without credentials, query or fragment')
        return ChatProvider(base_url=env['VOICE_API_BASE_URL'],
                            model=env['VOICE_MODEL'], api_key=env['VOICE_API_KEY'])
    return HermesProvider(model=env.get('VOICE_MODEL') or 'gpt-6.1-sol')


@dataclass(frozen=True)
class ChatProvider:
    delivery_protocol_version: ClassVar[int] = 1
    base_url: str
    model: str
    api_key: str = field(default='', repr=False)

    async def stream(self, messages):
        headers = {'Authorization': 'Bearer ' + self.api_key} if self.api_key else {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), trust_env=False) as client:
            async with client.stream('POST', self.base_url.rstrip('/') + '/chat/completions',
                    headers=headers, json={'model': self.model, 'messages': messages,
                        'stream': True, 'max_tokens': 768, 'temperature': 0.2}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.strip() == 'data: [DONE]':
                        break
                    delta = parse_delta(line)
                    if delta:
                        yield delta


def parse_delta(line: str) -> str | None:
    if not line.startswith('data:'):
        return None
    payload = line[5:].strip()
    if payload == '[DONE]':
        return None
    data = json.loads(payload)
    choices = data.get('choices', [])
    if not choices:
        return None
    content = choices[0].get('delta', {}).get('content')
    if content is not None and not isinstance(content, str):
        raise ValueError('provider content must be text')
    return content
