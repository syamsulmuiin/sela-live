"""Offline Supertonic3 adapter contract: no SDK imports or model allocation."""
import asyncio
import io
import json
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from voice_app.speech import Speech


@contextmanager
def configured(**overrides):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assets = root / 'assets'; assets.mkdir()
        manifest = root / 'manifest.json'; manifest.write_text('{}')
        env = {'VOICE_TTS_BACKEND':'supertonic3', 'VOICE_STT_BACKEND':'browser',
               'VOICE_SUPERTONIC3_PYTHON':sys.executable,
               'VOICE_SUPERTONIC3_MODEL_PATH':str(assets),
               'VOICE_SUPERTONIC3_MANIFEST':str(manifest)} | overrides
        yield env


def fixture_wav(rate=44100, channels=1, width=2):
    out = io.BytesIO()
    with wave.open(out, 'wb') as writer:
        writer.setnchannels(channels); writer.setsampwidth(width); writer.setframerate(rate)
        writer.writeframes(b'\x01' * (100 * channels * width))
    return out.getvalue()


class SynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_process_protocol_and_normalized_copy_preserve_history_ack(self):
        from voice_app.session_core import Session
        original_spawn = asyncio.create_subprocess_exec
        commands = []
        output = fixture_wav()
        async def spawn(*args, **kwargs):
            commands.append((args, kwargs['env']))
            code = ('import sys,json; request=json.load(sys.stdin); '
                    'assert request == {"text":"a pe i dua puluh satu <laugh>"}; '
                    'sys.stdout.buffer.write(' + repr(output) + ')')
            return await original_spawn(sys.executable, '-I', '-c', code, **kwargs)
        with configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
            session = Session(); session.start(); token = session.submit('question')
            text = 'API 21 <laugh>'
            session.response_text(token, text)
            with patch('voice_app.speech.asyncio.create_subprocess_exec', spawn):
                audio = await speech.synthesize(text)
            self.assertTrue(session.response_audio(token, audio, text))
            chunk = session.drain_audio(token)
            self.assertTrue(session.ack_played(token, chunk.chunk_id))
            self.assertEqual(session.history[-1].text, text)
            self.assertEqual(session.history[-1].heard_text, text)
            self.assertEqual(commands[0][0], (env['VOICE_SUPERTONIC3_PYTHON'], '-I',
                str(Path(__file__).resolve().parents[2] / 'voice_app/supertonic3_worker.py'), 'tts',
                env['VOICE_SUPERTONIC3_MODEL_PATH'], env['VOICE_SUPERTONIC3_MANIFEST']))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][1]['HF_HUB_OFFLINE'], '1')
        self.assertNotIn('VOICE_STT_API_KEY', commands[0][1])
        with wave.open(io.BytesIO(audio)) as reader:
            self.assertEqual((reader.getframerate(), reader.getnchannels(), reader.getsampwidth()), (44100, 1, 2))

    async def test_request_validation_before_spawning_and_after_normalization(self):
        with configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
        invalid = (None, '', ' ', 'x'*801, 'x\x00', '<angry>Halo', '<Laugh>', '</laugh>', '<laugh',
                   '<laugh>'*4, '<laugh volume="1">', 'x\ud800', '999999999 '*80)
        with patch('voice_app.speech._bounded_process') as process:
            for text in invalid:
                with self.subTest(text=repr(text)[:50]):
                    with self.assertRaises(ValueError): await speech.synthesize(text)
            process.assert_not_called()

    async def test_accepts_only_documented_tags_no_automatic_insertion(self):
        with configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
        with patch('voice_app.speech._bounded_process', return_value=fixture_wav()) as process:
            for text in ('Halo', 'Halo <laugh><breath><sigh>', '🙂'*800):
                await speech.synthesize(text)
                self.assertEqual(json.loads(process.call_args.args[1]), {'text':text})

    async def test_wrong_audio_format_and_worker_failure_do_not_fallback(self):
        with configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            speech = Speech(environ=env)
        for raw in (fixture_wav(22050), fixture_wav(channels=2), fixture_wav(width=1), b'bad'):
            with self.subTest(raw=raw[:30]), patch('voice_app.speech._bounded_process', return_value=raw) as process:
                with self.assertRaises((ValueError, wave.Error, EOFError)): await speech.synthesize('Halo')
                self.assertEqual(process.call_count, 1)
        with patch('voice_app.speech._bounded_process', side_effect=RuntimeError('worker failed')) as process:
            with self.assertRaisesRegex(RuntimeError, 'worker failed'): await speech.synthesize('Halo')
            self.assertEqual(process.call_count, 1)


class CoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def test_zipformer_quiescence_retained_until_real_child_reaped(self):
        original_spawn = asyncio.create_subprocess_exec
        for outcome in ('success', 'failure', 'cancel', 'limit'):
            with self.subTest(outcome=outcome):
                events = []; children = []; ready = asyncio.Event()
                with configured(VOICE_STT_BACKEND='local', VOICE_LOCAL_PREVIEW='1',
                                VOICE_ZIPFORMER_STATEFUL='1',
                                VOICE_LOCAL_ASR_ENGINE='zipformer_id_int8',
                                VOICE_ZIPFORMER_PYTHON='/isolated/asr/python',
                                VOICE_ZIPFORMER_MODEL_PATH='/isolated/asr/model') as env, \
                     patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)), \
                     patch('voice_app.speech._runtime_ready', return_value=True):
                    speech = Speech(environ=env)
                @asynccontextmanager
                async def quiesce(timeout):
                    self.assertTrue(speech._local_slot.locked())
                    events.append('quiesce')
                    try:
                        yield
                    finally:
                        self.assertIsNotNone(children[0].returncode, 'reap before ASR resumes')
                        self.assertTrue(speech._local_slot.locked(), 'slot retained during ASR resume')
                        events.append('resume')
                speech._stream_asr = type('ASR', (), {'quiesce':staticmethod(quiesce)})()
                async def spawn(*args, **kwargs):
                    self.assertEqual(events, ['quiesce'])
                    self.assertTrue(speech._local_slot.locked())
                    self.assertEqual(args[-3], 'tts')
                    events.append('spawn')
                    if outcome == 'success': code = 'import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(' + repr(fixture_wav()) + ')'
                    elif outcome == 'failure': code = 'raise SystemExit(2)'
                    elif outcome == 'limit': code = 'import sys,time; sys.stdin.buffer.read(); sys.stdout.buffer.write(b"x"*4096); sys.stdout.flush(); time.sleep(30)'
                    else: code = 'import time; time.sleep(30)'
                    child = await original_spawn(sys.executable, '-I', '-c', code, **kwargs)
                    children.append(child); ready.set(); return child
                with patch('voice_app.speech.asyncio.create_subprocess_exec', spawn), \
                     patch('voice_app.speech.MAX_WAV_BYTES', 1000 if outcome == 'limit' else 4000000):
                    task = asyncio.create_task(speech.synthesize('Halo'))
                    await asyncio.wait_for(ready.wait(), 2)
                    if outcome == 'cancel': task.cancel()
                    if outcome == 'success': await task
                    else:
                        expected = {'failure':RuntimeError, 'cancel':asyncio.CancelledError, 'limit':ValueError}[outcome]
                        with self.assertRaises(expected): await task
                self.assertEqual(events, ['quiesce', 'spawn', 'resume'])
                self.assertFalse(speech._local_slot.locked())
                self.assertFalse(speech._local_waiting)
                self.assertIsNotNone(children[0].returncode)

    async def test_whisper_closed_before_tts_and_stt_command_unchanged(self):
        events = []
        with configured(VOICE_STT_BACKEND='local', VOICE_LOCAL_PREVIEW='1') as env, \
             patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)), \
             patch('voice_app.speech._runtime_ready', return_value=True):
            speech = Speech(environ=env)
        async def close(): events.append('close')
        speech._stream_asr = type('ASR', (), {'close':staticmethod(close)})()
        async def process(args, payload, limit, **kwargs):
            events.append('tts'); self.assertEqual(events, ['close', 'tts']); return fixture_wav()
        with patch('voice_app.speech._bounded_process', process): await speech.synthesize('Halo')
        speech.local_preview = False
        from voice_app.speech import LOCAL_WORKER
        with patch('voice_app.speech._bounded_process', return_value=b'{"text":"Halo"}') as process:
            await speech._local_request('stt', b'fixture', 65536)
            self.assertEqual(process.call_args.args[0], (speech._python, '-I', str(LOCAL_WORKER), 'stt', str(speech._stt_path)))


class ConfigurationTests(unittest.TestCase):
    def test_default_remains_piper_without_supertonic_probe(self):
        with patch('voice_app.speech._runtime_ready', return_value=False), patch('voice_app.speech.subprocess.run') as check:
            speech = Speech(environ={})
        self.assertEqual(speech.tts_identity['backend'], 'piper')
        self.assertFalse(speech.tts_available)
        check.assert_not_called()

    def test_explicit_identity_and_bounded_offline_check(self):
        with configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as check:
            speech = Speech(environ=env)
        self.assertEqual(speech.tts_identity, {'backend':'supertonic3', 'voice':'M1', 'language':'id',
            'rate':'default', 'pitch':'default', 'format':'wav', 'sample_rate':44100, 'channels':1, 'sample_width':2})
        self.assertTrue(speech.tts_available)
        self.assertIn('M1', speech.tts_label)
        args, kwargs = check.call_args
        self.assertEqual(args[0], (env['VOICE_SUPERTONIC3_PYTHON'], '-I',
            str(Path(__file__).resolve().parents[2] / 'voice_app/supertonic3_worker.py'), 'check',
            env['VOICE_SUPERTONIC3_MODEL_PATH'], env['VOICE_SUPERTONIC3_MANIFEST']))
        self.assertEqual(kwargs['timeout'], 15)
        for key in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(kwargs[key], subprocess.DEVNULL)
        self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'], '1')
        self.assertNotIn('VOICE_STT_API_KEY', kwargs['env'])

    def test_missing_relative_wrong_kind_paths_fail_before_probe(self):
        keys = ('VOICE_SUPERTONIC3_PYTHON', 'VOICE_SUPERTONIC3_MODEL_PATH', 'VOICE_SUPERTONIC3_MANIFEST')
        with configured() as base:
            for key in keys:
                for bad in (None, '', 'relative', '/missing/supertonic-runtime'):
                    env = dict(base)
                    if bad is None: env.pop(key)
                    else: env[key] = bad
                    with self.subTest(key=key, bad=bad), patch('voice_app.speech.subprocess.run') as check:
                        with self.assertRaisesRegex(ValueError, 'Supertonic3'):
                            Speech(environ=env)
                        check.assert_not_called()
            for key, value in ((keys[0], base[keys[1]]), (keys[1], base[keys[2]]), (keys[2], base[keys[1]])):
                with self.subTest(key=key), patch('voice_app.speech.subprocess.run') as check:
                    with self.assertRaisesRegex(ValueError, 'Supertonic3'): Speech(environ=base | {key:value})
                    check.assert_not_called()

    def test_unavailable_probe_fails_explicitly_no_fallback(self):
        with configured() as env:
            for outcome in (subprocess.CompletedProcess([], 1), OSError('unavailable'), subprocess.TimeoutExpired('check', 15)):
                kwargs = {'side_effect':outcome} if isinstance(outcome, Exception) else {'return_value':outcome}
                with self.subTest(outcome=outcome), patch('voice_app.speech.subprocess.run', **kwargs) as check:
                    with self.assertRaisesRegex(ValueError, 'Supertonic3.*no fallback'): Speech(environ=env)
                    self.assertEqual(check.call_count, 1)

    def test_paths_require_explicit_backend_selection(self):
        for key in ('VOICE_SUPERTONIC3_PYTHON', 'VOICE_SUPERTONIC3_MODEL_PATH', 'VOICE_SUPERTONIC3_MANIFEST'):
            with self.subTest(key=key), patch('voice_app.speech._runtime_ready', return_value=False):
                with self.assertRaisesRegex(ValueError, 'Supertonic3'): Speech(environ={key:'/explicit/path'})
