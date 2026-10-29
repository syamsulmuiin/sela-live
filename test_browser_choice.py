"""Explicit browser choice can coexist with local speech; no automatic fallback."""
import io,tempfile,unittest,wave
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from voice_app.speech import Speech
from voice_app.server import create_app

class BrowserChoiceTests(unittest.TestCase):
    def test_local_backend_can_explicitly_advertise_browser_choice_without_losing_local_stt(self):
        with patch('voice_app.speech._runtime_ready',return_value=True):
            speech=Speech(environ={'VOICE_STT_BACKEND':'local','VOICE_ALLOW_BROWSER_STT':'1'})
        self.assertTrue(speech.stt_available)
        self.assertEqual(speech.stt_mode,'local')
        self.assertTrue(speech.browser_stt_allowed)
        self.assertEqual(speech.tts_identity['backend'],'piper')

    def test_browser_choice_rejects_ambiguous_configuration(self):
        for value in ('yes','true','2',''):
            with self.subTest(value=value), patch('voice_app.speech._runtime_ready',return_value=True):
                with self.assertRaisesRegex(ValueError,'VOICE_ALLOW_BROWSER_STT'):
                    Speech(environ={'VOICE_ALLOW_BROWSER_STT':value})

    def test_two_browser_transcript_turns_bypass_local_asr_and_preserve_history_and_audio_ack(self):
        class Provider:
            model='fixture'
            async def stream(self,messages):yield 'Balasan fixture.'
        with patch('voice_app.speech._runtime_ready',return_value=True):
            speech=Speech(environ={'VOICE_STT_BACKEND':'local','VOICE_ALLOW_BROWSER_STT':'1'})
        out=io.BytesIO()
        with wave.open(out,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(22050);w.writeframes(b'\0\0'*100)
        speech.transcribe=AsyncMock(side_effect=AssertionError('browser text must not invoke local ASR'))
        speech.synthesize=AsyncMock(return_value=out.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(provider=Provider(),speech=speech,data_dir=directory,local_test_auth=True,public_origin='')
            with TestClient(app,base_url='http://localhost') as client:
                config=client.get('/api/config').json()
                self.assertEqual(config['stt_mode'],'local')
                self.assertTrue(config['browser_stt_allowed'])
                with client.websocket_connect('/ws',headers={'host':'localhost','origin':'http://localhost'}) as ws:
                    ws.receive_json();ws.send_json({'type':'start'});ws.receive_json()
                    for text in ('Kalimat fixture pertama.','Kalimat fixture kedua.'):
                        ws.send_json({'type':'voice_text','text':text})
                        for _ in range(20):
                            event=ws.receive_json()
                            self.assertNotEqual(event['type'],'error',event)
                            if event['type']=='audio':audio=event
                            if event['type']=='response_done':break
                        else:self.fail('missing response completion')
                        ws.receive_json()
                        ws.send_json({'type':'ack','token':audio['token'],'chunk_id':audio['chunk_id']})
                        state=ws.receive_json()['state']
                        self.assertEqual(state['history'][-2]['text'],text)
                        self.assertEqual(state['history'][-2]['source'],'voice')
                        self.assertEqual(state['history'][-1]['heard_text'],'Balasan fixture.')
                    self.assertEqual(len(state['history']),4)
        speech.transcribe.assert_not_called()
