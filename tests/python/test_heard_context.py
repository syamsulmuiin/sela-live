"""Actual ASGI conversation continuity; fixture audio/provider, no inference."""
import tempfile
import unittest

from fastapi.testclient import TestClient
from voice_app.audio import pcm16_wav
from voice_app.server import create_app

HEAD = '  Bagian pertama; \n'
TAIL = 'Bagian kedua belum terdengar.'
HEADERS = {'host': 'localhost', 'origin': 'http://localhost'}


class RecordingProvider:
    model = 'fixture'

    def __init__(self):
        self.messages = []

    async def stream(self, messages):
        self.messages.append([dict(message) for message in messages])
        yield HEAD + TAIL


class ChunkSpeech:
    stt_available = False
    stt_reason = 'fixture'
    tts_label = 'fixture'

    async def synthesize_chunks(self, text):
        yield HEAD, pcm16_wav(b'\0\0' * 100, 16000), False
        yield TAIL, pcm16_wav(b'\0\0' * 100, 16000), True


class HeardContextTests(unittest.TestCase):
    def until(self, ws, kind, predicate=lambda event: True, observed=None):
        for _ in range(60):
            event = ws.receive_json()
            if observed is not None:
                observed.append(event)
            self.assertNotEqual(event['type'], 'error', event)
            if event['type'] == kind and predicate(event):
                return event
        self.fail('Missing event: ' + kind)

    def next_turn_context(self, *, acknowledge_prefix=True, complete=False):
        provider = RecordingProvider()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(provider=provider, speech=ChunkSpeech(),
                             local_test_auth=True, data_dir=directory)
            with TestClient(app) as client, client.websocket_connect('/ws', headers=HEADERS) as ws:
                ws.receive_json()
                ws.send_json({'type': 'start'})
                self.until(ws, 'state')
                ws.send_json({'type': 'text', 'text': 'Pertanyaan pertama.'})
                audio = self.until(ws, 'audio')
                heard = ''
                if acknowledge_prefix:
                    ws.send_json({'type': 'ack', 'token': audio['token'], 'chunk_id': audio['chunk_id']})
                    observed = []
                    self.until(ws, 'state', lambda e: e['state']['history'][-1]['heard_text'] == HEAD,
                               observed=observed)
                    heard = HEAD
                    if complete:
                        next_audio = next((event for event in observed if event['type'] == 'audio'), None)
                        next_audio = next_audio if next_audio is not None else self.until(ws, 'audio')
                        ws.send_json({'type': 'ack', 'token': next_audio['token'],
                                      'chunk_id': next_audio['chunk_id']})
                        self.until(ws, 'state', lambda e: e['state']['history'][-1]['heard_text'] == HEAD + TAIL
                                   and e['state']['history'][-1]['status'] == 'complete')
                        heard = HEAD + TAIL
                ws.send_json({'type': 'interrupt'})
                self.until(ws, 'interrupted')
                before = self.until(ws, 'state')['state']['history'][-1]
                self.assertEqual(before['status'], 'complete' if complete else 'interrupted')
                self.assertEqual(before['text'], HEAD + TAIL)
                self.assertEqual(before['heard_text'], heard)
                ws.send_json({'type': 'text', 'text': 'Lanjutkan dari yang tadi terdengar.'})
                self.until(ws, 'audio')
                ws.send_json({'type': 'stop'})
                self.until(ws, 'state', lambda e: not e['state']['open'])
        self.assertEqual(len(provider.messages), 2)
        return provider.messages[1][1:]

    def test_acknowledged_interrupted_prefix_reaches_next_reasoning_turn_exactly(self):
        self.assertEqual(self.next_turn_context(), [
            {'role': 'user', 'content': 'Pertanyaan pertama.'},
            {'role': 'assistant', 'content': HEAD},
            {'role': 'user', 'content': 'Lanjutkan dari yang tadi terdengar.'},
        ])

    def test_unacknowledged_interrupted_answer_is_not_invented_as_heard_context(self):
        self.assertEqual(self.next_turn_context(acknowledge_prefix=False), [
            {'role': 'user', 'content': 'Pertanyaan pertama.'},
            {'role': 'user', 'content': 'Lanjutkan dari yang tadi terdengar.'},
        ])

    def test_completed_acknowledged_answer_retains_exact_original_context(self):
        self.assertEqual(self.next_turn_context(complete=True), [
            {'role': 'user', 'content': 'Pertanyaan pertama.'},
            {'role': 'assistant', 'content': HEAD + TAIL},
            {'role': 'user', 'content': 'Lanjutkan dari yang tadi terdengar.'},
        ])


if __name__ == '__main__':
    unittest.main()
