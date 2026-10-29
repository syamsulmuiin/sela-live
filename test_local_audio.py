"""Offline adapter tests; model inference is opt-in, never simulated as real."""
import asyncio
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voice_app.speech import Speech


class LocalDefaultsTests(unittest.TestCase):
    def test_local_defaults_fail_closed_with_missing_runtime(self):
        speech = Speech(environ={'VOICE_AUDIO_PYTHON':'/missing/audio/python'})
        self.assertEqual(speech.stt_mode, 'local')
        self.assertFalse(speech.stt_available)
        self.assertFalse(speech.browser_stt_allowed)
        self.assertEqual(speech.tts_identity['backend'], 'piper')
        self.assertEqual(speech.tts_identity['voice'], 'id_ID-news_tts-medium')
        self.assertIn('lokal', speech.stt_reason)
        self.assertNotIn('browser', speech.stt_reason)


class PronunciationTests(unittest.TestCase):
    def test_conservative_indonesian_currency_dates_numbers_lexicon(self):
        from voice_app.speech import normalize_pronunciation
        cases = {
            'Rp25.000': 'dua puluh lima ribu rupiah',
            'Rp 1.250,50': 'seribu dua ratus lima puluh rupiah koma lima nol',
            'API Codex punya 21 alat.': 'a pe i kodeks punya dua puluh satu alat.',
            '2026-10-25': 'dua puluh lima Oktober dua ribu dua puluh enam',
            '2026-02-31': '2026-02-31',
            'v1.2.3 /api API_KEY 0012 127.0.0.1': 'v1.2.3 /api API_KEY 0012 127.0.0.1',
            'Harga Rp25.00 atau Rp25.000.000.000': 'Harga Rp25.00 atau Rp25.000.000.000',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(normalize_pronunciation(text), expected)


class LocalAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_waits_for_previous_local_worker_cleanup_instead_of_immediate_busy(self):
        from voice_app.speech import Speech
        entered=asyncio.Event(); release=asyncio.Event(); calls=[]
        async def process(*args, **kwargs):
            calls.append(args)
            if len(calls)==1:
                entered.set(); await release.wait()
            return b'fixture-result'
        with patch('voice_app.speech._runtime_ready',return_value=True), patch('voice_app.speech._bounded_process',process):
            speech=Speech(environ={})
            first=asyncio.create_task(speech._local_request('stt',b'a',100))
            await entered.wait()
            second=asyncio.create_task(speech._local_request('stt',b'b',100))
            await asyncio.sleep(.01)
            self.assertFalse(second.done(),'one retry may wait briefly for cleanup')
            release.set()
            self.assertEqual(await first,b'fixture-result')
            self.assertEqual(await second,b'fixture-result')
            self.assertEqual(len(calls),2)

    async def test_local_wait_is_bounded_and_cancellation_does_not_release_running_worker(self):
        from voice_app.speech import LocalAudioBusy
        entered=asyncio.Event(); release=asyncio.Event()
        async def process(*args,**kwargs):
            entered.set(); await release.wait(); return b'ok'
        with patch('voice_app.speech._runtime_ready',return_value=True), patch('voice_app.speech._bounded_process',process):
            speech=Speech(environ={})
            first=asyncio.create_task(speech._local_request('stt',b'a',100)); await entered.wait()
            second=asyncio.create_task(speech._local_request('stt',b'b',100)); await asyncio.sleep(.01)
            with self.assertRaises(LocalAudioBusy):
                await speech._local_request('stt',b'c',100)
            second.cancel()
            with self.assertRaises(asyncio.CancelledError):await second
            self.assertTrue(speech._local_slot.locked())
            with patch('voice_app.speech.LOCAL_RETRY_WAIT',.01):
                with self.assertRaises(LocalAudioBusy):await speech._local_request('stt',b'd',100)
            self.assertTrue(speech._local_slot.locked())
            release.set();await first
            self.assertFalse(speech._local_slot.locked())
            self.assertFalse(speech._local_waiting)

    async def test_missing_models_never_spawns_or_falls_back(self):
        speech = Speech(environ={'VOICE_AUDIO_PYTHON':'/missing/python'})
        with patch('voice_app.speech.asyncio.create_subprocess_exec') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'lokal'):
                await speech.synthesize('Halo.')
            with self.assertRaises(ValueError):
                await speech.transcribe(b'RIFF')
            spawn.assert_not_called()

    async def test_local_subprocess_protocol_normalization_offline_and_wav_validation(self):
        from voice_app.audio import pcm16_wav
        wav = pcm16_wav(b'\0\0'*160, 16000)
        output = io.BytesIO()
        with wave.open(output, 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(22050)
            out.writeframes(b'\0\0'*2205)
        original = asyncio.create_subprocess_exec
        commands = []
        async def spawn(*args, **kwargs):
            commands.append((args, kwargs['env']))
            code = ('import sys,json; data=json.load(sys.stdin); assert data["text"] == "dua puluh lima ribu rupiah"; '
                    'sys.stdout.buffer.write(' + repr(output.getvalue()) + ')') if args[-2] == 'tts' else (
                    'import sys; data=sys.stdin.buffer.read(); assert data[:4] == b"RIFF"; print(\'{"text":"Halo lokal."}\')')
            return await original(sys.executable, '-c', code, **kwargs)
        with patch('voice_app.speech._runtime_ready', return_value=True), patch('voice_app.speech.asyncio.create_subprocess_exec', spawn):
            speech = Speech(environ={'VOICE_AUDIO_PYTHON': '/root/voice-lab/audio-runtime/venv/bin/python'})
            self.assertEqual(await speech.transcribe(wav), 'Halo lokal.')
            raw = await speech.synthesize('Rp25.000')
            with wave.open(io.BytesIO(raw)) as reader:
                self.assertEqual(reader.getframerate(), 22050)
            for invalid in (b'RIFF', wav[:-2], pcm16_wav(b'\0\0'*160, 16000).replace((16000).to_bytes(4,'little'), (8000).to_bytes(4,'little'), 1)):
                with self.assertRaises(ValueError):
                    await speech.transcribe(invalid)
        self.assertEqual(len(commands), 2)
        for args, env in commands:
            self.assertEqual(args[0], '/root/voice-lab/audio-runtime/venv/bin/python')
            self.assertIn('-I', args)
            self.assertEqual(env['HF_HUB_OFFLINE'], '1')
            self.assertNotIn('VOICE_STT_API_KEY', env)


class WorkerTests(unittest.TestCase):
    def test_ctranslate_model_does_not_require_optional_preprocessor_file(self):
        from voice_app.local_audio_worker import validate_model
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('model.bin','config.json','tokenizer.json'):
                (root/name).write_text('{}')
            self.assertEqual(validate_model(root,'stt'),root)

    def test_worker_stt_enforces_offline_cpu_and_silence_filter(self):
        import types
        from voice_app.audio import pcm16_wav
        from voice_app import local_audio_worker as worker
        calls = {}
        class Model:
            def __init__(self, path, **kwargs):
                calls['load'] = (path, kwargs)
            def transcribe(self, audio, **kwargs):
                calls['transcribe'] = kwargs
                return iter([types.SimpleNamespace(text='Halo manusia.', no_speech_prob=.1, avg_logprob=-.1),
                             types.SimpleNamespace(text='Terima kasih menonton.', no_speech_prob=.99, avg_logprob=-2)]), None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('model.bin', 'config.json', 'tokenizer.json', 'preprocessor_config.json'):
                (root/name).write_text('{}')
            with patch.dict(sys.modules, {'faster_whisper':types.SimpleNamespace(WhisperModel=Model)}):
                text = worker.transcribe(pcm16_wav(b'\x01\x01'*1600, 16000), root)
        self.assertEqual(text, {'text':'Halo manusia.'})
        self.assertEqual(calls['load'][1], {'device':'cpu', 'compute_type':'int8', 'cpu_threads':2, 'num_workers':1, 'local_files_only':True})
        self.assertEqual(calls['transcribe']['language'], 'id')
        self.assertEqual(calls['transcribe']['beam_size'], 5)
        self.assertTrue(calls['transcribe']['vad_filter'])
        self.assertFalse(calls['transcribe']['condition_on_previous_text'])
        with self.assertRaises(ValueError):
            worker.transcribe(b'RIFF', Path('/missing'))

    def test_worker_preserves_upstream_accepted_speech_without_accepting_low_confidence_silence(self):
        import types
        from voice_app.audio import pcm16_wav
        from voice_app import local_audio_worker as worker
        cases=[(.95,-.2,True),(.1,-1.2,True),(.6,-1.0,True),
               (.60001,-1.0,False),(.95,-1.01,False),(.95,-.999,True)]
        class Model:
            def __init__(self,*args,**kwargs):pass
            def transcribe(self,*args,**kwargs):
                return iter([types.SimpleNamespace(text='S'+str(i),no_speech_prob=no_speech,avg_logprob=logprob)
                             for i,(no_speech,logprob,keep) in enumerate(cases)]),None
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('model.bin','config.json','tokenizer.json'):(root/name).write_text('{}')
            with patch.dict(sys.modules,{'faster_whisper':types.SimpleNamespace(WhisperModel=Model)}):
                result=worker.transcribe(pcm16_wav(b'\x01\x01'*1600,16000),root)
        self.assertEqual(result,{'text':'S0 S1 S2 S5'})

    def test_worker_piper_fixed_cpu_session_and_bounded_wav(self):
        import types
        from voice_app import local_audio_worker as worker
        calls = {}
        class Options:
            pass
        class Voice:
            def __init__(self, **kwargs):
                calls['voice'] = kwargs
            def synthesize_wav(self, text, output):
                self.assert_text = text
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(22050)
                output.writeframes(b'\0\0'*2205)
        def session(path, **kwargs):
            calls['session'] = kwargs
            return object()
        modules = {'piper':types.SimpleNamespace(PiperVoice=Voice),
                   'piper.config':types.SimpleNamespace(PiperConfig=types.SimpleNamespace(from_dict=lambda x:x)),
                   'onnxruntime':types.SimpleNamespace(SessionOptions=Options, InferenceSession=session)}
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp)/'id_ID-news_tts-medium.onnx'; model.write_bytes(b'model')
            Path(str(model)+'.json').write_text('{"audio":{"sample_rate":22050},"espeak":{"voice":"id"}}')
            with patch.dict(sys.modules, modules):
                raw = worker.synthesize(b'{"text":"Halo."}', model)
                with wave.open(io.BytesIO(raw)) as reader:
                    self.assertEqual(reader.getframerate(), 22050)
                with patch.object(worker, 'MAX_OUTPUT_WAV', 100):
                    with self.assertRaises(ValueError):
                        worker.synthesize(b'{"text":"Halo."}', model)
                with self.assertRaises(ValueError):
                    worker.synthesize(b'{"text":""}', model)
        options = calls['session']['sess_options']
        self.assertEqual(options.intra_op_num_threads, 2)
        self.assertEqual(options.inter_op_num_threads, 1)
        self.assertEqual(calls['session']['providers'], ['CPUExecutionProvider'])

    def test_worker_cli_missing_models_is_sanitized_and_never_downloads(self):
        import subprocess
        from voice_app.speech import LOCAL_WORKER, _offline_env
        for action in ('stt', 'tts', 'check-stt', 'check-tts'):
            result = subprocess.run([sys.executable, '-I', str(LOCAL_WORKER), action, '/missing/private-model'],
                input=b'PRIVATE USER INPUT', capture_output=True, env=_offline_env())
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')
            self.assertNotIn(b'PRIVATE', result.stderr)
            self.assertNotIn(b'Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
