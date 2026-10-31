"""Fixture-only worker tests: no native sessions or model output claims."""
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
import numpy as np


class ProcessorFixture:
    """Small shape fixture, not a replacement native tokenizer."""
    def __init__(self):
        self.calls = []
        self.tokens = 12
        self.bad_mask = False
        self.bad_ids = False
        self.bad_shape = False

    def __call__(self, texts, lang):
        self.calls.append((texts, lang))
        ids = np.ones((1, self.tokens), dtype=np.int64)
        mask = np.ones((1, 1, self.tokens), dtype=np.float32)
        if self.bad_mask:
            mask[0, 0, 0] = 0
        if self.bad_ids:
            ids[0, 0] = -1
        if self.bad_shape:
            mask = mask.reshape(1, self.tokens)
        return ids, mask


class DPFixture:
    def __init__(self):
        self.duration = np.array([2.1], dtype=np.float32)
        self.calls = []

    def run(self, outputs, inputs):
        self.calls.append((outputs, inputs))
        return [self.duration]


class EngineFixture:
    """Tiny PCM fixture exercises original guard; never model-generated audio."""
    def __init__(self):
        self.model = SimpleNamespace(text_processor=ProcessorFixture(), dp_ort=DPFixture())
        self.calls = []
        self.error = False
        self.samples = np.array([[0.0, 0.25, 0.0, -0.25]], dtype=np.float32)

    def synthesize(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.error:
            raise RuntimeError('fixture failure')
        return self.samples, np.array([2.0])

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / 'sela_live_tts/natural_worker.py'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError('fixture module specification')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NaturalWorkerTests(unittest.TestCase):
    def fixture_runtime(self, chunker=None):
        worker = load(WORKER, 'natural_worker_fixture')
        guard = load(ROOT / 'voice_app/supertonic3_worker.py', 'guard_fixture')
        cls = worker.controlled_runtime(guard, chunker or (lambda t, n: [t.strip()]))
        runtime = cls.__new__(cls)
        runtime.engine = EngineFixture()
        runtime.style = SimpleNamespace(dp=np.ones((1, 8, 16), dtype=np.float32))
        runtime.cold_load_seconds = 0
        runtime.calls = 0
        return worker, runtime

    def test_one_context_quality12_inside_original_guard(self):
        worker, runtime = self.fixture_runtime()
        original = runtime.engine.synthesize
        data, metrics = runtime.synthesize({'text':' Halo. '})
        self.assertTrue(data.startswith(b'RIFF'))
        import io
        import wave
        with wave.open(io.BytesIO(data), 'rb') as wav:
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
        np.testing.assert_array_equal(pcm, [0, 8192, 0, -8192])
        self.assertEqual(np.random.random(), np.random.RandomState(20261026).random())
        self.assertEqual(metrics['call'], 1)
        text, kwargs = runtime.engine.calls[0]
        self.assertEqual(text, ' Halo. ')
        self.assertEqual(kwargs, dict(voice_style=runtime.style, lang='id', total_steps=12,
                                     verbose=False, speed=1.05, max_chunk_length=800))
        self.assertEqual(runtime.engine.model.text_processor.calls, [(['Halo.'], 'id')])
        outputs, inputs = runtime.engine.model.dp_ort.calls[0]
        self.assertIsNone(outputs)
        self.assertIs(inputs['style_dp'], runtime.style.dp)
        self.assertEqual(inputs['text_ids'].shape, (1, 12))
        self.assertEqual(inputs['text_mask'].shape, (1, 1, 12))
        self.assertNotIn('synthesize', runtime.engine.__dict__)
        self.assertEqual(runtime.engine.synthesize, original)

    def test_preflight_rejects_excess_or_invalid_arrays_before_synthesis(self):
        for kind, value in [('tokens', 513), ('tokens', 0), ('bad_mask', True), ('bad_ids', True), ('bad_shape', True),
                            ('duration', 29.5), ('duration', 0), ('duration', -1),
                            ('duration', float('nan')), ('duration', float('inf')),
                            ('duration', [1, 2])]:
            with self.subTest(kind=kind, value=value):
                _, runtime = self.fixture_runtime()
                if kind == 'duration':
                    runtime.engine.model.dp_ort.duration = np.array(value, dtype=np.float32).reshape(-1)
                else:
                    setattr(runtime.engine.model.text_processor, kind, value)
                with self.assertRaises(ValueError):
                    runtime.synthesize({'text':'Halo.'})
                self.assertEqual(runtime.engine.calls, [])
                self.assertNotIn('synthesize', runtime.engine.__dict__)

    def test_chunker_exactly_one_context_and_same_source(self):
        for chunks in ([], ['Halo.', 'Dunia.'], ['Replaced.'], ['x' * 801]):
            with self.subTest(chunks=chunks):
                _, runtime = self.fixture_runtime(lambda t, n: chunks)
                with self.assertRaises(ValueError):
                    runtime.synthesize({'text':'Halo.'})
                self.assertEqual(runtime.engine.calls, [])

    def test_canonical_guard_arguments_only(self):
        worker = load(WORKER, 'worker_fixture')
        for changes in ({'speed':1.0}, {'speed':True}, {'total_steps':12},
                        {'total_steps':8.0}, {'verbose':0}, {'lang':'en'},
                        {'silence_duration':.3}, {'max_chunk_length':800}, {'extra':1}):
            with self.subTest(changes=changes):
                class Base:
                    def synthesize(self, request):
                        args = dict(voice_style=self.style, lang='id', total_steps=8, verbose=False, speed=1.05)
                        args.update(changes)
                        return self.engine.synthesize(request['text'], **args)
                guard = SimpleNamespace(Runtime=Base)
                cls = worker.controlled_runtime(guard, lambda t, n: [t])
                runtime = cls()
                runtime.style = SimpleNamespace(dp=np.ones((1,8,16)))
                runtime.engine = EngineFixture()
                with self.assertRaises(ValueError):
                    runtime.synthesize({'text':'Halo.'})
                self.assertEqual(runtime.engine.calls, [])

    def test_restores_existing_override_on_engine_error(self):
        _, runtime = self.fixture_runtime()
        runtime.engine.error = True
        original = runtime.engine.synthesize
        runtime.engine.synthesize = original
        with self.assertRaises(RuntimeError):
            runtime.synthesize({'text':'Halo.'})
        self.assertIs(runtime.engine.__dict__['synthesize'], original)

    def test_original_guard_pcm_clipping_duration_empty_checks_still_active(self):
        for samples in (np.array([1.0]), np.array([0.]), np.array([float('nan')]),
                        np.ones(44100*30+1)*.1):
            with self.subTest(size=samples.size):
                _, runtime = self.fixture_runtime()
                runtime.engine.samples = samples
                with self.assertRaises(ValueError):
                    runtime.synthesize({'text':'Halo.'})
                self.assertNotIn('synthesize', runtime.engine.__dict__)

    def test_raw_input_limits_controls_tags_and_types(self):
        worker = load(WORKER, 'worker_fixture')
        for text in ('x'*801, '\U0001f600'*801, '', ' ', 'a\x00', '<breath>', '<laugh>', '<sigh>', 1, None):
            with self.subTest(text=str(text)[:20]), self.assertRaises(ValueError):
                worker.validate_text(text)
        self.assertEqual(worker.validate_text('x'*800), 'x'*800)

    def test_cli_exact_and_profiles(self):
        worker = load(WORKER, 'worker_fixture')
        profiles = ('conditioning_b',) + tuple(f'conditioning_b_{a}_{i}' for a in ('calm', 'active') for i in ('low', 'medium', 'high'))
        for profile in profiles:
            self.assertEqual(worker.parse_cli(['tts','/assets','/manifest',profile,'12']),
                             ('tts','/assets','/manifest',profile))
        for args in ([], ['check','/a','/m','original','12'], ['tts','/a','/m','conditioning_b_evil','12'],
                     ['tts','/a','/m','conditioning_b','8'], ['tts','/a','/m','conditioning_b','012'],
                     ['tts','a','/m','conditioning_b','12'], ['serve','/a','/m','conditioning_b','12']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                worker.parse_cli(args)

    def test_boundary_duration_and_tokens_are_accepted(self):
        _, runtime = self.fixture_runtime()
        runtime.engine.model.text_processor.tokens = 512
        runtime.engine.model.dp_ort.duration = np.array([28 * 1.05], dtype=np.float64)
        runtime.synthesize({'text':'Halo.'})
        self.assertEqual(len(runtime.engine.calls), 1)

    def test_real_pinned_chunker_preprocessing_correspondence(self):
        worker = load(WORKER, 'worker_fixture')
        data, _ = worker.verify_native()
        utils = load(Path(data['sdk_root']) / 'utils.py', 'sdk_utils_fixture')
        _, runtime = self.fixture_runtime(utils.chunk_text)
        runtime.synthesize({'text':' Halo.  Dunia! '})
        self.assertEqual(runtime.engine.model.text_processor.calls, [(['Halo. Dunia!'], 'id')])
        self.assertEqual(runtime.engine.calls[0][0], ' Halo.  Dunia! ')
        _, runtime = self.fixture_runtime(utils.chunk_text)
        with self.assertRaises(ValueError):
            runtime.synthesize({'text':'Halo.\n\nDunia!'})
        self.assertEqual(runtime.engine.calls, [])

    def test_isolated_standalone_rejects_cli_without_native_import(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-I', '-B', str(WORKER), 'tts', '/a', '/m',
                                 'conditioning_b', '8'], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(result.stderr, b'supertonic3_failure=asset_error\n')
        code = ("import importlib.util,sys; "
                f"s=importlib.util.spec_from_file_location('fixture',{str(WORKER)!r}); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.verify_native(); "
                "bad=set(sys.modules)&{'supertonic','numpy','onnxruntime'}; "
                "sys.exit(bool(bad))")
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pinned_sources_verified_before_guard_import(self):
        from unittest.mock import patch
        worker = load(WORKER, 'worker_fixture')
        events = []
        real_load = worker.load_module
        def traced(name, path):
            events.append(path.name)
            return real_load(name, path)
        with patch.object(worker, 'load_module', side_effect=traced):
            data, guard = worker.verify_native()
        self.assertEqual(events, ['source_pins.py', 'supertonic3_worker.py'])
        self.assertEqual(data['guard_python_files']['supertonic3_worker.py'], worker.GUARD_SHA)
        self.assertTrue(hasattr(guard, 'main'))
        with patch.object(worker, 'MANIFEST_SHA', '0'*64), patch.object(worker, 'load_module', side_effect=traced):
            events.clear()
            with self.assertRaises(ValueError):
                worker.verify_native()
            self.assertEqual(events, ['source_pins.py'])
        with patch.object(worker, 'PIN_HELPER_SHA', '0'*64), patch.object(worker, 'load_module', side_effect=traced):
            events.clear()
            with self.assertRaises(ValueError):
                worker.verify_native()
            self.assertEqual(events, [])

    def test_sdk_drift_fails_before_guard_import(self):
        from unittest.mock import patch
        worker = load(WORKER, 'worker_fixture')
        events = []
        real_load = worker.load_module
        def corrupted_sdk_fixture(name, path):
            events.append(path.name)
            module = real_load(name, path)
            if path.name == 'source_pins.py':
                original_read = module._read
                def read_fixture(target):
                    if Path(target).name == 'core.py':
                        return b'fixture source drift, never executed'
                    return original_read(target)
                module._read = read_fixture
            return module
        with patch.object(worker, 'load_module', side_effect=corrupted_sdk_fixture):
            with self.assertRaises(ValueError):
                worker.verify_native()
        self.assertEqual(events, ['source_pins.py'])

    def test_main_delegates_check_to_unchanged_guard_main(self):
        from unittest.mock import patch
        worker = load(WORKER, 'worker_fixture')
        events = []
        class Base:
            pass
        guard = SimpleNamespace(Runtime=Base, validate_request=lambda r: r['text'])
        def guard_main():
            events.append(tuple(worker.sys.argv[1:]))
            self.assertEqual(guard.validate_request({'text':'Halo.'}), 'Halo.')
            with self.assertRaises(ValueError):
                guard.validate_request({'text':'Halo.', 'delivery':{'speed':1.05}})
        guard.main = guard_main
        guard.emit_failure = lambda reason: events.append(reason)
        guard.failure_reason = lambda exc: 'native_error'
        original_main = guard.main
        with patch.object(worker, 'verify_native', return_value=({'sdk_root':'/fixture/sdk', 'guard_root':'/fixture/guard'}, guard)), patch.object(worker.sys, 'argv', ['worker','check','/assets','/manifest','conditioning_b','12']), patch.object(worker.sys, 'path', list(worker.sys.path)):
            worker.main()
        self.assertIs(guard.main, original_main)
        self.assertEqual(events, [('check','/assets','/manifest','conditioning_b')])

    def test_text_only_request_before_original_runtime(self):
        self.assertTrue(WORKER.exists(), 'natural worker not implemented')
        worker = load(WORKER, 'natural_worker_fixture')
        calls = []
        class Base:
            def synthesize(self, request):
                calls.append(request)
        guard = type('Guard', (), {'Runtime': Base, 'validate_request': staticmethod(lambda r: r['text'])})
        runtime = worker.controlled_runtime(guard, lambda t, n: [t])()
        for request in ({'text':'Halo', 'delivery':{}}, {'text':'<breath>'}, {}, {'text': 'x'*801}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                runtime.synthesize(request)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
