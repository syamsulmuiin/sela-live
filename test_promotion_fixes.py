"""Promotion blockers: real relocated worker and bounded lifecycle regressions."""
import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_zipformer import PY, ROOT, WORKER
from unittest.mock import patch
from voice_app.stream_asr import PersistentASR
from voice_app.speech import _offline_env

REAL_TIMEOUT = asyncio.timeout

def short_ready_budget(seconds):
    return REAL_TIMEOUT(.05 if seconds == 25 else seconds)

class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_lock_busy_timeout_does_not_reap_active_producer(self):
        worker = PersistentASR(('/unused',), {})
        entered = asyncio.Event()
        release = asyncio.Event()
        async def exchange():
            async with worker._lock:
                entered.set()
                await release.wait()
                return 'active producer completed'
        producer = asyncio.create_task(exchange())
        worker._job = producer
        await entered.wait()
        try:
            with patch('voice_app.stream_asr.asyncio.timeout', short_ready_budget):
                started = asyncio.get_running_loop().time()
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(worker.ready(), .3)
                self.assertLess(asyncio.get_running_loop().time()-started, .15)
            self.assertTrue(worker.busy)
            self.assertFalse(producer.done())
            self.assertIsNone(await worker.preview(b'x'))
        finally:
            release.set()
            self.assertEqual(await producer, 'active producer completed')
            await worker.close()

    async def test_ready_timeout_and_cancel_cannot_reap_live_native_producer(self):
        import sys
        from test_fast_worker import SCRIPT
        with tempfile.TemporaryDirectory(dir=STAGE) as directory:
            path=Path(directory)/'framed.py'
            path.write_text(SCRIPT.replace('time.sleep(.05)', 'time.sleep(.25)'))
            for cancel in (False, True):
                worker=PersistentASR((sys.executable,'-I',str(path),'fixture','1'), {})
                await worker.ready();pid=worker.pid
                producer=asyncio.create_task(worker.preview(b'x'))
                await asyncio.sleep(.02)
                try:
                    with patch('voice_app.stream_asr.asyncio.timeout', short_ready_budget):
                        waiter=asyncio.create_task(worker.ready())
                        if cancel:
                            await asyncio.sleep(.01);waiter.cancel()
                            with self.assertRaises(asyncio.CancelledError):await waiter
                        else:
                            with self.assertRaises(TimeoutError):await asyncio.wait_for(waiter,.15)
                    self.assertEqual(worker.pid,pid)
                    self.assertTrue(worker.busy)
                    self.assertFalse(producer.done())
                    self.assertIsNone(await worker.preview(b'blocked'))
                    self.assertEqual(await producer,'fixture')
                    self.assertEqual(worker.pid,pid)
                finally:
                    await worker.close()

    async def test_cancelled_lock_waiter_leaves_producer_owned_capacity(self):
        worker = PersistentASR(('/unused',), {})
        await worker._lock.acquire()
        waiter = asyncio.create_task(worker.ready())
        await asyncio.sleep(.01)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertTrue(worker.busy)
        self.assertIsNone(await worker.preview(b'x'))
        worker._lock.release()

    async def test_delayed_native_readiness_and_owned_timeout_cleanup(self):
        import sys
        with tempfile.TemporaryDirectory(dir=STAGE) as directory:
            path = Path(directory)/'delayed.py'
            path.write_text("import time,sys\ntime.sleep(.12)\nprint('READY',flush=True)\nsys.stdin.read()\n")
            worker = PersistentASR((sys.executable, '-I', str(path)), {})
            try:
                await worker.ready()
                self.assertIsNotNone(worker.pid)
            finally:
                await worker.close()
            with patch('voice_app.stream_asr.asyncio.timeout', short_ready_budget):
                with self.assertRaises(TimeoutError):
                    await worker.ready()
            self.assertIsNone(worker.pid)
            self.assertFalse(worker.busy)

STAGE = Path(__file__).resolve().parent

class WarmPiperTests(unittest.IsolatedAsyncioTestCase):
    async def test_zipformer_retained_quiescent_through_piper_whisper_reaped(self):
        import sys
        from voice_app.speech import Speech
        from test_zipformer import ENV
        script = """import sys,struct,json,time
print('READY',flush=True)
while True:
 h=sys.stdin.buffer.read(4)
 if not h: break
 n=struct.unpack('!I',h)[0];sys.stdin.buffer.read(n)
 time.sleep(.12)
 body=json.dumps({'text':'fixture','metrics':{'cpu_seconds':1}}).encode()
 sys.stdout.buffer.write(struct.pack('!I',len(body))+body);sys.stdout.buffer.flush()
"""
        with tempfile.TemporaryDirectory(dir=STAGE) as directory:
            path=Path(directory)/'framed.py';path.write_text(script)
            for engine in ('zipformer_id_int8', 'whisper'):
                with self.subTest(engine=engine):
                    env = ENV if engine == 'zipformer_id_int8' else {
                        k:v for k,v in (ENV|{'VOICE_LOCAL_ASR_ENGINE':'whisper'}).items()
                        if not k.startswith('VOICE_ZIPFORMER_')}
                    with patch('voice_app.speech._runtime_ready', return_value=True):
                        speech=Speech(env)
                    worker=PersistentASR((sys.executable,'-I',str(path)), {}, idle_seconds=.5)
                    speech._stream_asr=worker
                    await worker.ready(); pid=worker.pid
                    active=asyncio.create_task(speech.preview(b'x'))
                    await asyncio.sleep(.03)
                    async def piper(*args, **kwargs):
                        self.assertTrue(active.done(), 'ASR must finish before Piper allocation/compute')
                        self.assertTrue(speech._local_slot.locked())
                        self.assertIsNone(await speech.preview(b'x'))
                        if engine == 'zipformer_id_int8':
                            self.assertEqual(worker.pid,pid, 'light model must remain warm')
                            self.assertTrue(worker.busy, 'warm worker must be exclusively quiesced')
                            self.assertIsNone(await worker.preview(b'x'))
                        else:
                            self.assertIsNone(worker.pid, 'heavy Whisper must be reaped')
                        await asyncio.sleep(.02)
                        return b'tts-fixture'
                    try:
                        with patch('voice_app.speech._bounded_process', side_effect=piper):
                            self.assertEqual(await speech._local_request('tts',b'x',100),b'tts-fixture')
                        self.assertEqual(await active,'fixture')
                        if engine == 'zipformer_id_int8':
                            self.assertEqual(await speech.preview(b'second'),'fixture')
                            self.assertEqual(worker.pid,pid)
                            await asyncio.sleep(.6)
                            self.assertIsNone(worker.pid, 'idle recycling must still operate')
                    finally:
                        await worker.close()

    async def test_quiesce_timeout_and_cancellation_preserve_active_capacity(self):
        import sys
        from voice_app.speech import Speech, LocalAudioBusy
        from test_zipformer import ENV
        from test_fast_worker import SCRIPT
        with tempfile.TemporaryDirectory(dir=STAGE) as directory:
            path=Path(directory)/'framed.py'
            path.write_text(SCRIPT.replace('time.sleep(.05)', 'time.sleep(.25)'))
            for cancel in (False, True):
                with self.subTest(cancel=cancel):
                    with patch('voice_app.speech._runtime_ready', return_value=True):
                        speech=Speech(ENV)
                    worker=PersistentASR((sys.executable,'-I',str(path),'fixture','1'), {})
                    speech._stream_asr=worker
                    await worker.ready();pid=worker.pid
                    active=asyncio.create_task(speech.preview(b'x'))
                    await asyncio.sleep(.02)
                    try:
                        with patch('voice_app.speech.LOCAL_RETRY_WAIT', .04), patch('voice_app.speech._bounded_process') as piper:
                            tts=asyncio.create_task(speech._local_request('tts',b'x',100))
                            if cancel:
                                await asyncio.sleep(.01);tts.cancel()
                                with self.assertRaises(asyncio.CancelledError): await tts
                            else:
                                with self.assertRaises(LocalAudioBusy): await asyncio.wait_for(tts,.15)
                            piper.assert_not_called()
                        self.assertEqual(worker.pid,pid)
                        self.assertTrue(worker.busy)
                        self.assertIsNone(await speech.preview(b'blocked'))
                        self.assertEqual(await active,'fixture')
                        self.assertEqual(worker.pid,pid)
                    finally:
                        await worker.close()

    async def test_cancelled_piper_retains_exclusion_until_cleanup_completes(self):
        import sys
        from voice_app.speech import Speech
        from test_zipformer import ENV
        from test_fast_worker import SCRIPT
        with tempfile.TemporaryDirectory(dir=STAGE) as directory:
            path=Path(directory)/'framed.py';path.write_text(SCRIPT)
            with patch('voice_app.speech._runtime_ready', return_value=True): speech=Speech(ENV)
            worker=PersistentASR((sys.executable,'-I',str(path),'fixture','1'), {})
            speech._stream_asr=worker
            await worker.ready();pid=worker.pid
            entered=asyncio.Event();cleaning=asyncio.Event();release=asyncio.Event()
            async def piper(*args, **kwargs):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
            try:
                with patch('voice_app.speech._bounded_process', side_effect=piper):
                    tts=asyncio.create_task(speech._local_request('tts',b'x',100))
                    await entered.wait();tts.cancel();await cleaning.wait()
                    self.assertTrue(speech._local_slot.locked())
                    self.assertTrue(worker.busy)
                    self.assertEqual(worker.pid,pid)
                    self.assertIsNone(await speech.preview(b'blocked'))
                    self.assertIsNone(await worker.preview(b'blocked'))
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):await tts
                self.assertFalse(speech._local_slot.locked())
                self.assertFalse(worker.busy)
                self.assertEqual(await speech.preview(b'next'),'fixture')
                self.assertEqual(worker.pid,pid)
            finally:
                release.set()
                await worker.close()

class NativeWarmPiperTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_zipformer_pid_survives_real_piper_and_second_turn(self):
        import json
        import wave
        import io
        from voice_app.speech import Speech, _bounded_process
        from voice_app.audio import pcm16_wav
        from test_zipformer import ENV
        speech=Speech(ENV)
        silence=pcm16_wav(b'\0\0'*16000,16000)
        receipt={}
        try:
            started=asyncio.get_running_loop().time()
            await speech.prepare_preview()
            worker=speech._stream_asr;pid=worker.pid
            receipt['cold_ready_seconds']=asyncio.get_running_loop().time()-started
            self.assertIsNotNone(pid)
            self.assertEqual(await speech.preview(silence),'')
            async def real_piper(*args, **kwargs):
                self.assertEqual(worker.pid,pid)
                self.assertTrue(worker.busy)
                self.assertIsNone(await speech.preview(silence))
                self.assertIsNone(await worker.preview(silence))
                receipt['quiesced_during_real_piper']=True
                return await _bounded_process(*args, **kwargs)
            with patch('voice_app.speech._bounded_process', side_effect=real_piper):
                audio=await speech.synthesize('Halo.')
            with wave.open(io.BytesIO(audio)) as reader:
                self.assertEqual((reader.getnchannels(),reader.getsampwidth(),reader.getframerate()),(1,2,22050))
                self.assertGreater(reader.getnframes(),0)
            started=asyncio.get_running_loop().time()
            self.assertEqual(await speech.preview(silence),'')
            self.assertEqual(worker.pid,pid)
            receipt.update(second_preview_seconds=asyncio.get_running_loop().time()-started,
                           same_native_pid_after_piper=True, wav_bytes=len(audio),
                           final_inference_count=worker.inference_count)
            self.assertEqual(worker.inference_count,2)
        finally:
            await speech.close_preview()
        self.assertIsNone(worker.pid)
        receipt['owned_pid_reaped']=True
        print('NATIVE_WARM_PIPER '+json.dumps(receipt,sort_keys=True))

class CorrelatedStartTests(unittest.TestCase):
    def test_failed_readiness_reports_matching_start_id(self):
        from fastapi.testclient import TestClient
        from voice_app.server import create_app
        from test_preview_protocol import DraftSpeech, CountProvider
        speech = DraftSpeech()
        async def fail():
            await asyncio.sleep(.02)
            raise TimeoutError('private failure')
        speech.prepare_preview = fail
        with tempfile.TemporaryDirectory(dir=STAGE) as directory:
            app = create_app(provider=CountProvider(), speech=speech, data_dir=directory,
                             local_test_auth=True, public_origin='')
            with TestClient(app) as client, client.websocket_connect('/ws', headers={
                    'host':'localhost', 'origin':'http://localhost'}) as ws:
                ws.receive_json()
                ws.send_json({'type':'start','start_id':'load-timeout','local_preview':True})
                for _ in range(5):
                    event = ws.receive_json()
                    if event['type'] == 'error':
                        break
                self.assertEqual(event['type'], 'error')
                self.assertEqual(event['command'], 'start')
                self.assertEqual(event['code'], 'local_audio_busy')
                self.assertEqual(event.get('start_id'), 'load-timeout')
                self.assertNotIn('private failure', str(event))

class RelocatedWorkerTests(unittest.TestCase):
    def test_real_authorized_model_check_from_relocated_worker(self):
        with tempfile.TemporaryDirectory(dir=STAGE) as directory:
            target = Path(directory) / 'voice_app' / 'renamed_zipformer_worker.py'
            target.parent.mkdir()
            shutil.copyfile(WORKER, target)
            result = subprocess.run((PY, '-I', str(target), 'check', str(ROOT/'model')),
                capture_output=True, env=_offline_env(), timeout=20)
            self.assertEqual(result.returncode, 0)
            self.assertEqual((result.stdout, result.stderr), (b'', b''))
