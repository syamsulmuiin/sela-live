import base64
import io
import unittest
import wave
from fastapi.testclient import TestClient

class Provider:
    model = 'fixture'
    async def stream(self, messages):
        yield 'Halo dari pengujian.'

class Speech:
    stt_available = False
    stt_reason = 'STT belum dikonfigurasi'
    tts_label = 'fixture'
    async def synthesize(self, text):
        from voice_app.audio import pcm16_wav
        return pcm16_wav(b'\0\0' * 100, 16000)

class ServerTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)

    def app(self, **kwargs):
        from voice_app.server import create_app
        return create_app(provider=kwargs.pop('provider', Provider()), speech=Speech(), local_test_auth=True, data_dir=self.data.name, **kwargs)

    def connect(self, client):
        return client.websocket_connect('/ws', headers={'origin':'http://localhost', 'host':'localhost'})

    def until(self, ws, kind):
        events = []
        for _ in range(30):
            event = ws.receive_json(); events.append(event)
            if event['type'] == 'error':
                self.fail(str(event))
            if event['type'] == kind:
                return event, events
        self.fail('event missing: ' + kind)

    def test_closed_session_error_is_structured_safe_and_does_not_start_session(self):
        with TestClient(self.app()) as client, self.connect(client) as ws:
            ws.receive_json()
            with self.assertLogs('voice_app.server', level='WARNING') as logs:
                ws.send_json({'type':'voice_text', 'text':'PRIVATE TRANSCRIPT'})
                error = ws.receive_json()
            self.assertEqual(error, {'type':'error', 'code':'session_not_ready',
                'command':'voice_text', 'message':'Sesi belum aktif. Mulai sesi terlebih dahulu.'})
            self.assertEqual(logs.output, ['WARNING:voice_app.server:command=voice_text errorcode=session_not_ready open=False muted=False inflight_jobs=0'])
            ws.send_json({'type':'provider','provider':'default'})
            self.assertFalse(ws.receive_json()['state']['open'])

    def test_invalid_message_never_echoes_untrusted_command_or_reason(self):
        with TestClient(self.app()) as client, self.connect(client) as ws:
            ws.receive_json()
            for raw, command in (('{broken PRIVATE', 'unknown'),
                                 ('{"type":"PRIVATE\\nCOMMAND"}', 'unknown'),
                                 ('{"type":[]}', 'unknown'),
                                 ('{"type":"voice_text","text":""}', 'voice_text'),
                                 ('{"type":"audio","pcm_b64":"AAAAAA==","sample_rate":48000}', 'audio')):
                with self.assertLogs('voice_app.server', level='WARNING') as logs:
                    ws.send_text(raw)
                    error = ws.receive_json()
                self.assertEqual(error, {'type':'error', 'code':'invalid_message',
                    'command':command, 'message':'Pesan tidak valid. Muat ulang halaman lalu coba lagi.'})
                self.assertEqual(logs.output, [f'WARNING:voice_app.server:command={command} errorcode=invalid_message open=False muted=False inflight_jobs=0'])

    def test_muted_browser_transcript_has_actionable_error_without_work(self):
        with TestClient(self.app()) as client, self.connect(client) as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            ws.send_json({'type':'mute','muted':True}); ws.receive_json()
            with self.assertLogs('voice_app.server', level='WARNING') as logs:
                ws.send_json({'type':'voice_text','text':'PRIVATE TRANSCRIPT'})
                self.assertEqual(ws.receive_json(), {'type':'error', 'code':'microphone_muted',
                    'command':'voice_text', 'message':'Mikrofon dibisukan. Aktifkan mikrofon lalu coba lagi.'})
            self.assertEqual(logs.output, ['WARNING:voice_app.server:command=voice_text errorcode=microphone_muted open=True muted=True inflight_jobs=0'])
            ws.send_json({'type':'provider','provider':'default'})
            self.assertEqual(ws.receive_json()['state']['history'], [])

    def test_unavailable_api_stt_is_safe_even_when_reason_contains_secrets(self):
        from voice_app.server import create_app
        speech = Speech()
        speech.stt_reason = 'PRIVATE CREDENTIAL REASON'
        with TestClient(create_app(provider=Provider(), speech=speech, local_test_auth=True,
                                  data_dir=self.data.name)) as client, self.connect(client) as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            with self.assertLogs('voice_app.server', level='WARNING') as logs:
                ws.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
                self.assertEqual(ws.receive_json(), {'type':'error', 'code':'api_stt_unavailable',
                    'command':'audio', 'message':'STT belum tersedia. Periksa konfigurasi server atau kirim pesan teks.'})
            self.assertEqual(logs.output, ['WARNING:voice_app.server:command=audio errorcode=api_stt_unavailable open=True muted=False inflight_jobs=0'])

    def test_closed_lifecycle_cleanup_is_idempotent_without_starting_session(self):
        with TestClient(self.app()) as client, self.connect(client) as ws:
            ws.receive_json()
            for initially_open in (False, True):
                if initially_open:
                    ws.send_json({'type':'start','mode':'push_to_talk'}); ws.receive_json()
                    ws.send_json({'type':'press'}); ws.receive_json()
                    ws.send_json({'type':'stop'})
                    self.assertFalse(ws.receive_json()['state']['open'])
                for command in ('stop', 'interrupt', 'release') * 2:
                    ws.send_json({'type':command})
                    if command == 'interrupt':
                        self.assertEqual(ws.receive_json(), {'type':'interrupted'})
                    state = ws.receive_json()
                    self.assertEqual(state['type'], 'state')
                    self.assertFalse(state['state']['open'])
                    self.assertFalse(state['state']['pressed'])
                    self.assertFalse(state['state']['responding'])
                    self.assertEqual(state['state']['history'], [])

    def test_start_id_is_optional_validated_and_acknowledged_before_state(self):
        with TestClient(self.app()) as client, self.connect(client) as ws:
            ws.receive_json()
            ws.send_json({'type':'start'})
            self.assertEqual(ws.receive_json()['type'], 'state')
            for start_id in ('restart_ABC-123', 'a' * 64):
                ws.send_json({'type':'start', 'start_id':start_id})
                self.assertEqual(ws.receive_json(), {'type':'session_ready','start_id':start_id})
                self.assertTrue(ws.receive_json()['state']['open'])
            for start_id in ('', 'a'*65, 'with space', 'é', 'line\nfeed', None, 3, []):
                ws.send_json({'type':'start','start_id':start_id})
                error = ws.receive_json()
                self.assertEqual(error['type'], 'error')
                self.assertEqual(error['code'], 'invalid_message')
                self.assertEqual(error['command'], 'start')
            ws.send_json({'type':'stop'})
            self.assertFalse(ws.receive_json()['state']['open'])

    def test_stt_workload_full_has_provider_busy_code(self):
        import asyncio
        import threading
        from voice_app.server import create_app
        class SlowSpeech(Speech):
            stt_available = True
            def __init__(self):
                self.release = threading.Event()
            async def transcribe(self, wav):
                while not self.release.is_set():
                    await asyncio.sleep(.005)
                return 'Fixture.'
        speech = SlowSpeech()
        try:
            with TestClient(create_app(provider=Provider(), speech=speech, local_test_auth=True,
                                       data_dir=self.data.name)) as client:
                with self.connect(client) as first, self.connect(client) as second, self.connect(client) as third:
                    for ws in (first, second, third):
                        ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                    for ws in (first, second):
                        ws.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
                        self.until(ws, 'processing')
                    with self.assertLogs('voice_app.server', level='WARNING') as logs:
                        third.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
                        error = third.receive_json()
                    self.assertEqual(error['code'], 'provider_busy')
                    self.assertEqual(error['command'], 'audio')
                    self.assertEqual(logs.output, ['WARNING:voice_app.server:command=audio errorcode=provider_busy open=True muted=False inflight_jobs=2'])
        finally:
            speech.release.set()

    def test_local_config_and_browser_transcript_rejected_without_fallback(self):
        from voice_app.server import create_app
        from voice_app.speech import Speech as RealSpeech
        speech = RealSpeech(environ={'VOICE_AUDIO_PYTHON':'/missing/python'})
        with TestClient(create_app(provider=Provider(), speech=speech, local_test_auth=True,
                                  data_dir=self.data.name)) as client, self.connect(client) as ws:
            config = client.get('/api/config', headers={'host':'localhost'}).json()
            self.assertEqual(config['stt_mode'], 'local')
            self.assertFalse(config['browser_stt_allowed'])
            self.assertFalse(config['tts_available'])
            self.assertEqual(config['tts_identity']['backend'], 'piper')
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            ws.send_json({'type':'voice_text','text':'Do not accept remote recognition'})
            self.assertEqual(ws.receive_json()['code'], 'browser_stt_disabled')
            ws.send_json({'type':'provider','provider':'default'})
            self.assertEqual(ws.receive_json()['state']['history'], [])

    def test_config_reports_browser_transcripts_separately_from_api_stt(self):
        from voice_app.server import create_app
        for configured in (False, True):
            speech = Speech()
            speech.stt_available = configured
            with TestClient(create_app(provider=Provider(), speech=speech, local_test_auth=True,
                                       data_dir=self.data.name)) as client:
                config = client.get('/api/config', headers={'host':'localhost'}).json()
                self.assertEqual(config['stt_available'], configured)
                self.assertIs(config['browser_stt_allowed'], True)
                self.assertIs(config.get('session_ack_supported'), True)
                self.assertEqual(config['stt_mode'], 'api' if configured else 'browser')

    def test_server_publishes_one_neural_voice_identity_for_all_clients(self):
        from voice_app.server import create_app
        from voice_app.speech import Speech as RealSpeech
        with TestClient(create_app(provider=Provider(), speech=RealSpeech(environ={'VOICE_TTS_BACKEND':'edge'}),
                                  local_test_auth=True, data_dir=self.data.name)) as client:
            config = client.get('/api/config', headers={'host':'localhost'}).json()
            self.assertEqual(config['tts_identity'], {'backend':'edge', 'voice':'id-ID-GadisNeural',
                'rate':'+0%', 'pitch':'+0Hz', 'format':'wav', 'sample_rate':24000,
                'channels':1, 'sample_width':2})
            self.assertFalse(config['client_tts_allowed'])

    def test_authenticated_browser_transcript_requires_open_unmuted_session(self):
        from pathlib import Path
        from starlette.websockets import WebSocketDisconnect
        from voice_app.server import create_app
        app = create_app(provider=Provider(), speech=Speech(), data_dir=self.data.name, public_origin='')
        with TestClient(app, base_url='http://localhost') as client:
            with self.assertRaises(WebSocketDisconnect):
                with self.connect(client):
                    pass
            token = (Path(self.data.name)/'bootstrap-token').read_text().strip()
            credentials = {'username':'fixture-owner','password':'temporary fixture password'}
            self.assertEqual(client.post('/api/setup', headers={'origin':'http://localhost'},
                json={**credentials,'token':token}).status_code, 201)
            self.assertEqual(client.post('/api/login', headers={'origin':'http://localhost'},
                json=credentials).status_code, 200)
            with client.websocket_connect('ws://localhost/ws', headers={'origin':'http://localhost'}) as ws:
                ws.receive_json()
                ws.send_json({'type':'voice_text','text':'Closed session'})
                self.assertEqual(ws.receive_json()['type'], 'error')
                ws.send_json({'type':'start','mode':'hands_free'}); ws.receive_json()
                for bad in ({'type':'voice_text','text':''}, {'type':'voice_text','text':'x'*4001},
                            {'type':'voice_text','text':4}, {'type':'voice_text','text':'Halo','source':'api'}):
                    ws.send_json(bad)
                    self.assertEqual(ws.receive_json()['type'], 'error')
                ws.send_json({'type':'mute','muted':True}); ws.receive_json()
                ws.send_json({'type':'voice_text','text':'Muted session'})
                self.assertEqual(ws.receive_json()['type'], 'error')
                ws.send_json({'type':'mute','muted':False}); ws.receive_json()
                ws.send_json({'type':'voice_text','text':'Transkrip dari browser.'})
                done, events = self.until(ws, 'response_done')
                transcript = next(e for e in events if e['type'] == 'transcript')
                self.assertEqual(transcript['source'], 'browser')
                state, _ = self.until(ws, 'state')
                self.assertEqual(state['state']['history'][0]['text'], 'Transkrip dari browser.')
                self.assertEqual(state['state']['history'][0]['source'], 'voice')
                self.assertEqual(len(state['state']['history']), 2)

    def test_text_session_can_start_microphone_mode_without_losing_history(self):
        with TestClient(self.app()) as client, self.connect(client) as ws:
            ws.receive_json()
            ws.send_json({'type':'provider','provider':'default'})
            self.assertEqual(ws.receive_json()['type'], 'state')
            ws.send_json({'type':'start'}); ws.receive_json()
            ws.send_json({'type':'text','text':'Halo'})
            self.until(ws, 'response_done'); self.until(ws, 'state')
            ws.send_json({'type':'start','mode':'push_to_talk','provider':'default'})
            state = ws.receive_json()
            self.assertEqual(state['type'], 'state')
            self.assertEqual(state['state']['mode'], 'push_to_talk')
            self.assertEqual(len(state['state']['history']), 2)

    def test_completed_history_is_bounded(self):
        with TestClient(self.app()) as client, self.connect(client) as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            for index in range(24):
                ws.send_json({'type':'text','text':'Pesan ' + str(index)})
                self.until(ws, 'response_done')
                final, _ = self.until(ws, 'state')
            self.assertEqual(len(final['state']['history']), 40)

    def test_cancelled_connections_do_not_release_running_remote_work(self):
        import asyncio
        import threading
        class SlowProvider(Provider):
            def __init__(self):
                self.release = threading.Event()
            async def stream(self, messages):
                while not self.release.is_set():
                    await asyncio.sleep(.005)
                yield 'Selesai.'
        provider = SlowProvider()
        try:
            with TestClient(self.app(provider=provider)) as client:
                with self.connect(client) as ws:
                    ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                    for text in ('Satu', 'Dua'):
                        ws.send_json({'type':'text', 'text':text})
                        self.until(ws, 'response_start')
                with self.connect(client) as ws:
                    ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                    ws.send_json({'type':'text','text':'Tiga'})
                    self.assertEqual(ws.receive_json()['type'], 'error')
                    provider.release.set()
        finally:
            provider.release.set()

    def test_configured_audio_validation_and_successful_voice_turn(self):
        class RemoteSpeech(Speech):
            stt_available = True
            stt_reason = ''
            async def transcribe(self, wav):
                with wave.open(io.BytesIO(wav)) as reader:
                    assert reader.getframerate() == 16000
                return 'Transkrip fixture.'
        from voice_app.server import create_app
        with TestClient(create_app(provider=Provider(), speech=RemoteSpeech(), local_test_auth=True, data_dir=self.data.name)) as client, self.connect(client) as ws:
            ws.send_text('{')
            self.assertEqual(ws.receive_json()['type'], 'state')
            self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_json({'type':'start','mode':'push_to_talk'}); ws.receive_json()
            ws.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
            self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_json({'type':'press'}); ws.receive_json()
            for pcm, rate in (('%%%=',16000), ('AAAA',16000), ('AAAAAA==',48000), ('A'*853340,16000)):
                ws.send_json({'type':'audio','pcm_b64':pcm,'sample_rate':rate})
                self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
            ws.send_json({'type':'release'})
            done, events = self.until(ws, 'response_done')
            self.assertTrue(any(e['type'] == 'transcript' for e in events))
            state, _ = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][0]['source'], 'voice')

    def test_audio_transcription_is_background_and_interrupt_suppresses_it(self):
        import asyncio
        import threading
        class RemoteSpeech(Speech):
            stt_available = True
            stt_reason = ''
            def __init__(self):
                self.ready = threading.Event()
                self.release = threading.Event()
            async def transcribe(self, wav):
                self.ready.set()
                while not self.release.is_set():
                    await asyncio.sleep(.005)
                return 'Suara menjadi teks.'
        speech = RemoteSpeech()
        from voice_app.server import create_app
        with TestClient(create_app(provider=Provider(), speech=speech, local_test_auth=True, data_dir=self.data.name)) as client, self.connect(client) as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            ws.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
            event = ws.receive_json()
            self.assertNotEqual(event['type'], 'error')
            self.assertTrue(speech.ready.wait(2))
            ws.send_json({'type':'interrupt'})
            self.until(ws, 'interrupted'); self.until(ws, 'state')
            speech.release.set()
            ws.send_json({'type':'text','text':'Teks baru'})
            done, events = self.until(ws, 'response_done')
            self.assertFalse(any(e['type'] == 'transcript' for e in events))

    def test_interrupt_supersession_stale_ack_and_workload_bound(self):
        import asyncio
        import threading
        class SlowProvider(Provider):
            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()
                self.calls = 0
            async def stream(self, messages):
                self.calls += 1
                self.entered.set()
                while not self.release.is_set():
                    await asyncio.sleep(.005)
                yield 'Jawaban baru.'
        provider = SlowProvider()
        with TestClient(self.app(provider=provider)) as client, self.connect(client) as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            ws.send_json({'type':'text','text':'Pertama'})
            old, _ = self.until(ws, 'response_start')
            self.assertTrue(provider.entered.wait(2))
            ws.send_json({'type':'interrupt'})
            self.until(ws, 'interrupted'); self.until(ws, 'state')
            ws.send_json({'type':'text','text':'Kedua'})
            new, _ = self.until(ws, 'response_start')
            with self.assertLogs('voice_app.server', level='WARNING') as logs:
                ws.send_json({'type':'text','text':'Ketiga'})
                timer = threading.Timer(.2, provider.release.set); timer.start()
                event, _ = self.until_allow_error(ws)
            self.assertEqual(event, {'type':'error','code':'provider_busy','command':'text',
                'message':'Provider sedang sibuk. Tunggu respons selesai lalu coba lagi.'})
            self.assertEqual(logs.output, ['WARNING:voice_app.server:command=text errorcode=provider_busy open=True muted=False inflight_jobs=2'])
            provider.release.set()
            done, events = self.until(ws, 'response_done')
            self.assertEqual(done['token'], new['token'])
            self.assertTrue(all(e.get('token') != old['token'] for e in events))
            self.until(ws, 'state')
            ws.send_json({'type':'ack','token':old['token'],'chunk_id':1})
            state, _ = self.until(ws, 'state')
            self.assertEqual(state['state']['history'][-1]['heard_text'], '')
            self.assertLessEqual(provider.calls, 2)

    def until_allow_error(self, ws):
        events=[]
        for _ in range(30):
            event=ws.receive_json(); events.append(event)
            if event['type'] in ('error', 'response_done'):
                return event, events
        self.fail('missing error')

    def test_validation_errors_keep_socket_usable_and_stt_is_explicit(self):
        with TestClient(self.app()) as client, self.connect(client) as ws:
            ws.receive_json()
            for bad in ([], {}, {'type':'text','text':3}, {'type':'text','text':'x'*4001},
                        {'type':'mute','muted':'false'}, {'type':'start','provider':'unknown'},
                        {'type':'audio','pcm_b64':'!'*900000,'sample_rate':16000}):
                ws.send_json(bad)
                self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_text('['*2000 + '0' + ']'*2000)
            self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_text('{broken')
            self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_json({'type':'start','mode':'push_to_talk'})
            self.assertTrue(ws.receive_json()['state']['open'])
            for kind in ('press', 'release'):
                ws.send_json({'type':kind})
                self.assertEqual(ws.receive_json()['state']['pressed'], kind == 'press')
            ws.send_json({'type':'audio','pcm_b64':'AAAAAA==','sample_rate':16000})
            err = ws.receive_json()
            self.assertEqual(err['type'], 'error')
            self.assertIn('STT', err['message'])
            ws.send_json({'type':'mute','muted':True})
            self.assertTrue(ws.receive_json()['state']['muted'])
            ws.send_json({'type':'provider','provider':'default'})
            self.assertEqual(ws.receive_json()['type'], 'state')
            ws.send_json({'type':'stop'})
            self.assertFalse(ws.receive_json()['state']['open'])

    def test_rejects_rebinding_and_mismatched_origins(self):
        from starlette.websockets import WebSocketDisconnect
        with TestClient(self.app()) as client:
            self.assertEqual(client.get('/health', headers={'host':'evil.example'}).status_code, 400)
            for headers in ({'host':'localhost','origin':'https://evil.example'},
                            {'host':'evil.example','origin':'http://evil.example'},
                            {'host':'localhost','origin':'http://localhost:9999'},
                            {'host':'localhost'}):
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect('/ws', headers=headers):
                        pass

    def test_text_session_emits_wav_before_done_and_ack_updates_history(self):
        with TestClient(self.app()) as client:
            self.assertEqual(client.get('/health', headers={'host':'localhost'}).status_code, 200)
            config = client.get('/api/config', headers={'host':'localhost'}).json()
            self.assertFalse(config['stt_available'])
            with self.connect(client) as ws:
                ws.receive_json()
                ws.send_json({'type':'start', 'mode':'hands_free', 'provider':'default'})
                self.until(ws, 'state')
                ws.send_json({'type':'text', 'text':'Halo'})
                done, events = self.until(ws, 'response_done')
                audio = next(e for e in events if e['type'] == 'audio')
                with wave.open(io.BytesIO(base64.b64decode(audio['wav_b64']))) as wav:
                    self.assertGreater(wav.getnframes(), 0)
                self.until(ws, 'state')
                ws.send_json({'type':'ack', 'token':done['token'], 'chunk_id':audio['chunk_id']})
                state, _ = self.until(ws, 'state')
                self.assertEqual(state['state']['history'][-1]['heard_text'], 'Halo dari pengujian.')
