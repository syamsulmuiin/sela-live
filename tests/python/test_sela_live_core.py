"""Integrated core contracts; no models, network or native sessions."""
import importlib.util
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

class CoreSTTTests(unittest.TestCase):
    def test_truthful_core_identity(self):
        import sela_live
        self.assertEqual(getattr(sela_live, 'CAPABILITIES', {}).get('version'), '0.1.0')
        self.assertFalse(sela_live.CAPABILITIES['trained_emotion'])
        self.assertFalse(sela_live.CAPABILITIES['multilingual_stt'])
        self.assertEqual(sela_live.CAPABILITIES['audio_execution'], 'local_server')

    def test_state_owned_by_core_and_legacy_identity(self):
        self.assertTrue((ROOT / 'sela_live/audio/stt.py').is_file(), 'owned STT core missing')
        from sela_live.audio.stt import ZipformerState
        from voice_app.zipformer_state import ZipformerState as Legacy
        self.assertIs(ZipformerState, Legacy)
        self.assertEqual(ZipformerState.__module__, 'sela_live.audio.stt')

    def test_isolated_legacy_import_uses_owned_class_without_native(self):
        code = (f"import sys; sys.path.insert(0, {str(ROOT / 'voice_app')!r}); "
                "from zipformer_state import ZipformerState; "
                "assert ZipformerState.__module__ == 'sela_live.audio.stt'; "
                "assert not set(sys.modules)&{'numpy','onnxruntime','sherpa_onnx'}")
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

class CoreTTSTests(unittest.TestCase):
    def test_core_tamper_fails_before_guard_import_in_isolated_modes(self):
        import hashlib
        import json
        import shutil
        import tempfile
        for mode in ([], ['-O']):
            for relative in ('sela_live/audio/tts.py', 'sela_live/audio/stt.py',
                             'sela_live/__init__.py', 'sela_live/audio/__init__.py',
                             'voice_app/zipformer_state.py'):
                with self.subTest(mode=mode, relative=relative), tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp)/'app'
                    target.mkdir()
                    builder_spec = importlib.util.spec_from_file_location('builder', ROOT/'tools/build_live_runtime.py')
                    builder = importlib.util.module_from_spec(builder_spec)
                    builder_spec.loader.exec_module(builder)
                    output = Path(tmp)/'payload'
                    builder.build_runtime(ROOT, target, output, write=True)
                    shutil.copytree(output, target, dirs_exist_ok=True)
                    (target/relative).write_text("raise RuntimeError('TAMPER EXECUTED')\n")
                    code = ("import importlib.util; "
                        f"s=importlib.util.spec_from_file_location('worker',{str(target/'sela_live_tts/natural_worker.py')!r}); "
                        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                        "m.verify_native()")
                    result = subprocess.run([sys.executable, '-I', '-B', *mode, '-c', code], capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b'asset_error', result.stderr)
                    self.assertNotIn(b'TAMPER EXECUTED', result.stderr)

    def test_native_compatibility_delegates_owned_policy(self):
        self.assertTrue((ROOT / 'sela_live/audio/tts.py').is_file(), 'owned TTS policy missing')
        from sela_live.audio import tts
        from sela_live_tts import natural_worker as worker
        from types import SimpleNamespace
        cls = worker.controlled_runtime(SimpleNamespace(Runtime=object), lambda t, n: [t])
        self.assertEqual(cls.__module__, '_sela_live_verified_tts')
        self.assertEqual(tts.validate_text('Halo.'), worker.validate_text('Halo.'))
        with self.assertRaises(ValueError):
            tts.validate_text('<emotion>')

if __name__ == '__main__':
    unittest.main()
