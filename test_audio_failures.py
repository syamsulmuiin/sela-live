"""Distinct recoverable speech failures; no private exception strings on wire."""
import tempfile
import unittest
from fastapi.testclient import TestClient
from voice_app.server import create_app
from voice_app.speech import LocalAudioBusy, NoSpeechDetected

class AudioFailureTests(unittest.TestCase):
    def test_transcription_failure_categories_are_distinct_and_socket_recovers(self):
        class Provider:
            model='fixture'
            async def stream(self,messages):
                yield 'Fixture.'
        class Speech:
            stt_available=True; stt_reason=''; stt_mode='local'; browser_stt_allowed=False
            tts_label='fixture'
            async def transcribe(self,wav):
                raise self.failure
        cases=[('no_speech',NoSpeechDetected('PRIVATE diagnostic input')),
               ('local_audio_busy',LocalAudioBusy('PRIVATE diagnostic input')),
               ('audio_timeout',TimeoutError('PRIVATE diagnostic input')),
               ('audio_worker_failed',RuntimeError('PRIVATE diagnostic input'))]
        for expected,failure in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                speech=Speech();speech.failure=failure
                app=create_app(provider=Provider(),speech=speech,data_dir=directory,public_origin='',local_test_auth=True)
                with TestClient(app,base_url='http://localhost') as client:
                    with client.websocket_connect('/ws',headers={'origin':'http://localhost','host':'localhost'}) as ws:
                        ws.receive_json();ws.send_json({'type':'start'});ws.receive_json()
                        ws.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
                        for _ in range(10):
                            event=ws.receive_json()
                            if event['type']=='error':break
                        self.assertEqual(event.get('code'),expected)
                        self.assertEqual(event.get('command'),'audio')
                        self.assertNotIn('PRIVATE',str(event))
                        if expected=='no_speech':self.assertNotIn('sibuk',event['message'])
                        ws.send_json({'type':'stop'})
                        self.assertEqual(ws.receive_json()['type'],'state')
