import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
try:
    from voice_app import providers
except ImportError:
    providers = None


class ProviderTests(unittest.TestCase):
    def test_factory_defaults_to_hermes_session_model(self):
        self.assertTrue(hasattr(providers, 'create_provider'), 'provider factory missing')
        provider = providers.create_provider({})
        self.assertEqual(type(provider).__name__, 'HermesProvider')
        self.assertEqual(provider.model, 'gpt-6.1-sol')

    def test_factory_selects_explicit_api_and_hides_key(self):
        provider = providers.create_provider({'VOICE_AI_BACKEND': 'api',
            'VOICE_API_BASE_URL': 'https://api.example.test/v1',
            'VOICE_API_KEY': 'test-only-not-a-real-secret', 'VOICE_MODEL': 'remote-model'})
        self.assertIsInstance(provider, providers.ChatProvider)
        self.assertEqual(provider.base_url, 'https://api.example.test/v1')
        self.assertEqual(provider.model, 'remote-model')
        self.assertEqual(provider.api_key, 'test-only-not-a-real-secret')
        self.assertNotIn('test-only-not-a-real-secret', repr(provider))

    def test_factory_rejects_invalid_configuration_without_echoing_values(self):
        for env in ({'VOICE_AI_BACKEND': 'local-llama'},
                    {'VOICE_AI_BACKEND': 'api'},
                    {'VOICE_AI_BACKEND': 'api', 'VOICE_MODEL': 'm',
                     'VOICE_API_BASE_URL': 'https://api.example.test/v1', 'VOICE_API_KEY': ''},
                    {'VOICE_AI_BACKEND': 'api', 'VOICE_MODEL': 'm',
                     'VOICE_API_BASE_URL': 'https://user:test-secret@example.test/v1',
                     'VOICE_API_KEY': 'test-secret'}):
            with self.subTest(env_keys=list(env)):
                with self.assertRaises(ValueError) as raised:
                    providers.create_provider(env)
                self.assertNotIn('test-secret', str(raised.exception))

    def test_streaming_content_parser(self):
        self.assertIsNotNone(providers, 'streaming provider adapter missing')
        self.assertEqual(providers.parse_delta('data: {"choices":[{"delta":{"content":"Halo"}}]}'), 'Halo')
        self.assertIsNone(providers.parse_delta('data: [DONE]'))
        self.assertIsNone(providers.parse_delta(': heartbeat'))
        self.assertIsNone(providers.parse_delta('data: {"choices":[]}'))
        self.assertIsNone(providers.parse_delta('data: {"choices":[{"delta":{"role":"assistant"}}]}'))
        with self.assertRaises(ValueError):
            providers.parse_delta('data: invalid JSON')


class HermesBridgeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Load the optional SDK before the async loop's slow-callback watchdog.
        import agent.auxiliary_client

    async def test_bridge_resolves_sdk_and_yields_buffered_text(self):
        provider = providers.create_provider({})
        self.assertTrue(hasattr(provider, 'stream'), 'Hermes bridge stream missing')
        create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='Halo dari remote'))]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        messages = [{'role': 'user', 'content': 'Halo'}]
        with patch('agent.auxiliary_client.resolve_provider_client', return_value=(client, 'resolved-model')) as resolve:
            chunks = [chunk async for chunk in provider.stream(messages)]
        self.assertEqual(chunks, ['Halo dari remote'])
        resolve.assert_called_once_with(provider='openai-codex', model='gpt-6.1-sol')
        create.assert_called_once_with(model='resolved-model', messages=messages,
                                       stream=False, max_tokens=768, timeout=60)


    async def test_bridge_rejects_empty_or_nontext_completions(self):
        for content in (None, '', '   ', ['not text']):
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                create=Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=content))])))))
            with self.subTest(content=content):
                with patch('agent.auxiliary_client.resolve_provider_client', return_value=(client, 'm')):
                    with self.assertRaisesRegex(ValueError, 'nonempty text'):
                        _ = [chunk async for chunk in providers.create_provider({}).stream([])]

    async def test_bridge_refreshes_expired_codex_auth_once_without_provider_fallback(self):
        class Fault(RuntimeError):
            status_code=401
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Pulih'))])
        for missing_first in (False,True):
            with self.subTest(missing_first=missing_first):
                create=Mock(side_effect=[response] if missing_first else [Fault('PRIVATE'),response])
                client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
                resolutions=[(None,None),(client,'m')] if missing_first else [(client,'m'),(client,'m')]
                with patch('agent.auxiliary_client.resolve_provider_client',side_effect=resolutions) as resolve, \
                     patch('hermes_cli.auth.resolve_codex_runtime_credentials',return_value={}) as refresh:
                    chunks=[s async for s in providers.create_provider({}).stream([])]
                self.assertEqual(chunks,['Pulih'])
                refresh.assert_called_once_with(force_refresh=True)
                self.assertEqual(resolve.call_count,2)
                for call in resolve.call_args_list:self.assertEqual(call.kwargs['provider'],'openai-codex')

    async def test_bridge_never_refreshes_forbidden_or_server_errors_and_bounds_unauthorized_retry(self):
        class Fault(RuntimeError):
            def __init__(self,status):self.status_code=status
        for status in (401,403,500):
            with self.subTest(status=status):
                create=Mock(side_effect=Fault(status))
                client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
                with patch('agent.auxiliary_client.resolve_provider_client',return_value=(client,'m')), \
                     patch('hermes_cli.auth.resolve_codex_runtime_credentials',return_value={}) as refresh:
                    with self.assertRaises(Fault):
                        _=[s async for s in providers.create_provider({}).stream([])]
                self.assertEqual(refresh.call_count,1 if status==401 else 0)
                self.assertEqual(create.call_count,2 if status==401 else 1)

    async def test_bridge_reports_missing_hermes_auth_without_fallback(self):
        with patch('agent.auxiliary_client.resolve_provider_client', return_value=(None, None)), \
             patch('hermes_cli.auth.resolve_codex_runtime_credentials',return_value={}) as refresh:
            with self.assertRaisesRegex(RuntimeError, 'Hermes openai-codex.*unavailable'):
                _ = [chunk async for chunk in providers.create_provider({}).stream([])]
        refresh.assert_called_once_with(force_refresh=True)


class ApiTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_api_sends_auth_and_streams_sse(self):
        import httpx
        env = {'VOICE_AI_BACKEND': 'api', 'VOICE_API_BASE_URL': 'https://api.example.test/v1',
               'VOICE_API_KEY': 'fake-test-key', 'VOICE_MODEL': 'test-model'}
        messages = [{'role': 'user', 'content': 'Halo'}]

        def handle(request):
            self.assertEqual(str(request.url), 'https://api.example.test/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer fake-test-key')
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], 'test-model')
            self.assertEqual(payload['messages'], messages)
            self.assertTrue(payload['stream'])
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                text='data: {"choices":[{"delta":{"content":"Halo"}}]}\n\n'
                     'data: {"choices":[{"delta":{"content":" dunia"}}]}\n\n'
                     'data: [DONE]\n\n')

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(providers.httpx, 'AsyncClient', return_value=client):
            chunks = [chunk async for chunk in providers.create_provider(env).stream(messages)]
        self.assertEqual(chunks, ['Halo', ' dunia'])

    async def test_api_http_errors_are_not_successful_chunks(self):
        import httpx
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text='Unauthorized')))
        with patch.object(providers.httpx, 'AsyncClient', return_value=client):
            with self.assertRaises(httpx.HTTPStatusError):
                _ = [chunk async for chunk in providers.ChatProvider(
                    'https://api.example.test/v1', 'm').stream([])]


@unittest.skipUnless(os.environ.get('VOICE_INTEGRATION') == '1', 'opt-in real remote inference')
class RealProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_selected_backend_returns_reply(self):
        provider = providers.create_provider()
        prompt = 'Berapa hasil dua tambah dua? Jawab singkat dalam bahasa Indonesia.'
        chunks = [chunk async for chunk in provider.stream([{'role': 'user', 'content': prompt}])]
        self.assertTrue(chunks)
        self.assertTrue(all(isinstance(chunk, str) for chunk in chunks))
        self.assertTrue(''.join(chunks).strip())
        out = Path(__file__).resolve().parents[2] / 'verification'
        out.mkdir(exist_ok=True)
        (out / 'api-llm.json').write_text(json.dumps({
            'prompt': prompt, 'response': ''.join(chunks), 'chunks': len(chunks),
            'backend': os.environ.get('VOICE_AI_BACKEND', 'hermes'),
            'provider': 'openai-codex' if isinstance(provider, providers.HermesProvider) else 'openai-compatible',
            'model': provider.model,
            'delivery': 'buffered' if isinstance(provider, providers.HermesProvider) else 'sse',
            'real_model_called': True}, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
