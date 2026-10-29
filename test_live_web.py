"""Opt-in real authenticated HTTP/WebSocket → remote Codex → neural TTS smoke test.
Run VOICE_WEB_INTEGRATION=1 python -m unittest test_live_web -v.
Starts an isolated authenticated loopback fixture; never changes the deployed account.
"""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import unittest
import wave

import httpx
import websockets


@unittest.skipUnless(os.environ.get('VOICE_WEB_INTEGRATION') == '1', 'opt-in live web/remote AI inference')
class LiveWebTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import socket
        import tempfile
        import threading
        import time
        import uvicorn
        from voice_app.server import create_app
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(create_app(data_dir=self.data.name, public_origin=''), log_level='warning', access_log=False, proxy_headers=False))
        thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
        thread.start()
        def close():
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
        self.addCleanup(close)
        for _ in range(100):
            if server.started:
                break
            time.sleep(.02)
        self.assertTrue(server.started)

    async def test_real_remote_reply_to_valid_neural_wav_over_network(self):
        origin = self.origin
        async with httpx.AsyncClient(trust_env=False) as client:
            self.assertEqual((await client.get(origin + '/health')).status_code, 200)
            self.assertEqual((await client.get(origin + '/api/config')).status_code, 401)
            token = (Path(self.data.name) / 'bootstrap-token').read_text().strip()
            import secrets
            credentials = {'username':'fixture-owner','password':secrets.token_urlsafe(32)}
            result = await client.post(origin + '/api/setup', headers={'Origin':origin}, json={**credentials,'token':token})
            self.assertEqual(result.status_code, 201)
            result = await client.post(origin + '/api/login', headers={'Origin':origin}, json=credentials)
            self.assertEqual(result.status_code, 200)
            cookie = client.cookies.get('sela_session')
            self.assertTrue(bool(cookie))
            config = (await client.get(origin + '/api/config')).json()
            self.assertIn('gpt-6.1-sol', config['providers'][0]['label'])
            self.assertEqual(config['stt_mode'], 'local')
            self.assertTrue(config['stt_available'])
            self.assertEqual(config['browser_stt_allowed'],os.environ.get('VOICE_ALLOW_BROWSER_STT','0')=='1')
            self.assertEqual(config['tts_identity']['backend'], 'piper')
            self.assertEqual(config['tts_identity']['voice'], 'id_ID-news_tts-medium')
            self.assertFalse(config['client_tts_allowed'])
            self.assertEqual((await client.get(origin + '/')).status_code, 200)
            rejected = await client.get(origin + '/health', headers={'host':'attacker.example'})
            self.assertEqual(rejected.status_code, 400)
        events = []
        text = ''
        transcript = ''
        wavs = []
        async with websockets.connect(origin.replace('http://','ws://') + '/ws', origin=origin, additional_headers={'Cookie':'sela_session=' + cookie}, max_size=6000000) as ws:
            await ws.recv()
            await ws.send(json.dumps({'type':'start','provider':'default','mode':'hands_free'}))
            await ws.recv()
            fixture = Path(__file__).resolve().parent / 'verification/human-audio/id_id_0000.wav'
            with wave.open(str(fixture), 'rb') as incoming:
                pcm = incoming.readframes(incoming.getnframes())
            prompt = 'Licensed FLEURS Indonesian human recording id_id_0000; not a physical microphone.'
            await ws.send(json.dumps({'type':'audio','pcm_b64':base64.b64encode(pcm).decode('ascii'),'sample_rate':16000}))
            async with asyncio.timeout(90):
                while True:
                    event = json.loads(await ws.recv())
                    events.append(event['type'])
                    self.assertNotEqual(event['type'], 'error', event)
                    if event['type'] == 'transcript':
                        transcript = event['text']
                    if event['type'] == 'text_delta':
                        text += event['text']
                    if event['type'] == 'audio':
                        audio = event
                        wavs.append(base64.b64decode(event['wav_b64'], validate=True))
                    if event['type'] == 'response_done':
                        token = event['token']
                        break
            self.assertTrue(text.strip())
            self.assertTrue(wavs)
            self.assertLess(events.index('audio'), events.index('response_done'))
            await ws.recv()  # Final snapshot before playback ack.
            await ws.send(json.dumps({'type':'ack','token':token,'chunk_id':audio['chunk_id']}))
            state = json.loads(await ws.recv())['state']
            self.assertEqual(state['history'][-1]['heard_text'], text)
            self.assertEqual(state['history'][0]['source'], 'voice')
            self.assertGreater(len(transcript), 30)
            self.assertEqual(state['history'][0]['text'], transcript)
            self.assertIn('transcript', events)
            # Exercise the same live provider and worker across a second turn,
            # including accumulated history, audio delivery and playback ACK.
            browser_fixture=config['browser_stt_allowed']
            follow_up = {'input_source':'browser_transcript_fixture' if browser_fixture else 'text',
                         'vendor_stt_verified':False, 'prompt':'Oke, mana apakah suara sudah benar?', 'events':[], 'reply':''}
            await ws.send(json.dumps({'type':'voice_text' if browser_fixture else 'text','text':follow_up['prompt']}))
            async with asyncio.timeout(90):
                while True:
                    event = json.loads(await ws.recv())
                    follow_up['events'].append(event['type'])
                    self.assertNotEqual(event['type'],'error',event)
                    if event['type']=='text_delta':follow_up['reply']+=event['text']
                    if event['type']=='audio':
                        follow_audio=event
                        follow_wav=base64.b64decode(event['wav_b64'],validate=True)
                    if event['type']=='response_done':
                        follow_token=event['token'];break
            self.assertTrue(follow_up['reply'].strip())
            self.assertIn('audio',follow_up['events'])
            with wave.open(io.BytesIO(follow_wav),'rb') as follow_wave:
                self.assertEqual(follow_wave.getframerate(),22050)
                self.assertGreater(follow_wave.getnframes(),100)
            await ws.recv()
            await ws.send(json.dumps({'type':'ack','token':follow_token,'chunk_id':follow_audio['chunk_id']}))
            follow_state=json.loads(await ws.recv())['state']
            self.assertEqual(len(follow_state['history']),4)
            self.assertEqual(follow_state['history'][-2]['source'],'voice' if browser_fixture else 'text')
            self.assertEqual(follow_state['history'][-1]['heard_text'],follow_up['reply'])
            follow_up['ack_verified']=True
            follow_up['history_preserved']=True
        with wave.open(io.BytesIO(wavs[0]), 'rb') as wav:
            metadata = {'channels':wav.getnchannels(), 'sample_rate':wav.getframerate(),
                        'sample_width':wav.getsampwidth(), 'frames':wav.getnframes(),
                        'seconds':wav.getnframes()/wav.getframerate(), 'bytes':len(wavs[0])}
            self.assertGreater(metadata['frames'], 100)
            self.assertEqual(metadata['channels'], 1)
            self.assertEqual(metadata['sample_width'], 2)
            self.assertEqual(metadata['sample_rate'], 22050)
            self.assertEqual(int.from_bytes(wavs[0][4:8], 'little'), len(wavs[0])-8)
        from datetime import datetime, timezone
        report = {'verified_at':datetime.now(timezone.utc).isoformat(),
                  'transport':'real loopback HTTP and WebSocket', 'ai_backend':'hermes/openai-codex',
                  'model':'gpt-6.1-sol', 'ai_delivery':config['ai_delivery'],
                  'prompt':prompt, 'transcript':transcript, 'reply':text, 'events':events, 'wav':metadata,
                  'authenticated_fixture':True, 'production_account_unchanged':True, 'ack_verified':True, 'stt_available':config['stt_available'],
                  'tts':config['tts_label'], 'tts_identity':config['tts_identity'],
                  'input_command':'audio', 'transcript_source':'actual local STT of licensed human recording',
                  'microphone_verified':False, 'speaker_verified':False,
                  'follow_up':follow_up, 'stt_live_verified':True}
        root = Path(__file__).resolve().parent / 'verification'
        root.mkdir(exist_ok=True)
        (root/'web-api-smoke.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
        (root/'web-api-smoke.wav').write_bytes(wavs[0])
        print(json.dumps(report, ensure_ascii=False))
