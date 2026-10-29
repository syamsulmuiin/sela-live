"""Style B is a provider instruction, never a punctuation rewrite of literal output."""
import io
import tempfile
import unittest
import wave
from fastapi.testclient import TestClient
from voice_app.server import create_app

class SpokenStyleB(unittest.TestCase):
    def test_style_b_reaches_provider_without_rewriting_reply_or_user_literals(self):
        reply = 'Baik... saya siap membantu! Buka SRC/APP.JS dan MYFUNCTION().' 
        raw = 'SAYA PAKAI API DI SRC/APP.JS DAN MYFUNCTION().'
        class Provider:
            model = 'scripted-style-contract-not-live-provider'
            received = None
            async def stream(self, messages):
                self.received = messages
                yield reply
        class Speech:
            stt_available = False
            stt_reason = 'test fixture'
            tts_label = 'protocol fixture'
            received = None
            async def synthesize(self, text):
                self.received = text
                out = io.BytesIO()
                with wave.open(out, 'wb') as wav:
                    wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    wav.writeframes(b'\0\0' * 1000)
                return out.getvalue()
        provider, speech = Provider(), Speech()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(provider=provider, speech=speech, data_dir=directory,
                             public_origin='', local_test_auth=True)
            with TestClient(app, base_url='http://localhost') as client:
                with client.websocket_connect('/ws', headers={'host':'localhost','origin':'http://localhost'}) as ws:
                    ws.receive_json()
                    ws.send_json({'type':'start'})
                    ws.receive_json()
                    ws.send_json({'type':'voice_text','text':raw})
                    events = []
                    for _ in range(30):
                        event = ws.receive_json(); events.append(event)
                        self.assertNotEqual(event['type'], 'error')
                        if event['type'] == 'audio':
                            ws.send_json({'type':'ack','token':event['token'],'chunk_id':event['chunk_id']})
                        if event['type'] == 'response_done': break
                    self.assertEqual(events[-1]['type'], 'response_done')
        system = provider.received[0]['content']
        self.assertIn('pola tutur B', system)
        self.assertIn('elipsis', system)
        self.assertIn('seru', system)
        self.assertIn('kode', system)
        self.assertIn('jangan', system.lower())
        self.assertIn(raw, [m['content'] for m in provider.received if m['role']=='user'])
        self.assertEqual(speech.received, reply)
        self.assertEqual(''.join(e['text'] for e in events if e['type']=='text_delta'), reply)

if __name__ == '__main__': unittest.main()
