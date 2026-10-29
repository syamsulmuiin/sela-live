import asyncio,io,json,os,struct,subprocess,unittest,wave
from pathlib import Path
from unittest.mock import patch
from voice_app.speech import Speech,_offline_env
# Shared pinned test assets are app-owned and independent of test installation.
ROOT=Path('/root/voice-lab/autonomous/zipformer')
PY=str(ROOT/'venv/bin/python')
WORKER=Path(__file__).resolve().parent/'voice_app/zipformer_worker.py'
ENV={'VOICE_STT_BACKEND':'local','VOICE_TTS_BACKEND':'piper','VOICE_LOCAL_PREVIEW':'1',
     'VOICE_LOCAL_ASR_ENGINE':'zipformer_id_int8','VOICE_ZIPFORMER_PYTHON':PY,
     'VOICE_ZIPFORMER_MODEL_PATH':str(ROOT/'model'),
     'VOICE_AUDIO_PYTHON':'/root/voice-lab/audio-runtime/venv/bin/python',
     'VOICE_WHISPER_MODEL_PATH':'/root/voice-lab/audio-runtime/models/whisper-small',
     'VOICE_PIPER_MODEL_PATH':'/root/voice-lab/audio-runtime/models/piper-id/id_ID-news_tts-medium.onnx'}
class ZipformerSelection(unittest.TestCase):
    def test_rejects_invalid_or_unavailable_explicit_selection(self):
        for changes in (
            {'VOICE_LOCAL_ASR_ENGINE':'unknown'},
            {'VOICE_LOCAL_PREVIEW':'0'},
            {'VOICE_STT_BACKEND':'api','VOICE_LOCAL_PREVIEW':'0'},
            {'VOICE_ZIPFORMER_PYTHON':'relative/python'},
            {'VOICE_ZIPFORMER_MODEL_PATH':'relative/model'},
            {'VOICE_ZIPFORMER_MODEL_PATH':'/missing/model'},
            {'VOICE_ZIPFORMER_PYTHON':'/missing/python'},
            {'VOICE_DRAFT_MODEL_PATH':'/another/draft'},
        ):
            with self.subTest(changes=changes),self.assertRaises(ValueError):Speech(ENV|changes)
        with self.assertRaises(ValueError):
            Speech(ENV|{'VOICE_LOCAL_ASR_ENGINE':'whisper'})

    def test_explicit_engine_routes_both_without_changing_piper(self):
        speech=Speech(ENV)
        self.assertEqual(getattr(speech,'asr_engine',None),'zipformer_id_int8')
        worker=speech._preview_worker()
        self.assertEqual(worker.args,worker.final_args)
        self.assertIn(str(WORKER),worker.args)
        self.assertEqual(worker.args[0],PY)
        self.assertEqual(speech._python,ENV['VOICE_AUDIO_PYTHON'])
        self.assertEqual(speech.tts_identity['voice'],'id_ID-news_tts-medium')
        self.assertEqual(speech.preview_source,'local_zipformer_id_prefix')
        self.assertEqual(speech.preview_semantics,'repeated_bounded_zipformer_online_prefix_decode')
class ZipformerWorker(unittest.TestCase):
    def test_real_binary_worker_check_and_serial_frames(self):
        self.assertTrue(WORKER.is_file(), 'independent worker missing')
        result=subprocess.run((PY,'-I',str(WORKER),'check',str(ROOT/'model')),capture_output=True,env=_offline_env(),timeout=20)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout,b'')
        from voice_app.audio import pcm16_wav
        payload=pcm16_wav(b'\0\0'*16000,16000)
        frame=struct.pack('!I',len(payload))+payload
        result=subprocess.run((PY,'-I',str(WORKER),'serve',str(ROOT/'model')),input=frame*2,capture_output=True,env=_offline_env(),timeout=30)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stderr,b'')
        data=result.stdout;self.assertTrue(data.startswith(b'READY\n'));data=data[6:]
        for _ in range(2):
            size=struct.unpack('!I',data[:4])[0];body=json.loads(data[4:4+size]);data=data[4+size:]
            self.assertEqual(body['text'],'')
            self.assertTrue(body['metrics']['reset_empty'])
            self.assertGreater(body['metrics']['cpu_seconds'],0)
        self.assertEqual(data,b'')

class ZipformerProtocolLabel(unittest.TestCase):
    def test_config_and_partial_report_the_selected_engine_honestly(self):
        import tempfile
        from fastapi.testclient import TestClient
        from voice_app.server import create_app
        from test_preview_protocol import DraftSpeech,CountProvider,PreviewProtocolTests
        speech=DraftSpeech()
        speech.preview_source='local_zipformer_id_prefix'
        speech.preview_semantics='repeated_bounded_zipformer_online_prefix_decode'
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(provider=CountProvider(),speech=speech,data_dir=directory,local_test_auth=True,public_origin='')
            with TestClient(app) as client:
                cfg=client.get('/api/config',headers={'host':'localhost'}).json()
                self.assertEqual(cfg['local_preview_semantics'],speech.preview_semantics)
                with client.websocket_connect('/ws',headers={'host':'localhost','origin':'http://localhost'}) as ws:
                    ws.receive_json();ws.send_json({'type':'start'});PreviewProtocolTests.until(self,ws,'state')
                    ws.send_json(PreviewProtocolTests.preview(self))
                    event=PreviewProtocolTests.until(self,ws,'transcript_partial')
                    self.assertEqual(event['source'],speech.preview_source)

if __name__=='__main__':unittest.main()
