"""Core payload delta: pure source hashes and disposable installer roots only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from tests.setup import test_live_tts_install as fixture

ROOT = Path(__file__).resolve().parents[2]
CORE = {'sela_live/__init__.py', 'sela_live/audio/__init__.py', 'sela_live/audio/stt.py',
        'sela_live/audio/tts.py', 'voice_app/zipformer_state.py'}

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

class CorePayloadTests(unittest.TestCase):
    def test_core_installer_and_application_allowlists_match(self):
        from voice_app.sela_live_bridge import NATURAL_RUNTIME_FILES
        installer = fixture.module()
        self.assertTrue(CORE <= NATURAL_RUNTIME_FILES, 'application does not bind core')
        self.assertEqual(installer.NATURAL_RUNTIME_FILES, NATURAL_RUNTIME_FILES)
        for name in CORE:
            self.assertTrue(installer.allowed(name), name)
        self.assertFalse(installer.allowed('sela_live/unknown.py'))

    def test_builder_relocates_core_and_preserves_source_manifest(self):
        builder = load('core_builder', 'tools/build_live_runtime.py')
        before = (ROOT / 'sela_live_tts/upstream-manifest.json').read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            target, output = Path(tmp)/'target', Path(tmp)/'payload'
            report = builder.build_runtime(ROOT, target, output, write=True)
            self.assertTrue(CORE <= set(report['files']), 'builder omits core')
            data = json.loads((output/'sela_live_tts/upstream-manifest.json').read_bytes())
            self.assertEqual(data['core_root'], str(target))
            for name in CORE:
                self.assertEqual((output/name).read_bytes(), (ROOT/name).read_bytes())
            self.assertFalse(target.exists())
        self.assertEqual((ROOT/'sela_live_tts/upstream-manifest.json').read_bytes(), before)

class CoreInstallerTests(fixture.InstallerTests):
    # Inherit existing fixture setup, not its test cases (loader filters below).
    def test_new_core_absence_archived_and_rollback_restores_absence(self):
        installer = self.natural_fixture()
        for name in CORE:
            self.plan['files'][name] = self.write(self.payload, name, b'# core fixture\n')
        self.refresh_natural_inventory()
        self.plan['live_env']['VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256'] = self.plan['files'][self.m.RUNTIME_MANIFEST]['sha256']
        self.approve()
        installer.plan = self.plan
        snapshot, records = installer.archive()
        for name in CORE:
            self.assertFalse(records['app'][name]['exists'])
        attempts = []
        def fail():
            self.hooks.events.append('restart')
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError('fixture restart failure')
        self.hooks.restart = fail
        result = installer.run(apply=True)
        self.assertEqual(result['status'], 'rolled_back')
        for name in CORE:
            self.assertFalse((self.root/name).exists())

def load_tests(loader, tests, pattern):
    return unittest.TestSuite([loader.loadTestsFromTestCase(CorePayloadTests),
        CoreInstallerTests('test_new_core_absence_archived_and_rollback_restores_absence')])
