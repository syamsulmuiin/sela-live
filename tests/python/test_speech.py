import asyncio
import io
import unittest
import os
import wave
from pathlib import Path

@unittest.skipUnless(os.environ.get('VOICE_EDGE_INTEGRATION') == '1', 'opt-in real online Edge neural synthesis')
class EdgeLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_online_edge_synthesis_is_valid_pcm16_wav(self):
        from voice_app.speech import Speech
        speech = Speech(environ={'VOICE_TTS_BACKEND':'edge', 'VOICE_STT_BACKEND':'browser'})
        wav = await speech.synthesize('Halo, ini suara neural bahasa Indonesia. Pengujian koneksi berhasil.')
        with wave.open(io.BytesIO(wav)) as reader:
            self.assertEqual(reader.getframerate(), 24000)
            self.assertEqual(reader.getnchannels(), 1)
            self.assertEqual(reader.getsampwidth(), 2)
            self.assertGreater(reader.getnframes(), 24000)
            self.assertEqual(len(reader.readframes(reader.getnframes())), len(wav)-44)
        root = Path(__file__).resolve().parents[2] / 'verification'
        root.mkdir(exist_ok=True)
        (root / 'edge-neural-smoke.wav').write_bytes(wav)
        print('Real Edge neural WAV:', root / 'edge-neural-smoke.wav', 'bytes:', len(wav))

class SpeechTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_edge_neural_and_invalid_backend_is_rejected(self):
        from voice_app.speech import Speech
        speech = Speech(environ={'VOICE_TTS_BACKEND':'edge', 'VOICE_STT_BACKEND':'browser'})
        self.assertIn('Edge', speech.tts_label)
        self.assertIn('online neural', speech.tts_label)
        self.assertIn('id-ID-GadisNeural', speech.tts_label)
        self.assertIn('browser', speech.stt_reason)
        with self.assertRaises(ValueError):
            Speech(environ={'VOICE_TTS_BACKEND':'unknown'})
        with self.assertRaises(ValueError):
            Speech(environ={'VOICE_TTS_VOICE':'../../not-a-voice'})

    async def test_edge_mp3_subprocess_is_converted_to_canonical_pcm16_wav(self):
        import subprocess
        import sys
        from unittest.mock import patch
        from voice_app.speech import Speech
        ffmpeg = '/root/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg'
        mp3 = subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                              'sine=frequency=440:duration=0.1', '-f', 'mp3', 'pipe:1'],
                             check=True, capture_output=True).stdout
        original = asyncio.create_subprocess_exec
        commands = []
        async def spawn(*args, **kwargs):
            commands.append(args)
            if '--edge-worker' in args:
                # Deterministic remote-transport fixture; conversion is real ffmpeg.
                return await original(sys.executable, '-c',
                    'import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(' + repr(mp3) + ')', **kwargs)
            return await original(*args, **kwargs)
        with patch('voice_app.speech.asyncio.create_subprocess_exec', spawn):
            wav = await Speech(environ={'VOICE_TTS_BACKEND':'edge', 'VOICE_STT_BACKEND':'browser'}).synthesize('Halo dari pengujian.')
        self.assertEqual(commands[0][0], '/root/voice-web-venv/bin/python')
        self.assertIn('--edge-worker', commands[0])
        self.assertEqual(commands[1][0], ffmpeg)
        with wave.open(io.BytesIO(wav)) as reader:
            self.assertEqual(reader.getnchannels(), 1)
            self.assertEqual(reader.getsampwidth(), 2)
            self.assertEqual(reader.getframerate(), 24000)
            frames = reader.readframes(reader.getnframes())
            self.assertGreater(len(frames), 100)
        self.assertEqual(int.from_bytes(wav[4:8], 'little'), len(wav)-8)

    def test_stt_configuration_rejects_credential_urls_and_partial_config(self):
        from voice_app.speech import Speech
        for url in ('ftp://example.com', 'https://user:pass@example.com', 'https://example.com?key=x'):
            with self.assertRaises(ValueError):
                Speech(environ={'VOICE_STT_API_BASE_URL':url, 'VOICE_STT_API_KEY':'fixture', 'VOICE_STT_MODEL':'configured'})
        speech = Speech(environ={'VOICE_STT_BACKEND':'api', 'VOICE_STT_API_KEY':'fixture'})
        self.assertFalse(speech.stt_available)

    async def test_cancelling_synthesis_reaps_real_subprocess(self):
        from unittest.mock import patch
        from voice_app.speech import Speech
        original = asyncio.create_subprocess_exec
        processes = []
        ready = asyncio.Event()
        async def spawn(*args, **kwargs):
            proc = await original(*args, **kwargs)
            processes.append(proc); ready.set()
            return proc
        with patch('voice_app.speech.asyncio.create_subprocess_exec', spawn):
            task = asyncio.create_task(Speech(environ={'VOICE_TTS_BACKEND':'espeak'}).synthesize('Halo dunia. '*300))
            await ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(processes[0].returncode)

    async def test_edge_failure_never_falls_back_to_espeak(self):
        import sys
        from unittest.mock import patch
        from voice_app.speech import Speech
        original = asyncio.create_subprocess_exec
        commands = []
        async def spawn(*args, **kwargs):
            commands.append(args)
            return await original(sys.executable, '-c', 'raise SystemExit(1)', **kwargs)
        with patch('voice_app.speech.asyncio.create_subprocess_exec', spawn):
            with self.assertRaises(RuntimeError):
                await Speech(environ={'VOICE_TTS_BACKEND':'edge', 'VOICE_STT_BACKEND':'browser'}).synthesize('Halo.')
        self.assertEqual(len(commands), 1)
        self.assertIn('--edge-worker', commands[0])

    async def test_edge_output_bound_timeout_and_cancel_reap_real_processes(self):
        import sys
        from unittest.mock import patch
        from voice_app.speech import Speech
        original = asyncio.create_subprocess_exec
        for action in ('limit', 'timeout', 'cancel'):
            processes = []
            ready = asyncio.Event()
            code = ('import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b"x"*10000); sys.stdout.flush(); '
                    'import time; time.sleep(30)') if action == 'limit' else 'import time; time.sleep(30)'
            async def spawn(*args, **kwargs):
                proc = await original(sys.executable, '-c', code, **kwargs)
                processes.append(proc); ready.set()
                return proc
            with patch('voice_app.speech.asyncio.create_subprocess_exec', spawn), \
                 patch('voice_app.speech.MAX_MP3_BYTES', 1000), \
                 patch('voice_app.speech.TTS_TIMEOUT', .15 if action == 'timeout' else 5):
                task = asyncio.create_task(Speech(environ={'VOICE_TTS_BACKEND':'edge', 'VOICE_STT_BACKEND':'browser'}).synthesize('Halo.'))
                await ready.wait()
                if action == 'cancel':
                    task.cancel()
                expected = {'limit':ValueError, 'timeout':TimeoutError, 'cancel':asyncio.CancelledError}[action]
                with self.assertRaises(expected):
                    await task
            self.assertIsNotNone(processes[0].returncode)

    async def test_stt_posts_explicit_wav_multipart_configuration(self):
        import httpx
        from voice_app.audio import pcm16_wav
        from voice_app.speech import Speech
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={'text':'Halo dari suara.'})
        speech = Speech(environ={'VOICE_STT_BACKEND':'api', 'VOICE_STT_API_BASE_URL':'https://stt.example/v1',
                               'VOICE_STT_API_KEY':'fixture-only', 'VOICE_STT_MODEL':'configured-model'},
                        transport=httpx.MockTransport(handle))
        self.assertTrue(speech.stt_available)
        wav = pcm16_wav(b'\0\0'*160, 16000)
        self.assertEqual(await speech.transcribe(wav), 'Halo dari suara.')
        self.assertEqual(str(requests[0].url), 'https://stt.example/v1/audio/transcriptions')
        self.assertIn(b'configured-model', requests[0].content)
        self.assertIn(wav, requests[0].content)
        self.assertIn(b'audio/wav', requests[0].content)
        self.assertEqual(requests[0].headers['authorization'], 'Bearer fixture-only')

    async def test_real_espeak_indonesian_wav_and_limits(self):
        from voice_app.speech import Speech
        speech = Speech(environ={'VOICE_TTS_BACKEND':'espeak', 'VOICE_STT_BACKEND':'browser'})
        self.assertFalse(speech.stt_available)
        self.assertIn('non-AI', speech.tts_label)
        wav = await speech.synthesize('Halo, ini pengujian suara bahasa Indonesia.')
        with wave.open(io.BytesIO(wav)) as reader:
            self.assertEqual(reader.getnchannels(), 1)
            self.assertGreater(reader.getnframes(), 100)
            self.assertLess(len(wav), 4000000)
        for text in ('', 'x'*4001):
            with self.assertRaises(ValueError):
                await speech.synthesize(text)
        with self.assertRaises(ValueError):
            await speech.transcribe(b'RIFF')
