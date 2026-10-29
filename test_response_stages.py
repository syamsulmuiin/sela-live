"""Response-stage errors stay distinct and generated text survives TTS failure."""
import tempfile
import unittest
from fastapi.testclient import TestClient
from voice_app.server import create_app
from voice_app.speech import LocalAudioBusy

class ResponseStageTests(unittest.TestCase):
    def test_llm_and_tts_failures_have_safe_specific_codes_and_preserve_text(self):
        class Fault(RuntimeError):
            def __init__(self,status=None):
                self.status_code=status
                super().__init__('PRIVATE upstream request body and credentials')
        cases=[('llm',Fault(),'llm_failed'),('llm',Fault(401),'llm_auth'),
               ('llm',Fault(429),'llm_rate_limited'),('llm',TimeoutError('PRIVATE'),'llm_timeout'),
               ('tts',Fault(),'tts_failed'),('tts',LocalAudioBusy('PRIVATE'),'tts_busy'),
               ('tts',TimeoutError('PRIVATE'),'tts_timeout')]
        for stage,fault,expected in cases:
            with self.subTest(code=expected), tempfile.TemporaryDirectory() as directory:
                class Provider:
                    model='fixture'
                    async def stream(self,messages):
                        if stage=='llm':raise fault
                        yield 'Balasan fixture.'
                class Speech:
                    stt_available=False;stt_reason='fixture';tts_label='fixture'
                    async def synthesize(self,text):raise fault
                app=create_app(provider=Provider(),speech=Speech(),data_dir=directory,public_origin='',local_test_auth=True)
                with TestClient(app,base_url='http://localhost') as client:
                    with client.websocket_connect('/ws',headers={'host':'localhost','origin':'http://localhost'}) as ws:
                        ws.receive_json();ws.send_json({'type':'start'});ws.receive_json()
                        events=[]
                        with self.assertLogs('voice_app.server',level='WARNING') as logs:
                            ws.send_json({'type':'text','text':'Pesan fixture'})
                            for _ in range(15):
                                event=ws.receive_json();events.append(event)
                                if event['type']=='error':break
                        self.assertEqual(event.get('code'),expected)
                        self.assertEqual(event.get('stage'),stage)
                        self.assertNotIn('PRIVATE',str(events)+str(logs.output))
                        snapshot=ws.receive_json()['state']
                        if stage=='tts':
                            self.assertEqual(snapshot['history'][-1]['text'],'Balasan fixture.')
                            self.assertEqual(snapshot['history'][-1]['status'],'complete')
                            self.assertEqual(snapshot['history'][-1]['heard_text'],'')
                            done=next(e for e in events if e['type']=='response_done')
                            self.assertIs(done.get('audio_available'),False)
                        else:self.assertFalse(any(e['type']=='text_delta' for e in events))
