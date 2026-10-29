"""Chunked speech contract; all text and audio here are labeled fixtures."""
import asyncio
import io
import json
import subprocess
import unittest
import wave
from unittest.mock import patch
from tests.python import test_supertonic3_adapter as fixtures
from voice_app.speech import Speech, normalize_pronunciation


class ChunkSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_source_is_preserved_exactly_and_normalized_per_small_piece(self):
        text = ('  Halo dunia! API 999999999 pada 2026-10-27, Rp21.000. 🙂 <laugh>\n\t' * 17) + '  selesai.  '
        with fixtures.configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
        calls = []
        async def process(action, payload, limit):
            calls.append(json.loads(payload)['text'])
            return fixtures.fixture_wav()
        speech._local_request = process
        self.assertTrue(getattr(speech, 'tts_chunked', False), 'Supertonic must opt into chunked delivery')
        parts = [item async for item in speech.synthesize_chunks(text)]
        self.assertGreater(len(parts), 1)
        self.assertEqual(''.join(p[0] for p in parts), text)
        self.assertEqual([p[2] for p in parts], [False] * (len(parts)-1) + [True])
        self.assertEqual(calls, [normalize_pronunciation(p[0].strip()) for p in parts])
        self.assertTrue(all(len(p) <= 180 for p in calls))
        self.assertTrue(all(p[1] == fixtures.fixture_wav() for p in parts))
        self.assertTrue(all('<laugh>' in p[0] or '<' not in p[0] for p in parts))


    async def test_currency_internal_whitespace_keeps_reading_and_exact_ack_source(self):
        from voice_app.session_core import Session
        from voice_app.supertonic3_worker import validate_request
        cases = [
            ('Rp' + ' ' * 1000 + '1', 'satu rupiah'),
            ('Rp' + ' ' * 797 + '1', 'satu rupiah'),  # native 800-character edge
            ('Rp' + ' ' * 798 + '1', 'satu rupiah'),
            ('Rp' + '\u3000' * 3997 + '1', 'satu rupiah'),  # 4k source; >3200 bytes
            ('  API Rp' + '\t\n\u00a0' * 350 + '21.000 pada 2026-10-27 <laugh>  ',
             'a pe i dua puluh satu ribu rupiah pada dua puluh tujuh Oktober dua ribu dua puluh enam <laugh>'),
        ]
        with fixtures.configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
        calls = []
        async def process(action, payload, limit):
            spoken = json.loads(payload)['text']
            validate_request({'text':spoken})
            calls.append(spoken)
            return fixtures.fixture_wav()
        speech._local_request = process
        for text, expected in cases:
            with self.subTest(source_chars=len(text), source_bytes=len(text.encode('utf-8'))):
                calls.clear()
                session = Session(); session.start(); token = session.submit('fixture question')
                session.response_text(token, text)
                if len(text.strip()) > 800 or len(text.strip().encode('utf-8')) > 3200:
                    with self.assertRaisesRegex(ValueError, 'text_limit'):
                        await speech.synthesize(text.strip())
                    self.assertEqual(calls, [], 'direct synthesis must retain its source guard')
                with patch.object(speech, 'synthesize', wraps=speech.synthesize) as synth:
                    parts = [item async for item in speech.synthesize_chunks(text)]
                for call in synth.call_args_list:
                    validate_request({'text':call.args[0]})
                    if len(text.strip()) <= 800:
                        self.assertEqual(call.args[0], text.strip())
                self.assertEqual(''.join(part[0] for part in parts), text)
                self.assertEqual(calls, [expected])
                self.assertEqual([part[2] for part in parts], [True])
                for source, audio, _ in parts:
                    self.assertTrue(session.response_audio(token, audio, source))
                    chunk = session.drain_audio(token)
                    self.assertTrue(session.ack_played(token, chunk.chunk_id))
                self.assertEqual(session.history[-1].text, text)
                self.assertEqual(session.history[-1].heard_text, text)

    async def test_boundaries_invalid_source_and_other_engines_remain_buffered(self):
        from voice_app.tts_chunks import source_parts, synthesis_source
        from voice_app.supertonic3_worker import validate_request
        with fixtures.configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            chunked = Speech(environ=env)
        for text in ('x'*4000, '🙂'*1000, 'Halo' + ' '*3000 + 'akhir',
                     ' <laugh><breath><sigh><laugh> Halo ', '999999999 '*300):
            parts = source_parts(text, normalize_pronunciation)
            self.assertEqual(''.join(parts), text)
            for part in parts:
                source = synthesis_source(part)
                validate_request({'text':source})
                spoken = normalize_pronunciation(source)
                validate_request({'text':spoken})
                self.assertLessEqual(len(spoken), 180)
                self.assertLessEqual(len(spoken.encode('utf-8')), 720)
            with patch.object(chunked, '_local_request', return_value=fixtures.fixture_wav()):
                delivered = [part async for part in chunked.synthesize_chunks(text)]
            self.assertEqual([part[0] for part in delivered], parts)
            self.assertEqual([part[2] for part in delivered], [False] * (len(parts)-1) + [True])
        for text in (None, '', ' ', 'a'*4001, 'Halo\x00', '<angry>Halo', '<laugh', 'a\ud800'):
            with self.subTest(text=repr(text)[:40]), self.assertRaises(ValueError):
                source_parts(text, normalize_pronunciation)
        with patch('voice_app.speech._runtime_ready', return_value=False):
            speech = Speech(environ={'VOICE_TTS_BACKEND':'piper'})
        self.assertFalse(speech.tts_chunked)
        with patch.object(speech, 'synthesize', return_value=fixtures.fixture_wav()) as synth:
            self.assertEqual([x async for x in speech.synthesize_chunks('full text')],
                             [('full text', fixtures.fixture_wav(), True)])
            synth.assert_awaited_once_with('full text')


import tempfile
import threading
from fastapi.testclient import TestClient
from voice_app.server import create_app


class ChunkProvider:
    model = 'SCRIPTED fixture, not Codex'
    async def stream(self, messages):
        yield 'Satu. Dua. Tiga.'


class ChunkAdapter:
    stt_available = True
    stt_reason = ''
    tts_label = 'SCRIPTED fixture'
    tts_chunked = True
    def __init__(self, count=3, block_second=False, fail_second=False):
        self.parts = ['Satu. ', 'Dua. ', 'Tiga.'][:count]
        self.started = []
        self.prefetched = threading.Event()
        self.cancelled = threading.Event()
        self.reaped = threading.Event()
        self.closed = threading.Event()
        self.block_second = block_second
        self.fail_second = fail_second
    async def synthesize(self, text):
        return fixtures.fixture_wav()
    async def transcribe(self, wav):
        return 'SCRIPTED ASR fixture'
    async def synthesize_chunks(self, text):
        try:
            for index, part in enumerate(self.parts):
                self.started.append(index)
                if index == 1:
                    self.prefetched.set()
                    if self.block_second:
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            self.cancelled.set()
                            await asyncio.sleep(.1)  # model owned child-reap latency
                            self.reaped.set()
                            raise
                    if self.fail_second:
                        raise RuntimeError('SCRIPTED failure')
                yield part, fixtures.fixture_wav(), index == len(self.parts)-1
        finally:
            self.closed.set()


class ChunkServerTests(unittest.TestCase):
    def client(self, speech):
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        return TestClient(create_app(provider=ChunkProvider(), speech=speech,
                                     data_dir=data.name, local_test_auth=True))
    def connect(self, client):
        return client.websocket_connect('/ws', headers={'origin':'http://localhost','host':'localhost'})
    def until(self, ws, kind):
        seen = []
        for _ in range(30):
            event = ws.receive_json(); seen.append(event)
            if event['type'] == kind:
                return event, seen
            if event['type'] == 'error':
                self.fail(str(event))
        self.fail('Missing ' + kind)
    def start(self, ws):
        ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
        ws.send_json({'type':'text','text':'SCRIPTED question'})
    def ack(self, ws, audio, **overrides):
        ws.send_json({'type':'ack','token':audio['token'],'chunk_id':audio['chunk_id'], **overrides})
    def test_prefetch_is_one_ahead_delivery_requires_matching_ack(self):
        speech = ChunkAdapter()
        with self.client(speech) as client, self.connect(client) as ws:
            self.start(ws)
            first, initial = self.until(ws, 'audio')
            self.assertIs(first.get('last_chunk'), False)
            self.assertEqual(sum(e['type'] == 'text_delta' for e in initial), 1)
            self.assertTrue(speech.prefetched.wait(2), 'successor should synthesize during playback')
            self.assertEqual(speech.started, [0, 1], 'only one successor may be prefetched')
            for bad in ({'chunk_id':first['chunk_id'] + 99},
                        {'token':first['token'] | {'session_id':'foreign'}}):
                self.ack(ws, first, **bad)
                state, events = self.until(ws, 'state')
                self.assertFalse(any(e['type'] in ('audio', 'response_done') for e in events))
                self.assertEqual(state['state']['history'][-1]['heard_text'], '')
            self.ack(ws, first)
            second, events = self.until(ws, 'audio')
            self.assertFalse(any(e['type'] == 'response_done' for e in events))
            self.assertGreater(second['chunk_id'], first['chunk_id'])
            self.assertEqual(second['token'], first['token'])
            self.ack(ws, first)  # duplicate cannot acknowledge the second
            state, events = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][-1]['heard_text'], 'Satu. ')
            self.assertFalse(any(e['type'] in ('audio','response_done') for e in events))
            self.ack(ws, second)
            third, events = self.until(ws, 'audio')
            self.assertGreater(third['chunk_id'], second['chunk_id'])
            self.assertIs(third['last_chunk'], True)
            done, _ = self.until(ws, 'response_done')  # final ACK remains after done
            self.assertEqual(done['token'], first['token'])
            self.until(ws, 'state')
            self.ack(ws, third)
            state, _ = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][-1]['heard_text'], 'Satu. Dua. Tiga.')

    def test_repeated_interrupt_does_not_cancel_prefetch_reaping(self):
        speech = ChunkAdapter(block_second=True)
        with self.client(speech) as client, self.connect(client) as ws:
            self.start(ws)
            first, _ = self.until(ws, 'audio')
            self.assertTrue(speech.prefetched.wait(2))
            ws.send_json({'type':'interrupt'})
            self.until(ws, 'interrupted'); self.until(ws, 'state')
            self.assertTrue(speech.cancelled.wait(2))
            ws.send_json({'type':'interrupt'})
            self.until(ws, 'interrupted')
            state, events = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][-1]['heard_text'], '')
            self.assertTrue(speech.reaped.wait(2), 'repeated cancellation must not abort child cleanup')
            self.assertTrue(speech.closed.wait(2))
            self.assertEqual(speech.started, [0,1])
            self.ack(ws, first)
            state, events = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][-1]['heard_text'], '')
            self.assertFalse(any(e['type'] in ('audio','response_done') for e in events))

    def test_cancel_on_restart_stop_supersession_and_disconnect(self):
        for command in ('start','stop','text','disconnect'):
            with self.subTest(command=command):
                speech = ChunkAdapter(block_second=True)
                with self.client(speech) as client:
                    with self.connect(client) as ws:
                        self.start(ws); first, _ = self.until(ws, 'audio')
                        self.assertTrue(speech.prefetched.wait(2))
                        if command != 'disconnect':
                            if command == 'text':
                                speech.block_second = False
                                ws.send_json({'type':'text','text':'New scripted turn'})
                                new, events = self.until(ws, 'audio')
                                self.assertNotEqual(new['token'], first['token'])
                                self.assertGreater(new['chunk_id'], first['chunk_id'])
                                self.assertFalse(any(e.get('token') == first['token'] for e in events))
                                ws.send_json({'type':'interrupt'})
                                self.until(ws, 'interrupted'); self.until(ws, 'state')
                            else:
                                ws.send_json({'type':command})
                                state, events = self.until(ws, 'state')
                                self.assertFalse(any(e['type'] in ('audio','response_done') for e in events))
                            self.assertTrue(speech.reaped.wait(2))
                    self.assertTrue(speech.cancelled.wait(2))
                    self.assertTrue(speech.reaped.wait(2), 'disconnect must await owned cleanup')

    def test_ack_timeout_is_per_piece_and_cancels_successor(self):
        speech = ChunkAdapter(block_second=True)
        with patch('voice_app.server.CHUNK_ACK_TIMEOUT', .12), self.client(speech) as client, self.connect(client) as ws:
            self.start(ws); first, _ = self.until(ws, 'audio')
            done, events = self.until(ws, 'response_done')
            self.assertFalse(done['audio_available'])
            self.assertFalse(any(e['type'] == 'audio' for e in events))
            error = ws.receive_json(); self.assertEqual(error['code'], 'tts_timeout')
            state, _ = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][-1]['text'], 'Satu. Dua. Tiga.')
            self.assertEqual(state['state']['history'][-1]['heard_text'], '')
            self.assertTrue(speech.reaped.wait(2))

    def test_partial_failure_keeps_acknowledged_source_and_complete_visible_text(self):
        speech = ChunkAdapter(fail_second=True)
        with self.client(speech) as client, self.connect(client) as ws:
            self.start(ws); first, _ = self.until(ws, 'audio')
            self.assertTrue(speech.prefetched.wait(2))
            self.ack(ws, first)
            done, events = self.until(ws, 'response_done')
            self.assertFalse(done['audio_available'])
            self.assertFalse(any(e['type'] == 'audio' for e in events))
            error = ws.receive_json(); self.assertEqual(error['code'], 'tts_failed')
            state, _ = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][-1]['heard_text'], 'Satu. ')
            self.assertEqual(state['state']['history'][-1]['text'], 'Satu. Dua. Tiga.')

    def test_one_chunk_and_legacy_adapters_keep_audio_done_ack_order_and_repeated_asr(self):
        for chunked in (True, False):
            speech = ChunkAdapter(count=1)
            if not chunked:
                speech.synthesize_chunks = None
                speech.tts_chunked = False
            with self.client(speech) as client, self.connect(client) as ws:
                ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                for turn in range(2):
                    ws.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
                    done, events = self.until(ws, 'response_done')
                    audio = [e for e in events if e['type'] == 'audio']
                    self.assertEqual(len(audio), 1)
                    if chunked:
                        self.assertTrue(audio[0]['last_chunk'])
                    self.until(ws, 'state'); self.ack(ws, audio[0])
                    state, _ = self.until(ws, 'state')
                    self.assertEqual(state['state']['history'][-1]['heard_text'],
                                     'Satu. ' if chunked else 'Satu. Dua. Tiga.')
                    self.assertEqual(len(state['state']['history']), 2*(turn+1))

    def test_each_ack_has_fresh_deadline_not_a_whole_answer_deadline(self):
        import time
        speech = ChunkAdapter()
        with patch('voice_app.server.CHUNK_ACK_TIMEOUT', .22), self.client(speech) as client, self.connect(client) as ws:
            self.start(ws); first, _ = self.until(ws, 'audio')
            time.sleep(.13); self.ack(ws, first)
            second, _ = self.until(ws, 'audio')
            time.sleep(.13); self.ack(ws, second)
            third, _ = self.until(ws, 'audio')
            self.until(ws, 'response_done'); self.until(ws, 'state')
            self.ack(ws, third)
            state, _ = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][-1]['heard_text'], 'Satu. Dua. Tiga.')

    def test_completed_prefetch_is_discarded_when_interrupted_waiting_for_ack(self):
        speech = ChunkAdapter()
        with self.client(speech) as client, self.connect(client) as ws:
            self.start(ws); first, _ = self.until(ws, 'audio')
            self.assertTrue(speech.prefetched.wait(2))
            ws.send_json({'type':'interrupt'})
            self.until(ws, 'interrupted'); self.until(ws, 'state')
            self.assertTrue(speech.closed.wait(2))
            self.ack(ws, first)
            state, events = self.until(ws, 'state')
            self.assertFalse(any(e['type'] in ('audio','response_done') for e in events))
            self.assertEqual(state['state']['history'][-1]['heard_text'], '')
            self.assertEqual(speech.started, [0,1])

    def test_revoked_auth_prevents_successor_emission(self):
        class Auth:
            allowed = True
            def valid_session(self, cookie): return self.allowed
        auth = Auth()
        speech = ChunkAdapter()
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(provider=ChunkProvider(), speech=speech, data_dir=tmp, auth=auth)
            with TestClient(app) as client, self.connect(client) as ws:
                self.start(ws); first, _ = self.until(ws, 'audio')
                self.assertTrue(speech.prefetched.wait(2))
                auth.allowed = False
                self.ack(ws, first)
                from starlette.websockets import WebSocketDisconnect
                with self.assertRaises(WebSocketDisconnect): ws.receive_json()
                self.assertTrue(speech.closed.wait(2))
                self.assertEqual(speech.started, [0,1])

    def test_config_advertises_chunked_only_when_selected(self):
        for selected in (True, False):
            speech = ChunkAdapter(); speech.tts_chunked = selected
            with self.client(speech) as client:
                config = client.get('/api/config', headers={'host':'localhost'}).json()
                self.assertEqual(config.get('tts_delivery'), 'chunked' if selected else 'buffered')


if __name__ == '__main__':
    unittest.main()
