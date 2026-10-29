"""Stateful adapter/wire fixtures; these are not native speech accuracy tests."""
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from voice_app.speech import Speech
from voice_app.audio import pcm16_wav
from test_zipformer import ENV


class StatefulSelection(unittest.TestCase):
    def test_stateful_is_explicit_and_requires_selected_zipformer(self):
        with patch('voice_app.speech._runtime_ready', return_value=True):
            self.assertFalse(getattr(Speech(ENV), 'stateful_preview', False))
            speech = Speech(ENV | {'VOICE_ZIPFORMER_STATEFUL': '1'})
            self.assertTrue(getattr(speech, 'stateful_preview', False))
            self.assertEqual(speech.preview_source, 'local_zipformer_id_stream')
            self.assertEqual(speech.preview_semantics, 'stateful_incremental_decode_over_bounded_prefix_snapshots')
            self.assertEqual(speech.tts_identity['voice'], 'id_ID-news_tts-medium')
            for env in ({'VOICE_ZIPFORMER_STATEFUL': '1'},
                        ENV | {'VOICE_ZIPFORMER_STATEFUL': 'bad'},
                        ENV | {'VOICE_ZIPFORMER_STATEFUL': '1', 'VOICE_LOCAL_PREVIEW': '0'}):
                with self.subTest(env_keys=tuple(env)), self.assertRaises(ValueError):
                    Speech(env)

    def test_stateful_constructs_only_the_new_worker_transport(self):
        import sys, types
        module = types.ModuleType('voice_app.stateful_asr')
        class Worker:
            def __init__(self, args, env, **kwargs):
                self.args, self.env = args, env
        module.StatefulASR = Worker
        with patch('voice_app.speech._runtime_ready', return_value=True):
            speech = Speech(ENV | {'VOICE_ZIPFORMER_STATEFUL': '1'})
        with patch.dict(sys.modules, {'voice_app.stateful_asr': module}), patch('voice_app.speech.Path.read_text', return_value='MemAvailable: 2000000 kB\n'):
            worker = speech._preview_worker()
        self.assertIsInstance(worker, Worker)
        self.assertTrue(any(arg.endswith('/zipformer_stateful_worker.py') for arg in worker.args))
        self.assertNotIn('VOICE_STT_API_KEY', worker.env)


class StatefulAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_preview_final_and_forget_share_explicit_owner_without_remote_route(self):
        with patch('voice_app.speech._runtime_ready', return_value=True):
            speech = Speech(ENV | {'VOICE_ZIPFORMER_STATEFUL': '1'})
        self.assertTrue(callable(getattr(speech, 'discard_stream', None)), 'missing stateful cleanup adapter')
        worker = type('Worker', (), {'preview': AsyncMock(return_value='draft'),
            'transcribe': AsyncMock(return_value='final'), 'forget': AsyncMock()})()
        speech._stream_asr = worker
        wav = pcm16_wav(b'\x01\x01' * 16000, 16000)
        key = 'a' * 32
        self.assertEqual(await speech.preview(wav, stream_key=key), 'draft')
        self.assertEqual(await speech.transcribe(wav, stream_key=key), 'final')
        await speech.discard_stream(key)
        worker.preview.assert_awaited_once_with(wav, stream_key=key)
        worker.transcribe.assert_awaited_once_with(wav, stream_key=key)
        worker.forget.assert_awaited_once_with(key)
        self.assertFalse(speech._local_slot.locked())

    async def test_forget_does_not_construct_an_inactive_worker(self):
        with patch('voice_app.speech._runtime_ready', return_value=True):
            speech = Speech(ENV | {'VOICE_ZIPFORMER_STATEFUL': '1'})
        self.assertTrue(callable(getattr(speech, 'discard_stream', None)), 'missing stateful cleanup adapter')
        with patch.object(speech, '_preview_worker', side_effect=AssertionError('must not warm')):
            await speech.discard_stream('b' * 32)


from fastapi.testclient import TestClient
from voice_app.server import create_app
import test_preview_protocol as fixtures


class StatefulDraft(fixtures.DraftSpeech):
    stateful_preview = True
    preview_source = 'local_zipformer_id_stream'
    preview_semantics = 'stateful_incremental_decode_over_bounded_prefix_snapshots'
    def __init__(self):
        super().__init__()
        self.preview_keys, self.final_keys, self.discard_keys = [], [], []
    async def preview(self, wav, *, stream_key=None):
        self.preview_keys.append(stream_key)
        return await super().preview(wav)
    async def transcribe(self, wav, *, stream_key=None):
        self.final_keys.append(stream_key)
        return await super().transcribe(wav)
    async def discard_stream(self, stream_key):
        self.discard_keys.append(stream_key)


class StatefulProtocol(unittest.TestCase):
    until = fixtures.PreviewProtocolTests.until
    preview = fixtures.PreviewProtocolTests.preview
    final = fixtures.PreviewProtocolTests.final
    connect = fixtures.PreviewProtocolTests.connect
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.speech, self.provider = StatefulDraft(), fixtures.CountProvider()
    def app(self):
        return create_app(provider=self.provider, speech=self.speech,
                          data_dir=self.directory.name, local_test_auth=True, public_origin='')

    def test_config_advertises_stateful_half_second_updates(self):
        with TestClient(self.app()) as c:
            cfg = c.get('/api/config', headers={'host': 'localhost'}).json()
            self.assertTrue(cfg.get('local_stateful_preview_supported'))
            self.assertEqual(cfg['local_preview_interval_ms'], 500)

    def test_half_second_partial_and_final_share_owner_but_sockets_do_not(self):
        with TestClient(self.app()) as c, self.connect(c) as a, self.connect(c) as b:
            for ws in (a, b):
                ws.receive_json(); ws.send_json({'type': 'start'}); self.until(ws, 'state')
            a.send_json(self.preview(.5, id=1)); self.until(a, 'transcript_partial')
            first = self.speech.preview_keys[-1]
            self.assertIsNotNone(first)
            self.assertRegex(first, r'^[0-9a-f]{32}$')
            with patch('voice_app.server.STATEFUL_PREVIEW_MIN_INTERVAL', 0, create=True):
                a.send_json(self.preview(1, id=1)); self.until(a, 'transcript_partial')
            self.assertEqual(self.speech.preview_keys[-1], first)
            b.send_json(self.preview(.5, id=1)); self.until(b, 'transcript_partial')
            second = self.speech.preview_keys[-1]
            self.assertNotEqual(second, first)
            self.assertEqual(self.provider.calls, 0)
            a.send_json(self.final(id=1)); self.until(a, 'response_done'); self.until(a, 'state')
            self.assertEqual(self.speech.final_keys, [first])
            self.assertEqual(self.provider.calls, 1)

    def test_lifecycle_boundary_discards_only_its_own_stream(self):
        import threading
        for boundary in ('mute', 'stop', 'interrupt', 'start', 'disconnect'):
            with self.subTest(boundary=boundary):
                self.speech = StatefulDraft()
                completed = threading.Event()
                async def discard(key):
                    self.speech.discard_keys.append(key)
                    completed.set()
                self.speech.discard_stream = discard
                with TestClient(self.app()) as c, self.connect(c) as ws:
                    ws.receive_json(); ws.send_json({'type': 'start'}); self.until(ws, 'state')
                    ws.send_json(self.preview(.5, id=1)); self.until(ws, 'transcript_partial')
                    key = self.speech.preview_keys[-1]
                    if boundary == 'disconnect':
                        ws.close()
                    else:
                        command = {'type': boundary}
                        if boundary == 'mute': command['muted'] = True
                        ws.send_json(command); self.until(ws, 'state')
                    self.assertTrue(completed.wait(2), 'native stream not discarded at ' + boundary)
                    self.assertEqual(self.speech.discard_keys, [key])

    def test_muted_then_unmuted_final_cannot_dispatch_late_transcript_or_llm(self):
        import threading
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        async def delayed(wav, *, stream_key=None):
            entered.set()
            while not release.is_set(): await asyncio.sleep(.002)
            completed.set()
            return 'final utterance'
        self.speech.transcribe = delayed
        with TestClient(self.app()) as c, self.connect(c) as ws:
            ws.receive_json(); ws.send_json({'type': 'start'}); self.until(ws, 'state')
            ws.send_json(self.final()); self.until(ws, 'processing')
            self.assertTrue(entered.wait(2))
            for muted in (True, False):
                ws.send_json({'type': 'mute', 'muted': muted}); self.until(ws, 'state')
            release.set(); self.assertTrue(completed.wait(2))
            ws.send_json({'type': 'provider', 'provider': 'default'})
            self.assertEqual(ws.receive_json()['type'], 'state', 'late final leaked across mute lifecycle')
            self.assertEqual(self.provider.calls, 0)

    def test_ptt_release_after_final_admission_does_not_invalidate_that_final(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        async def delayed(wav, *, stream_key=None):
            entered.set()
            while not release.is_set(): await asyncio.sleep(.002)
            return 'final utterance'
        self.speech.transcribe = delayed
        with TestClient(self.app()) as c, self.connect(c) as ws:
            ws.receive_json(); ws.send_json({'type': 'start', 'mode': 'push_to_talk'}); self.until(ws, 'state')
            ws.send_json({'type': 'press'}); self.until(ws, 'state')
            ws.send_json(self.final()); self.until(ws, 'processing'); self.assertTrue(entered.wait(2))
            ws.send_json({'type': 'release'}); self.until(ws, 'state')
            release.set(); self.until(ws, 'response_done'); self.until(ws, 'state')
            self.assertEqual(self.provider.calls, 1)

    def test_final_does_not_discard_live_state_before_using_it(self):
        original = self.speech.transcribe
        async def final(wav, *, stream_key=None):
            self.assertNotIn(stream_key, self.speech.discard_keys)
            return await original(wav, stream_key=stream_key)
        self.speech.transcribe = final
        with TestClient(self.app()) as c, self.connect(c) as ws:
            ws.receive_json(); ws.send_json({'type': 'start'}); self.until(ws, 'state')
            ws.send_json(self.preview(.5)); self.until(ws, 'transcript_partial')
            ws.send_json(self.final()); self.until(ws, 'response_done'); self.until(ws, 'state')
            self.assertEqual(self.provider.calls, 1)
