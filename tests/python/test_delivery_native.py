"""Numeric native delivery only: no cached phrases or emotional conditioning."""
import asyncio
import inspect
import json
import subprocess
import sys
from pathlib import Path
import types
import unittest
from unittest.mock import patch

from tests.python import test_supertonic3_adapter as fixtures
from voice_app.speech import Speech
from voice_app import supertonic3_worker as worker


class NativeWorkerValidationTests(unittest.TestCase):
    def test_only_bounded_finite_numeric_delivery_is_accepted(self):
        text = 'Teks asli tetap utuh.'
        for controls in ({'speed': 0.9, 'silence_duration': 0.1},
                         {'speed': 1.1, 'silence_duration': 0.5},
                         {'speed': 1, 'silence_duration': 0.3}):
            request = {'text': text, 'delivery': controls.copy()}
            self.assertEqual(worker.validate_request(request), text)
            self.assertEqual(request, {'text': text, 'delivery': controls})
        good = {'speed': 1.0, 'silence_duration': 0.3}
        invalid = [None, [], 'sad', {}, {'speed': 1.0},
                   good | {'emotion': 'sad'}, good | {'voice_style': 'F1'}]
        for key in good:
            for value in (True, False, None, '1.0', float('nan'), float('inf'),
                          -float('inf'), [], {}):
                invalid.append(good | {key: value})
        invalid += [good | {'speed': x} for x in (0.899, 1.101)]
        invalid += [good | {'silence_duration': x} for x in (0.099, 0.501)]
        for controls in invalid:
            with self.subTest(controls=controls), self.assertRaises(ValueError):
                worker.validate_request({'text': text, 'delivery': controls})
        for request in ({'text': text, 'emotion': 'sad'},
                        {'text': text, 'delivery': good, 'voice': 'F1'}):
            with self.assertRaises(ValueError):
                worker.validate_request(request)


    def test_worker_cli_rejects_delivery_labels_before_loading_assets(self):
        runtime = Path('/root/voice-lab/autonomous/expressive-tts/venv/bin/python')
        script = Path(worker.__file__)
        # Missing assets deliberately prove invalid input is rejected first.
        for delivery in ({'emotion': 'sad'}, {'speed': True, 'silence_duration': 0.3},
                         {'speed': float('nan'), 'silence_duration': 0.3},
                         {'speed': 1.0, 'silence_duration': 0.501}):
            result = subprocess.run((str(runtime), '-I', str(script), 'tts',
                    '/missing/native-assets', '/missing/native-manifest'),
                input=json.dumps({'text': 'Halo.', 'delivery': delivery}).encode(),
                capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')
            self.assertEqual(result.stderr, worker.FAILURE_PREFIX + b'invalid_text\n')

    def test_invalid_delivery_has_safe_fixed_failure_reason(self):
        try:
            worker.validate_request({'text': 'Halo.', 'delivery': {'emotion': 'sad'}})
        except ValueError as exc:
            self.assertEqual(worker.failure_reason(exc), 'invalid_text')
        else:
            self.fail('Worker accepted unsupported emotional metadata')

    def run_numeric(self, body):
        # Preserve the app/SDK interpreter's independence from native numpy.
        import textwrap
        setup = '''import importlib.util,sys,unittest
import numpy as np
spec=importlib.util.spec_from_file_location('worker',sys.argv[1])
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
self=unittest.TestCase()
runtime=worker.Runtime.__new__(worker.Runtime)
runtime.style=object();runtime.cold_load_seconds=0;runtime.calls=0
'''
        result = subprocess.run(('/root/voice-lab/autonomous/expressive-tts/venv/bin/python',
                '-I', '-c', setup + textwrap.dedent(body), worker.__file__),
            capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_delivery_retains_source_and_audio_guards(self):
        controls = {'speed': 0.9, 'silence_duration': 0.5}
        for text in ('x' * 801, '🙂' * 801, '<angry>Halo', 'Halo\x00'):
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                worker.validate_request({'text': text, 'delivery': controls})
        self.run_numeric('''
            controls = {'speed': 0.9, 'silence_duration': 0.5}
            class Engine:
                samples = np.full(44100 * 30 + 1, 0.1)
                def synthesize(self, text, **kwargs):
                    return self.samples, len(self.samples) / 44100
            engine = runtime.engine = Engine()
            for samples, reason in ((engine.samples, 'duration_limit'),
                                    (np.array([float('nan')]), 'invalid_audio'),
                                    (np.array([1.0]), 'clipping'),
                                    (np.array([0.0]), 'empty_audio')):
                engine.samples = samples
                with self.assertRaisesRegex(ValueError, reason):
                    runtime.synthesize({'text': 'Halo.', 'delivery': controls})
        ''')

    def test_runtime_passes_controls_without_changing_voice_text_or_defaults(self):
        self.run_numeric('''
            calls = []
            class Engine:
                def synthesize(self, text, **kwargs):
                    calls.append((text, kwargs))
                    return np.full(100, 0.1), 100 / 44100
            runtime.engine = Engine()
            controls = {'speed': 0.94, 'silence_duration': 0.42}
            text = 'Kalimat asli, tanpa label.'
            runtime.synthesize({'text': text, 'delivery': controls})
            runtime.synthesize({'text': text})
            base = {'voice_style': runtime.style, 'lang': 'id', 'total_steps': 8,
                    'speed': 1.05, 'verbose': False}
            self.assertEqual(calls, [(text, base | controls), (text, base)])
        ''')


class FakeDelivery:
    """Hidden intent stays here; only numbers may cross the native boundary."""
    def __init__(self, speed=0.94, silence_duration=0.42):
        self.controls = {'speed': speed, 'silence_duration': silence_duration}
        self.emotion = 'unsupported-future-emotion'
        self.calls = 0

    def native_controls(self):
        self.calls += 1
        return self.controls


class NativeSpeechTests(unittest.IsolatedAsyncioTestCase):
    def make_speech(self):
        with fixtures.configured(VOICE_EXPERIMENTAL_DELIVERY_PROSODY='1') as env, patch('voice_app.speech.subprocess.run',
                return_value=subprocess.CompletedProcess([], 0)):
            return Speech(environ=env)

    async def test_experimental_prosody_requires_explicit_listener_trial_opt_in(self):
        # Numeric pacing presets are not listener-approved; availability alone
        # must never enable their use in a default production configuration.
        for flag in (None, '0'):
            with fixtures.configured() as env, patch('voice_app.speech.subprocess.run',
                    return_value=subprocess.CompletedProcess([], 0)):
                if flag is not None:
                    env['VOICE_EXPERIMENTAL_DELIVERY_PROSODY'] = flag
                speech = Speech(environ=env)
            self.assertTrue(speech.delivery_controls_available)
            self.assertFalse(speech.delivery_controls_supported)
            delivery = FakeDelivery()
            with patch.object(speech, '_local_request', return_value=fixtures.fixture_wav()) as process:
                with self.assertRaises(ValueError):
                    await speech.synthesize('Kalimat asli.', delivery=delivery)
                with self.assertRaises(ValueError):
                    await anext(speech.synthesize_chunks('Kalimat asli.', delivery=delivery))
                process.assert_not_called()
                self.assertEqual(delivery.calls, 0)
                await speech.synthesize('Kalimat asli.')
                self.assertEqual(json.loads(process.call_args.args[1]), {'text': 'Kalimat asli.'})
        for backend in ('piper', 'edge', 'espeak'):
            for flag in ('0', '1'):
                with patch('voice_app.speech._runtime_ready', return_value=False):
                    speech = Speech(environ={'VOICE_TTS_BACKEND': backend,
                        'VOICE_STT_BACKEND': 'browser', 'VOICE_EXPERIMENTAL_DELIVERY_PROSODY': flag})
                self.assertFalse(speech.delivery_controls_available)
                self.assertFalse(speech.delivery_controls_supported)
                with self.assertRaises(ValueError):
                    await speech.synthesize('Kalimat asli.', delivery=FakeDelivery())
        for bad in ('true', 'false', '', 'yes', '2', True, 1, None):
            with self.subTest(flag=bad), self.assertRaises(ValueError):
                Speech(environ={'VOICE_TTS_BACKEND': 'espeak', 'VOICE_STT_BACKEND': 'browser',
                    'VOICE_EXPERIMENTAL_DELIVERY_PROSODY': bad})

    async def test_numeric_delivery_crosses_real_process_and_cancel_reaps_child(self):
        speech = self.make_speech()
        original_spawn = asyncio.create_subprocess_exec
        for cancel in (False, True):
            children = []
            ready = asyncio.Event()
            async def spawn(*args, **kwargs):
                code = ('import json,sys,time; request=json.load(sys.stdin); '
                        'assert request == {"text":"Kalimat asli.", '
                        '"delivery":{"speed":0.94,"silence_duration":0.42}}; ')
                code += 'time.sleep(30)' if cancel else 'sys.stdout.buffer.write(' + repr(fixtures.fixture_wav()) + ')'
                child = await original_spawn(sys.executable, '-I', '-c', code, **kwargs)
                children.append(child)
                ready.set()
                return child
            with patch('voice_app.speech.asyncio.create_subprocess_exec', spawn):
                task = asyncio.create_task(speech.synthesize('Kalimat asli.', delivery=FakeDelivery()))
                await asyncio.wait_for(ready.wait(), 2)
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertEqual(await task, fixtures.fixture_wav())
            self.assertIsNotNone(children[0].returncode)
            self.assertFalse(speech._local_slot.locked())

    async def test_delivery_validation_precedes_spawn_without_weakening_text_guards(self):
        speech = self.make_speech()
        invalid = [FakeDelivery(speed=True), FakeDelivery(speed=float('nan')),
                   FakeDelivery(silence_duration=0.51)]
        extra = FakeDelivery()
        extra.controls['emotion'] = 'sad'
        invalid.append(extra)
        with patch.object(speech, '_local_request', return_value=fixtures.fixture_wav()) as process:
            for delivery in invalid:
                with self.subTest(controls=delivery.controls), self.assertRaises(ValueError):
                    await speech.synthesize('Halo.', delivery=delivery)
            for text in ('x' * 801, '🙂' * 801, '999999999 ' * 80, '<angry>Halo'):
                with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                    await speech.synthesize(text, delivery=FakeDelivery())
            process.assert_not_called()

    async def test_delivery_preserves_pronunciation_and_exact_source_ack(self):
        from voice_app.session_core import Session
        from voice_app.speech import normalize_pronunciation
        speech = self.make_speech()
        delivery = FakeDelivery()
        text = '  API 21 pada 2026-10-27, Rp21.000.  ' * 15
        seen = []
        async def process(action, payload, limit):
            seen.append(json.loads(payload))
            return fixtures.fixture_wav()
        session = Session()
        session.start()
        token = session.submit('Pertanyaan asli.')
        session.response_text(token, text)
        with patch.object(speech, '_local_request', process):
            parts = [item async for item in speech.synthesize_chunks(text, delivery=delivery)]
        for (source, audio, _), request in zip(parts, seen, strict=True):
            self.assertEqual(request, {'text': normalize_pronunciation(source.strip()),
                                       'delivery': delivery.controls})
            self.assertTrue(session.response_audio(token, audio, source))
            chunk = session.drain_audio(token)
            self.assertTrue(session.ack_played(token, chunk.chunk_id))
        self.assertEqual(session.history[-1].text, text)
        self.assertEqual(session.history[-1].heard_text, text)
        self.assertEqual(delivery.calls, 1)

    async def test_per_call_delivery_is_numeric_only_and_never_changes_source(self):
        speech = self.make_speech()
        self.assertIn('delivery', inspect.signature(speech.synthesize).parameters)
        self.assertTrue(speech.delivery_controls_supported)
        text = 'Sedih sekali mendengarnya. Teks asli tetap utuh.'
        first, second = FakeDelivery(), FakeDelivery(1.1, 0.1)
        seen = []
        async def process(action, payload, limit):
            seen.append(json.loads(payload))
            await asyncio.sleep(0)
            return fixtures.fixture_wav()
        with patch.object(speech, '_local_request', process):
            await asyncio.gather(speech.synthesize(text, delivery=first),
                                 speech.synthesize(text, delivery=second))
            await speech.synthesize(text)
        self.assertEqual(seen, [{'text': text, 'delivery': first.controls},
                                {'text': text, 'delivery': second.controls}, {'text': text}])
        self.assertEqual((first.calls, second.calls), (1, 1))
        self.assertEqual(speech.tts_identity['voice'], 'M1')
        self.assertEqual(speech.tts_identity['language'], 'id')

    async def test_chunk_delivery_snapshot_is_stable_across_prefetch_and_other_calls(self):
        speech = self.make_speech()
        self.assertIn('delivery', inspect.signature(speech.synthesize_chunks).parameters)
        delivery = FakeDelivery()
        original = delivery.controls.copy()
        text = '  Kalimat dinamis tetap asli. ' * 30
        seen = []
        async def process(action, payload, limit):
            seen.append(json.loads(payload))
            await asyncio.sleep(0)
            return fixtures.fixture_wav()
        with patch.object(speech, '_local_request', process):
            chunks = speech.synthesize_chunks(text, delivery=delivery)
            first = await anext(chunks)
            delivery.controls.update(speed=1.1, silence_duration=0.1)
            prefetch = asyncio.create_task(anext(chunks))
            await speech.synthesize('Permintaan lain.', delivery=FakeDelivery(1.02, 0.2))
            parts = [first, await prefetch] + [item async for item in chunks]
        self.assertGreater(len(parts), 2)
        self.assertEqual(''.join(item[0] for item in parts), text)
        self.assertEqual([item[2] for item in parts], [False] * (len(parts)-1) + [True])
        self.assertEqual(delivery.calls, 1)
        for request in seen:
            if request['text'] != 'Permintaan lain.':
                self.assertEqual(request['delivery'], original)
                self.assertEqual(set(request), {'text', 'delivery'})

    async def test_old_opening_trigger_is_always_fully_synthesized(self):
        def forbidden(*args):
            raise AssertionError('Cached-opening runtime must not be consulted')
        removed = types.SimpleNamespace(ApprovedOpeningFailure=RuntimeError,
                                        approved_prefix=forbidden, combine=forbidden)
        speech = self.make_speech()
        for text in ('Sedih sekali mendengarnya.',
                     'Sedih sekali mendengarnya. Saya tetap menemanimu.',
                     'Balasan biasa tanpa pembukaan.'):
            with self.subTest(text=text), patch.dict('sys.modules',
                    {'voice_app.approved_empathy': removed}), patch('voice_app.speech._bounded_process',
                    return_value=fixtures.fixture_wav()) as process:
                audio = await speech.synthesize(text)
                self.assertEqual(audio, fixtures.fixture_wav())
                self.assertEqual(process.call_count, 1)
                self.assertEqual(json.loads(process.call_args.args[1]), {'text': text})
