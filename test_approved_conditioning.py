"""Approved fixed conditioning contract; no heavy native synthesis."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from voice_app import supertonic3_worker as worker

STAGE = Path(__file__).resolve().parent
MODEL = Path('/root/voice-lab/autonomous/expressive-tts')
PROFILE_SHA = '4f52698e957ae4d15d23c7208c26b298d56f70647083d61c9ff518c4d374d828'


def helper():
    path = STAGE / 'voice_app/approved_conditioning.py'
    spec = importlib.util.spec_from_file_location('approved_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApprovedAssets(unittest.TestCase):
    def test_exact_profile_and_bound_manifest_relocate_without_experiment_dependency(self):
        self.assertTrue((STAGE / 'voice_app/approved_conditioning.py').is_file(), 'approved profile validator missing')
        m = helper()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'installed'; (root / 'voice_app').mkdir(parents=True)
            shutil.copy(STAGE / 'voice_app/approved_conditioning.py', root / 'voice_app')
            shutil.copytree(STAGE / 'voice_assets', root / 'voice_assets')
            spec = importlib.util.spec_from_file_location('relocated_approved', root / 'voice_app/approved_conditioning.py')
            relocated = importlib.util.module_from_spec(spec); spec.loader.exec_module(relocated)
            path = relocated.approved_profile(MODEL / 'assets', MODEL / 'assets-manifest.json')
            self.assertEqual(path, root / 'voice_assets/conditioning-b/profile.json')
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), PROFILE_SHA)
            data = json.loads(path.read_bytes())
            base = json.loads((MODEL / 'assets/voice_styles/M1.json').read_bytes())
            self.assertEqual(data['style_dp'], base['style_dp'])
            provenance = json.loads(path.with_name('manifest.json').read_bytes())
            self.assertEqual(provenance['community']['revision'], 'e3a92e1473b85426b220cafdefa98043228218fb')
            self.assertEqual(provenance['coefficient'], 0.4)
            self.assertFalse(provenance['native_emotion_conditioning'])
            self.assertEqual(m.PROFILE_SHA256, PROFILE_SHA)


class AssetRejection(unittest.TestCase):
    def test_missing_corrupt_mismatched_symlink_assets_fail_closed(self):
        m = helper()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'app'; (root / 'voice_app').mkdir(parents=True)
            shutil.copytree(STAGE / 'voice_assets', root / 'voice_assets')
            assets = Path(tmp) / 'model'; (assets / 'voice_styles').mkdir(parents=True)
            shutil.copy(MODEL / 'assets/voice_styles/M1.json', assets / 'voice_styles/M1.json')
            manifest = Path(tmp) / 'model-manifest.json'; shutil.copy(MODEL / 'assets-manifest.json', manifest)
            candidates = [root / 'voice_assets/conditioning-b/profile.json', root / 'voice_assets/conditioning-b/manifest.json',
                          assets / 'voice_styles/M1.json', manifest]
            with patch.object(m, '__file__', str(root / 'voice_app/approved_conditioning.py')):
                for file in candidates:
                    original = file.read_bytes()
                    for mode in ('missing', 'corrupt', 'symlink'):
                        with self.subTest(file=file.name, mode=mode):
                            file.unlink()
                            if mode == 'corrupt': file.write_bytes(original + b' ')
                            if mode == 'symlink':
                                target = Path(tmp) / 'target'; target.write_bytes(original); file.symlink_to(target)
                            try:
                                with self.assertRaisesRegex(ValueError, '^asset_error$'):
                                    m.approved_profile(assets, manifest)
                            finally:
                                if file.exists() or file.is_symlink(): file.unlink()
                                file.write_bytes(original)
                directory = root / 'voice_assets/conditioning-b'
                renamed = directory.with_name('actual'); directory.rename(renamed); directory.symlink_to(renamed, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, '^asset_error$'): m.approved_profile(assets, manifest)

    def test_shapes_finite_values_source_and_unchanged_dp_are_explicitly_validated(self):
        import copy
        m = helper()
        self.assertTrue(hasattr(m, 'validate_profile'), 'semantic profile validation missing')
        original = json.loads((STAGE / 'voice_assets/conditioning-b/profile.json').read_bytes())
        base = json.loads((MODEL / 'assets/voice_styles/M1.json').read_bytes())
        m.validate_profile(original, base)
        mutations = [lambda d: d['style_ttl'].__setitem__('dims', [1, 1, 256]),
                     lambda d: d['style_ttl']['data'][0].pop(),
                     lambda d: d['style_ttl']['data'][0][0].__setitem__(0, float('nan')),
                     lambda d: d['style_ttl']['data'][0][0].__setitem__(0, float('inf')),
                     lambda d: d['style_ttl']['data'][0][0].__setitem__(0, True),
                     lambda d: d['style_dp']['data'][0][0].__setitem__(0, 0.5),
                     lambda d: d['metadata'].__setitem__('source_file', 'F1.wav'),
                     lambda d: d.__setitem__('extra', {}),
                     lambda d: d['style_dp'].__setitem__('type', 'float64')]
        for mutation in mutations:
            data = copy.deepcopy(original); mutation(data)
            with self.assertRaisesRegex(ValueError, '^asset_error$'): m.validate_profile(data, base)


class WorkerSelection(unittest.TestCase):
    def test_sdk_uses_approved_path_original_remains_asset_independent(self):
        from types import SimpleNamespace
        calls = []
        style = object()
        class Engine:
            sample_rate = 44100
            def __init__(self, **kwargs): calls.append(('engine', kwargs))
            def get_voice_style(self, name): calls.append(('original', name)); return style
            def get_voice_style_from_path(self, path): calls.append(('approved', path)); return style
        with patch.dict(sys.modules, {'supertonic':SimpleNamespace(TTS=Engine)}), patch.object(worker, 'check_assets'):
            self.assertIn('selected_profile', __import__('inspect').signature(worker.Runtime).parameters, 'runtime profile selection missing')
            runtime = worker.Runtime(MODEL / 'assets', MODEL / 'assets-manifest.json', 'conditioning_b')
            self.assertIs(runtime.style, style)
            self.assertEqual(calls[-1], ('approved', str(STAGE / 'voice_assets/conditioning-b/profile.json')))
            with patch.object(worker, 'selected_profile_path', return_value=None) as resolver:
                runtime = worker.Runtime(MODEL / 'assets', MODEL / 'assets-manifest.json')
                resolver.assert_called_once_with(MODEL / 'assets', MODEL / 'assets-manifest.json', 'original')
                self.assertEqual(calls[-1], ('original', 'M1'))
            with patch.object(worker, 'selected_profile_path', side_effect=worker.AssetFailure('asset_error')):
                before = len(calls)
                with self.assertRaises(worker.AssetFailure): worker.Runtime(MODEL / 'assets', MODEL / 'assets-manifest.json', 'conditioning_b')
                self.assertEqual(len(calls), before, 'bad selected assets must fail before SDK construction, never fallback')

    def test_isolated_cli_selection_and_legacy_check_safe_errors(self):
        args = [str(MODEL / 'venv/bin/python'), '-I', str(STAGE / 'voice_app/supertonic3_worker.py')]
        for extra in ([], ['original'], ['conditioning_b']):
            result = subprocess.run(args + ['check', str(MODEL / 'assets'), str(MODEL / 'assets-manifest.json')] + extra,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            identity = json.loads(result.stdout)
            self.assertTrue(identity['native_network_denied'])
            self.assertEqual(identity['voice'], 'M1')
            self.assertEqual(identity.get('voice_style_profile'), 'conditioning_b' if extra == ['conditioning_b'] else None)
        for bad in ('unknown', '/private/profile.json', '{"emotion":"happy"}'):
            for action in ('check', 'tts'):
                result = subprocess.run(args + [action, str(MODEL / 'assets'), str(MODEL / 'assets-manifest.json'), bad],
                                        input=b'{"text":"private text"}', capture_output=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
                self.assertEqual(result.stderr, b'supertonic3_failure=invalid_request\n')

    def test_selected_sdk_style_load_failures_are_safe_asset_errors(self):
        from types import SimpleNamespace
        class Engine:
            sample_rate = 44100
            def __init__(self, **kwargs): pass
            def get_voice_style_from_path(self, path): raise OSError('private path and text')
        with patch.dict(sys.modules, {'supertonic':SimpleNamespace(TTS=Engine)}), patch.object(worker, 'check_assets'):
            try:
                worker.Runtime(MODEL / 'assets', MODEL / 'assets-manifest.json', 'conditioning_b')
            except Exception as exc:
                self.assertEqual(worker.failure_reason(exc), 'asset_error')
                self.assertEqual(str(exc), 'asset_error')
            else:
                self.fail('selected style load error must not fall back')

    def test_original_resolver_needs_no_assets_selected_errors_are_safe(self):
        self.assertTrue(hasattr(worker, 'selected_profile_path'), 'safe fixed profile resolver missing')
        self.assertIsNone(worker.selected_profile_path('/missing', '/missing', 'original'))
        with self.assertRaises(worker.AssetFailure) as failure:
            worker.selected_profile_path('/missing/private', '/missing/private', 'conditioning_b')
        self.assertEqual(worker.failure_reason(failure.exception), 'asset_error')
        for key in ('profile', 'profile_path', 'voice_style', 'emotion'):
            with self.assertRaises(ValueError): worker.validate_request({'text':'Halo', key:'conditioning_b'})


class SpeechSelection(unittest.IsolatedAsyncioTestCase):
    async def test_selected_profile_both_commands_dynamic_payload_and_truthful_identity(self):
        import test_supertonic3_adapter as fixtures
        from voice_app.speech import Speech
        with fixtures.configured(VOICE_SUPERTONIC3_STYLE_PROFILE='conditioning_b') as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as check:
            speech = Speech(environ=env)
            self.assertEqual(speech.tts_identity.get('voice_style_profile'), 'conditioning_b', 'selected style identity missing')
            self.assertEqual(speech.tts_identity['style_label'], 'Conditioning B (+0.4)')
            self.assertIn('gaya B', speech.tts_label)
            self.assertEqual((speech.tts_identity['voice'], speech.tts_identity['language']), ('M1', 'id'))
            self.assertFalse(speech.delivery_controls_supported)
            self.assertEqual(check.call_args.args[0][-1], 'conditioning_b')
            self.assertEqual(check.call_args.args[0][-4], 'check')
        with patch('voice_app.speech._bounded_process', return_value=fixtures.fixture_wav()) as process:
            for text in ('Kabar baru, kita coba sekarang.', "Simpan src/style_b.json; panggil foo_bar(); kutip 'literal tetap'."):
                await speech.synthesize(text)
                self.assertEqual(process.call_args.args[0][-1], 'conditioning_b')
                self.assertEqual(process.call_args.args[0][-4], 'tts')
                self.assertEqual(json.loads(process.call_args.args[1]), {'text':text})
                self.assertTrue(process.call_args.kwargs['native_diagnostics'])
                self.assertNotIn('VOICE_SUPERTONIC3_STYLE_PROFILE', process.call_args.kwargs['env'])

    async def test_unknown_profile_and_other_backend_rejected_before_probe(self):
        from voice_app.speech import Speech
        for backend, profile in (('supertonic3','unknown'), ('supertonic3','/tmp/a.json'), ('edge','conditioning_b'), ('piper','conditioning_b'), ('espeak','conditioning_b'), ('edge','unknown')):
            with self.subTest(backend=backend, profile=profile), patch('voice_app.speech.subprocess.run') as check, patch('voice_app.speech._runtime_ready') as ready:
                with self.assertRaisesRegex(ValueError, 'STYLE_PROFILE'):
                    Speech(environ={'VOICE_TTS_BACKEND':backend, 'VOICE_SUPERTONIC3_STYLE_PROFILE':profile})
                check.assert_not_called(); ready.assert_not_called()

    async def test_selected_readiness_or_synthesis_failure_never_retries_original(self):
        import test_supertonic3_adapter as fixtures
        from voice_app.speech import Speech, NativeTTSFailure
        with fixtures.configured(VOICE_SUPERTONIC3_STYLE_PROFILE='conditioning_b') as env:
            with patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 1)) as check:
                with self.assertRaisesRegex(ValueError, 'no fallback'): Speech(environ=env)
                self.assertEqual(check.call_args.args[0][-1], 'conditioning_b')
                self.assertEqual(check.call_count, 1)
            with patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
                speech = Speech(environ=env)
        with patch('voice_app.speech._bounded_process', side_effect=NativeTTSFailure('asset_error', 1)) as process:
            with self.assertRaises(NativeTTSFailure): await speech.synthesize('Teks dinamis baru.')
            self.assertEqual(process.call_count, 1)
            self.assertEqual(process.call_args.args[0][-1], 'conditioning_b')


class NativeRegression(unittest.TestCase):
    def test_real_sdk_style_loader_and_seed_defaults_with_lightweight_array_fixture(self):
        code = r'''
import importlib.util, json, pathlib, sys
from unittest.mock import patch
import numpy as np
import supertonic
from supertonic import TTS
spec = importlib.util.spec_from_file_location('selected_worker', sys.argv[1])
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
profile = json.loads(pathlib.Path(sys.argv[2]).read_bytes())
calls = []
class Engine(TTS):
    def __init__(self, **kwargs): self.sample_rate = 44100
    def synthesize(self, text, **kwargs):
        calls.append((text, kwargs, np.random.randn(3)))
        np.testing.assert_array_equal(kwargs['voice_style'].ttl, np.asarray(profile['style_ttl']['data'], dtype=np.float32))
        np.testing.assert_array_equal(kwargs['voice_style'].dp, np.asarray(profile['style_dp']['data'], dtype=np.float32))
        return np.full(100, 0.1), 100 / 44100
with patch.object(supertonic, 'TTS', Engine):
    runtime = w.Runtime(pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4]), 'conditioning_b')
    for text in ("Teks dinamis pertama, src/style_b.json, foo_bar(), 'literal tetap'.", 'Teks berbeda tanpa pembukaan.'):
        runtime.synthesize({'text':text})
        assert calls[-1][0] == text
        assert calls[-1][1] == dict(voice_style=runtime.style, lang='id', total_steps=8, verbose=False, speed=1.05)
        expected = np.random.RandomState(20261026).randn(3)
        np.testing.assert_array_equal(calls[-1][2], expected)
assert runtime.calls == 2
print('REAL_SDK_PROFILE_LOADER_OK; array fixture only; no ONNX synthesis')
'''
        result = subprocess.run([str(MODEL / 'venv/bin/python'), '-I', '-c', code,
                                 str(STAGE / 'voice_app/supertonic3_worker.py'),
                                 str(STAGE / 'voice_assets/conditioning-b/profile.json'),
                                 str(MODEL / 'assets'), str(MODEL / 'assets-manifest.json')],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'REAL_SDK_PROFILE_LOADER_OK', result.stdout)

    def test_relocated_isolated_worker_fails_closed_and_original_needs_no_new_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'installed'; (root / 'voice_app').mkdir(parents=True)
            for name in ('supertonic3_worker.py', 'approved_conditioning.py'):
                shutil.copy(STAGE / 'voice_app' / name, root / 'voice_app' / name)
            shutil.copytree(STAGE / 'voice_assets', root / 'voice_assets')
            args = [str(MODEL / 'venv/bin/python'), '-I', str(root / 'voice_app/supertonic3_worker.py'),
                    'check', str(MODEL / 'assets'), str(MODEL / 'assets-manifest.json')]
            result = subprocess.run(args + ['conditioning_b'], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('profile.json', 'manifest.json'):
                path = root / 'voice_assets/conditioning-b' / name
                original = path.read_bytes(); path.write_bytes(original + b' ')
                result = subprocess.run(args + ['conditioning_b'], capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b'')
                self.assertEqual(result.stderr, b'supertonic3_failure=asset_error\n')
                path.write_bytes(original)
            shutil.rmtree(root / 'voice_assets')
            (root / 'voice_app/approved_conditioning.py').unlink()
            result = subprocess.run(args, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('voice_style_profile', json.loads(result.stdout))
            result = subprocess.run(args + ['conditioning_b'], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, b'supertonic3_failure=asset_error\n')


if __name__ == '__main__':
    unittest.main()
