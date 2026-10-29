"""Stateful ASR contracts. Fake native objects are not acoustic verification."""
import asyncio
import importlib.util
import unittest
import io
import json
import struct
from voice_app.audio import pcm16_wav


def frame(op, key, wav=b''):
    payload = b'ST1' + bytes([op]) + bytes.fromhex(key) + wav
    return struct.pack('!I', len(payload)) + payload


class ProtocolTests(unittest.TestCase):
    def worker(self):
        self.assertIsNotNone(importlib.util.find_spec('voice_app.zipformer_stateful_worker'),
                             'stateful worker protocol is missing')
        from voice_app import zipformer_stateful_worker
        return zipformer_stateful_worker

    def test_framing_wav_prefix_final_and_owner_scoped_forget(self):
        w = self.worker()
        r = Recognizer()
        pcm = b'\x01\x00' * 1600
        request = (frame(0, A, pcm16_wav(pcm[:1600], 16000)) + frame(2, B)
                   + frame(1, A, pcm16_wav(pcm, 16000)) + frame(2, A))
        output = io.BytesIO()
        w.serve(r, io.BytesIO(request), output, enforce_limits=False)
        reply = io.BytesIO(output.getvalue())
        self.assertEqual(reply.readline(), b'READY\n')
        bodies = []
        while header := reply.read(4):
            bodies.append(json.loads(reply.read(struct.unpack('!I', header)[0])))
        self.assertEqual(len(bodies), 4)
        self.assertEqual(len(r.streams), 1)
        self.assertEqual(bodies[2]['metrics']['source_samples_fed'], 1600)
        self.assertEqual(bodies[2]['metrics']['new_samples_fed'], 800)
        self.assertFalse(bodies[1]['metrics']['forgotten'])
        self.assertFalse(bodies[3]['metrics']['forgotten'])
        for body in bodies:
            self.assertNotIn(A, json.dumps(body['metrics']))
            self.assertNotIn(B, json.dumps(body['metrics']))
            self.assertFalse(body['metrics']['prefix_redecode'])

    def test_bad_frames_and_wav_fail_closed_before_feed(self):
        w = self.worker()
        good = frame(0, A, pcm16_wav(b'\0\0'*10, 16000))
        malformed = [b'\0', struct.pack('!I', 640065), frame(3, A),
                     frame(2, A, b'extra'), good[:-1],
                     frame(0, A, pcm16_wav(b'\0\0'*10, 16000)[:24] + struct.pack('<I', 8000)
                           + pcm16_wav(b'\0\0'*10, 16000)[28:]),
                     frame(0, A, b'not wav')]
        for request in malformed:
            with self.subTest(size=len(request)):
                r = Recognizer()
                with self.assertRaises((ValueError, EOFError)):
                    w.serve(r, io.BytesIO(request), io.BytesIO(), enforce_limits=False)
                self.assertEqual(r.streams, [])

A = 'a' * 32
B = 'b' * 32

class Stream:
    def __init__(self):
        self.count = 0
        self.finished = False
    def accept_waveform(self, rate, samples):
        assert rate == 16000
        self.count += len(samples)
    def input_finished(self):
        self.finished = True

class Recognizer:
    def __init__(self):
        self.streams = []
        self.resets = []
    def create_stream(self):
        s = Stream()
        self.streams.append(s)
        return s
    def is_ready(self, stream):
        return False
    def get_result(self, stream):
        return str(stream.count)
    def reset(self, stream):
        self.resets.append(stream)
    def is_endpoint(self, stream):
        raise AssertionError('application, not native endpoint, owns lifetime')

class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, *, cpu=0, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('voice_app.stateful_asr'),
                             'stateful bounded process adapter is missing')
        from voice_app.stateful_asr import StatefulASR
        from pathlib import Path
        import sys
        script = '''import sys,time,types
sys.path.insert(0, sys.argv[1])
from test_stateful_worker import Recognizer
from voice_app import zipformer_stateful_worker as w
original = w._handle
def slow(state, payload):
 time.sleep(.12)
 return original(state,payload)
w._handle = slow
w.resource.getrusage = lambda _: types.SimpleNamespace(ru_utime=float(sys.argv[2]),ru_stime=0,ru_maxrss=1234)
w.serve(Recognizer(),sys.stdin.buffer,sys.stdout.buffer,enforce_limits=False)
'''
        return StatefulASR((sys.executable, '-I', '-c', script,
                            str(Path(__file__).resolve().parent), str(cpu)),
                           {'API_KEY':'DO NOT INHERIT', 'HTTPS_PROXY':'blocked'}, **kwargs)

    async def test_process_preserves_stream_cancellation_final_and_forget(self):
        w = self.adapter()
        wav = pcm16_wav(b'\x01\x00'*100, 16000)
        try:
            await w.forget(A)
            self.assertIsNone(w.pid)
            self.assertNotIn('API_KEY', w.env)
            self.assertNotIn('HTTPS_PROXY', w.env)
            await w.ready()
            pid = w.pid
            t = asyncio.create_task(w.preview(wav, stream_key=A))
            await asyncio.sleep(.03)
            t.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await t
            self.assertIsNone(await w.preview(wav, stream_key=B))
            await w.forget(B)
            self.assertEqual(w.pid, pid)
            self.assertEqual(await w.transcribe(wav, stream_key=A), str(25700))
            self.assertEqual(w.last_metrics['new_samples_fed'], 0)
            self.assertEqual(w.last_metrics['stream_resets'], 1)
            self.assertEqual(await w.preview(wav, stream_key=A), '100')
            await w.forget(A)
            self.assertTrue(w.last_metrics['forgotten'])
            self.assertEqual(await w.preview(wav, stream_key=A), '100')
        finally:
            await w.close()
        self.assertIsNone(w.pid)

    async def test_forget_deadline_and_cancellation_do_not_steal_live_capacity(self):
        from voice_app.speech import LocalAudioBusy
        wav = pcm16_wav(b'\x01\x00'*100, 16000)
        w = self.adapter()
        try:
            await w.ready()
            pid = w.pid
            w._forget_wait_seconds = .015
            producer = asyncio.create_task(w.preview(wav, stream_key=A))
            await asyncio.sleep(.02)
            with self.assertRaises(LocalAudioBusy):
                await w.forget(A)
            self.assertEqual(w.pid, pid)
            self.assertIsNone(await w.preview(wav, stream_key=B))
            forgetting = asyncio.create_task(w.forget(A))
            await asyncio.sleep(.005)
            forgetting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await forgetting
            self.assertIsNone(await w.preview(wav, stream_key=B))
            self.assertEqual(await producer, '100')
            self.assertEqual(await w.transcribe(wav, stream_key=A), '25700')
            self.assertEqual(w.last_metrics['new_samples_fed'], 0)
        finally:
            await w.close()

    async def test_idle_cpu_quiesce_and_caps(self):
        wav = pcm16_wav(b'\x01\x00'*100, 16000)
        w = self.adapter(idle_seconds=.08)
        try:
            for key in ('A'*32, 'a'*31, None):
                with self.assertRaises(ValueError):
                    await w.preview(wav, stream_key=key)
            for bad in (b'', b'a'*640045):
                with self.assertRaises(ValueError):
                    await w.transcribe(bad, stream_key=A)
            await w.ready()
            async with w.quiesce():
                self.assertIsNone(await w.preview(wav, stream_key=A))
            await asyncio.sleep(.15)
            self.assertIsNone(w.pid)
            self.assertEqual(await w.transcribe(wav, stream_key=A), '25700')
        finally:
            await w.close()
        w = self.adapter(cpu=75)
        try:
            await w.preview(wav, stream_key=A)
            self.assertIsNone(w.pid)
            self.assertEqual(await w.transcribe(wav, stream_key=A), '25700')
            self.assertIsNone(w.pid)
        finally:
            await w.close()


class StateTests(unittest.TestCase):
    def state(self):
        self.assertIsNotNone(importlib.util.find_spec('voice_app.zipformer_state'),
                             'incremental native state module is missing')
        from voice_app.zipformer_state import ZipformerState
        r = Recognizer()
        return ZipformerState(r), r

    def test_duplicate_mutation_shrink_and_owner_switch(self):
        state, r = self.state()
        pcm = b'\x01\x00' * 4000
        state.update(A, pcm)
        duplicate = state.update(A, pcm)
        self.assertEqual(duplicate['metrics']['new_samples_fed'], 0)
        for bad in (pcm[:-2], b'\x02\x00' + pcm[2:], pcm[::-1]):
            with self.assertRaises(ValueError):
                state.update(A, bad)
        self.assertEqual(r.streams[0].count, 4000)
        state.update(B, pcm[:2000])
        self.assertEqual(len(r.streams), 2)
        self.assertEqual(r.resets, [r.streams[0]])
        self.assertEqual(r.streams[1].count, 1000)
        self.assertFalse(state.forget(A))
        self.assertIs(state.stream, r.streams[1])
        self.assertTrue(state.forget(B))
        self.assertIsNone(state.stream)
        self.assertFalse(state.forget(B))
        final = state.update(A, pcm, final=True)
        self.assertEqual(final['metrics']['new_samples_fed'], 4000)
        self.assertEqual(final['metrics']['source_samples_fed'], 4000)
        self.assertEqual(final['metrics']['stream_resets'], 3)
        self.assertIsNone(state.digest)
        self.assertNotIn(pcm, vars(state).values())

    def test_validation_before_native_mutation(self):
        state, r = self.state()
        for key in ('A'*32, 'a'*31, 'g'*32, None, b'a'*32):
            with self.assertRaises(ValueError):
                state.update(key, b'\0\0')
        for pcm in (b'', b'a', b'a'*640002, 'string'):
            with self.assertRaises(ValueError):
                state.update(A, pcm)
        self.assertEqual(r.streams, [])

    def test_same_native_stream_across_growing_prefixes_and_final(self):
        state, r = self.state()
        pcm = b'\x01\x00' * 4000
        first = state.update(A, pcm[:4000])
        second = state.update(A, pcm)
        final = state.update(A, pcm, final=True)
        self.assertEqual(len(r.streams), 1)
        self.assertEqual(first['text'], '2000')
        self.assertEqual(second['text'], '4000')
        self.assertEqual(final['text'], str(4000 + 25600))
        self.assertEqual(final['metrics']['source_samples_fed'], 4000)
        self.assertEqual(final['metrics']['new_samples_fed'], 0)
        self.assertFalse(final['metrics']['prefix_redecode'])
        self.assertTrue(r.streams[0].finished)
        self.assertEqual(r.resets, r.streams)
        self.assertIsNone(state.stream)
