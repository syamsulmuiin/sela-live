"""Offline payload contract tests; never import runtime or run inference."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / 'tools/build_live_runtime.py'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class RuntimePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.target = self.base / 'future-target'
        self.output = self.base / 'payload'
        self.raw = {}
        for rel in ('voice_app/approved_conditioning.py', 'voice_app/automatic_conditioning.py',
                    'voice_app/supertonic3_worker.py', 'sela_live_tts/__init__.py',
                    'sela_live_tts/source_pins.py', 'sela_live_tts/controls.py',
                    'sela_live_tts/admission.py', 'vendor/supertonic/__init__.py',
                    'vendor/supertonic/server/core.py'):
            self.put(rel, ('# ' + rel + '\n').encode())
        sdk = {p: sha(self.raw['vendor/supertonic/' + p]) for p in ('__init__.py', 'server/core.py')}
        self.manifest = {
            'sdk_root': '/old/sdk', 'sdk_commit': 'a' * 40, 'sdk_repository': 'official/repo',
            'sdk_python_files': sdk, 'guard_root': str(self.source / 'voice_app'),
            'guard_python_files': {p: sha(self.raw['voice_app/' + p]) for p in
                                   ('approved_conditioning.py', 'automatic_conditioning.py', 'supertonic3_worker.py')},
            'live_root': str(self.source / 'sela_live_tts'),
            'live_python_files': {p: sha(self.raw['sela_live_tts/' + p]) for p in
                                  ('__init__.py', 'source_pins.py', 'controls.py')}}
        self.put('sela_live_tts/upstream-manifest.json', json.dumps(self.manifest).encode())
        self.worker = ("MANIFEST_SHA='" + sha(self.raw['sela_live_tts/upstream-manifest.json']) +
                       "'\nPIN_HELPER_SHA='" + sha(self.raw['sela_live_tts/source_pins.py']) + "'\n# body stays exact\n").encode()
        self.put('sela_live_tts/worker.py', self.worker)
        self.put('vendor/supertonic-LICENSE.txt', b'MIT fixture license\n')
        self.provenance = {'repository': 'official/repo', 'commit': 'a' * 40, 'python_files': sdk,
                           'license': 'supertonic-LICENSE.txt',
                           'license_sha256': sha(self.raw['vendor/supertonic-LICENSE.txt'])}
        self.put('vendor/SUPERTONIC-PROVENANCE.json', json.dumps(self.provenance).encode())

    def put(self, relative, raw):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.raw[relative] = raw

    def api(self, **kwargs):
        self.assertTrue(BUILDER.is_file(), 'offline runtime builder is missing')
        spec = importlib.util.spec_from_file_location('runtime_builder', BUILDER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_runtime(self.source, self.target, self.output, **kwargs)

    def natural_bundle(self):
        for name in ('natural.py', 'utterance.py', 'duration.py', 'duration_worker.py'):
            self.put('sela_live_tts/' + name, ('# ' + name + '\n').encode())
        self.put('sela_live_tts/natural_worker.py', self.worker + b'# natural worker\n')

    def test_natural_relocation_inventory_and_external_binding(self):
        self.natural_bundle()
        original = self.raw['sela_live_tts/natural_worker.py']
        result = self.api(write=True, expected_natural_worker_sha256=sha(original))
        manifest = 'sela_live_tts/natural-runtime-manifest.json'
        inventory = json.loads((self.output / manifest).read_bytes())
        expected = {p: h for p, h in result['files'].items()
                    if p.endswith('.py') or p == 'sela_live_tts/upstream-manifest.json'}
        self.assertEqual(inventory, {'schema_version': 1, 'files': expected})
        self.assertNotIn(manifest, inventory['files'])
        transformed = original.replace(sha(self.raw_original_manifest()).encode(),
                                       result['manifest_sha256'].encode(), 1)
        self.assertEqual((self.output / 'sela_live_tts/natural_worker.py').read_bytes(), transformed)
        self.assertEqual(result['natural_worker_sha256'], sha(transformed))
        self.assertEqual(result['runtime_manifest_sha256'], result['files'][manifest])
        self.assertEqual(result['transforms']['natural_worker'], {
            'kind': 'MANIFEST_SHA_literal_only', 'source_sha256': sha(original),
            'output_sha256': sha(transformed)})
        self.assertEqual(result['source_files']['sela_live_tts/natural_worker.py'], sha(original))
        self.assertEqual((self.output / 'sela_live_tts/duration_worker.py').read_bytes(),
                         self.raw['sela_live_tts/duration_worker.py'])
        self.assertFalse(self.target.exists())

    def test_natural_partial_bundle_and_missing_external_binding_target_refused(self):
        self.natural_bundle()
        for name in ('natural.py', 'utterance.py', 'duration.py', 'duration_worker.py', 'natural_worker.py'):
            with self.subTest(missing=name):
                path = self.source / 'sela_live_tts' / name
                saved = path.read_bytes()
                path.unlink()
                with self.assertRaises(ValueError):
                    self.api(write=True)
                self.assertFalse(self.output.exists())
                path.write_bytes(saved)
        for name in ('natural.py', 'utterance.py', 'duration.py', 'duration_worker.py', 'natural_worker.py'):
            (self.source / 'sela_live_tts' / name).unlink()
        with self.assertRaises(ValueError):
            self.api(expected_natural_worker_sha256='0' * 64)

    def test_natural_hostile_pins_hash_drift_symlinks_and_cli_binding(self):
        self.natural_bundle()
        path = self.source / 'sela_live_tts/natural_worker.py'
        original = path.read_bytes()
        manifest_sha = sha(self.raw_original_manifest()).encode()
        helper_sha = sha(self.raw['sela_live_tts/source_pins.py']).encode()
        for raw in (original.replace(manifest_sha, b'0' * 64),
                    original.replace(helper_sha, b'0' * 64),
                    original.replace(b'PIN_HELPER_SHA=', b'OTHER='),
                    original + b"MANIFEST_SHA='" + manifest_sha + b"'\n",
                    original + b"PIN_HELPER_SHA='" + helper_sha + b"'\n",
                    original + b'# unreviewed body\n'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.api(write=True, expected_natural_worker_sha256=sha(original))
                self.assertFalse(self.output.exists())
        path.write_bytes(original)
        for name in ('natural_worker.py', 'natural.py', 'utterance.py', 'duration.py', 'duration_worker.py'):
            link = self.source / 'sela_live_tts' / name
            saved = link.read_bytes()
            outside = self.base / 'outside'
            outside.write_bytes(saved)
            link.unlink()
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                self.api(write=True)
            self.assertFalse(self.output.exists())
            link.unlink()
            link.write_bytes(saved)
        command = [sys.executable, '-B', str(BUILDER), '--source-app-root', str(self.source),
                   '--target-app-root', str(self.target), '--output-root', str(self.output),
                   '--expected-natural-worker-sha256', sha(original)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['source_files']['sela_live_tts/natural_worker.py'], sha(original))
        self.assertFalse(self.output.exists())
        rejected = subprocess.run(command[:-1] + ['0' * 64, '--write'], capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse(self.output.exists())

    def test_default_plan_has_exact_hashes_without_mutation(self):
        before = dict(self.raw)
        result = self.api()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(result['mode'], 'plan')
        self.assertEqual(result['target_app_root'], str(self.target))
        self.assertIn('sela_live_tts/admission.py', result['files'])
        for relative, raw in before.items():
            self.assertEqual((self.source / relative).read_bytes(), raw)
        self.assertEqual(result['files']['voice_app/approved_conditioning.py'],
                         sha(self.raw['voice_app/approved_conditioning.py']))

    def test_paths_and_symlink_ancestors_are_refused_in_plan(self):
        original_source, original_target, original_output = self.source, self.target, self.output
        alias = self.base / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        scenarios = (
            (original_source, original_target, original_source / 'payload'),
            (original_source, original_target, original_target / 'payload'),
            (original_source, original_target, original_target),
            (original_source, original_target, self.base),
            (original_source, original_target, Path('relative-output')),
            (original_source, Path(str(original_target) + '/../future-target'), original_output),
            (alias, original_target, original_output),
            (original_source, alias / 'future', original_output),
            (original_source, original_target, alias / 'out'),
        )
        for source, target, output in scenarios:
            with self.subTest(source=str(source), target=str(target), output=str(output)):
                self.source, self.target, self.output = source, target, output
                with self.assertRaises(ValueError):
                    self.api()
        self.source, self.target, self.output = original_source, original_target, original_output
        self.output.mkdir()
        with self.assertRaises(ValueError):
            self.api()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_symlink_source_files_and_directories_are_refused(self):
        for relative in ('sela_live_tts/controls.py', 'vendor/supertonic/__init__.py',
                         'voice_app/approved_conditioning.py', 'vendor/supertonic-LICENSE.txt'):
            with self.subTest(relative=relative):
                path = self.source / relative
                saved = path.read_bytes()
                copy = self.base / 'linked-file'
                copy.write_bytes(saved)
                path.unlink()
                path.symlink_to(copy)
                with self.assertRaises(ValueError):
                    self.api()
                path.unlink()
                path.write_bytes(saved)
        linked = self.source / 'vendor/supertonic/linked'
        linked.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.api()
        self.assertFalse(self.output.exists())

    def test_guard_and_live_pins_are_verified_before_copy(self):
        for relative in ('voice_app/approved_conditioning.py', 'voice_app/automatic_conditioning.py',
                         'voice_app/supertonic3_worker.py', 'sela_live_tts/source_pins.py',
                         'sela_live_tts/controls.py', 'sela_live_tts/__init__.py'):
            with self.subTest(relative=relative):
                path = self.source / relative
                original = path.read_bytes()
                path.write_bytes(original + b'# drift\n')
                with self.assertRaises(ValueError):
                    self.api(write=True)
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_worker_manifest_and_helper_bindings_must_be_unique_and_current(self):
        old_digest = sha(self.raw['sela_live_tts/upstream-manifest.json']).encode()
        helper_digest = sha(self.raw['sela_live_tts/source_pins.py']).encode()
        variants = (self.worker.replace(old_digest, b'0' * 64),
                    self.worker.replace(helper_digest, b'0' * 64),
                    self.worker + b"MANIFEST_SHA='" + old_digest + b"'\n",
                    self.worker + b"MANIFEST_SHA = '" + old_digest + b"'\n",
                    self.worker.replace(b'MANIFEST_SHA=', b'OTHER_SHA='),
                    self.worker + b"PIN_HELPER_SHA='" + helper_digest + b"'\n")
        for worker in variants:
            with self.subTest(worker=worker):
                self.put('sela_live_tts/worker.py', worker)
                with self.assertRaises(ValueError):
                    self.api(write=True)
                self.assertFalse(self.output.exists())
        self.put('sela_live_tts/worker.py', self.worker)

    def test_native_inventory_and_source_roots_cannot_redirect_guards(self):
        for key, value in (('guard_root', '/unrelated/voice_app'),
                           ('live_root', '/unrelated/sela_live_tts'),
                           ('guard_python_files', {'../escape.py': '0' * 64}),
                           ('live_python_files', {'source_pins.py': sha(self.raw['sela_live_tts/source_pins.py'])})):
            with self.subTest(key=key):
                changed = dict(self.manifest, **{key: value})
                raw = json.dumps(changed).encode()
                self.put('sela_live_tts/upstream-manifest.json', raw)
                self.put('sela_live_tts/worker.py', self.worker.replace(
                    sha(self.raw_original_manifest()).encode(), sha(raw).encode()))
                with self.assertRaises(ValueError):
                    self.api()

    def raw_original_manifest(self):
        return json.dumps(self.manifest).encode()

    def test_reviewed_worker_sha_excludes_unapproved_body_edits(self):
        approved = sha(self.worker)
        result = self.api(expected_worker_sha256=approved)
        self.assertEqual(result['source_files']['sela_live_tts/worker.py'], approved)
        self.assertEqual(result['source_app_root'], str(self.source))
        self.put('sela_live_tts/worker.py', self.worker + b'# not reviewed\n')
        with self.assertRaises(ValueError):
            self.api(write=True, expected_worker_sha256=approved)
        self.assertFalse(self.output.exists())

    def test_cli_plan_write_and_collision_are_machine_readable(self):
        command = [sys.executable, '-B', str(BUILDER), '--source-app-root', str(self.source),
                   '--target-app-root', str(self.target), '--output-root', str(self.output),
                   '--expected-worker-sha256', sha(self.worker)]
        plan = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(json.loads(plan.stdout)['mode'], 'plan')
        self.assertFalse(self.output.exists())
        written = subprocess.run(command + ['--write'], capture_output=True, text=True)
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertEqual(json.loads(written.stdout)['mode'], 'write')
        before = {str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        collision = subprocess.run(command + ['--write'], capture_output=True, text=True)
        self.assertEqual(collision.returncode, 2)
        self.assertIn('error', json.loads(collision.stderr))
        self.assertEqual(before, {str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
        self.assertFalse(self.target.exists())

    def test_vendor_validation_fails_closed_before_output(self):
        cases = ('missing', 'corrupt', 'extra', 'license', 'metadata', 'traversal')
        for case in cases:
            with self.subTest(case=case):
                path = self.source / 'vendor/supertonic/__init__.py'
                if case == 'missing':
                    path.unlink()
                elif case == 'corrupt':
                    path.write_bytes(b'corrupt')
                elif case == 'extra':
                    (self.source / 'vendor/supertonic/extra.py').write_bytes(b'extra')
                elif case == 'license':
                    (self.source / 'vendor/supertonic-LICENSE.txt').write_bytes(b'wrong')
                elif case == 'metadata':
                    metadata = dict(self.provenance, commit='b' * 40)
                    self.put('vendor/SUPERTONIC-PROVENANCE.json', json.dumps(metadata).encode())
                else:
                    metadata = dict(self.provenance, python_files={'../outside.py': '0' * 64})
                    self.put('vendor/SUPERTONIC-PROVENANCE.json', json.dumps(metadata).encode())
                with self.assertRaises(ValueError):
                    self.api(write=True)
                self.assertFalse(self.output.exists())
                path.write_bytes(b'# vendor/supertonic/__init__.py\n')
                (self.source / 'vendor/supertonic/extra.py').unlink(missing_ok=True)
                (self.source / 'vendor/supertonic-LICENSE.txt').write_bytes(b'MIT fixture license\n')
                self.put('vendor/SUPERTONIC-PROVENANCE.json', json.dumps(self.provenance).encode())

    def test_write_relocates_only_roots_and_worker_digest(self):
        self.target.mkdir()
        sentinel = self.target / 'do-not-touch'
        sentinel.write_bytes(b'production sentinel')
        result = self.api(write=True)
        manifest_raw = (self.output / 'sela_live_tts/upstream-manifest.json').read_bytes()
        relocated = json.loads(manifest_raw)
        for group, relative in (('guard', 'voice_app'), ('live', 'sela_live_tts'), ('sdk', 'vendor/supertonic')):
            self.assertEqual(relocated.pop(group + '_root'), str(self.target / relative))
        original = {k: v for k, v in self.manifest.items() if not k.endswith('_root')}
        self.assertEqual(relocated, original)
        expected_worker = self.worker.replace(sha(self.raw['sela_live_tts/upstream-manifest.json']).encode(),
                                              sha(manifest_raw).encode(), 1)
        self.assertEqual((self.output / 'sela_live_tts/worker.py').read_bytes(), expected_worker)
        self.assertEqual(result['worker_sha256'], sha(expected_worker))
        self.assertEqual(result['manifest_sha256'], sha(manifest_raw))
        self.assertEqual(result, json.loads((self.output / 'LIVE-RUNTIME-PACKAGE.json').read_bytes()))
        actual = {str(p.relative_to(self.output)): sha(p.read_bytes()) for p in self.output.rglob('*')
                  if p.is_file() and p.name != 'LIVE-RUNTIME-PACKAGE.json'}
        self.assertEqual(actual, result['files'])
        for relative, expected in result['files'].items():
            if relative not in ('sela_live_tts/worker.py', 'sela_live_tts/upstream-manifest.json'):
                self.assertEqual(expected, sha(self.raw[relative]))
        self.assertEqual(list(self.target.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'production sentinel')
        self.assertEqual(result['transforms']['worker']['kind'], 'MANIFEST_SHA_literal_only')


if __name__ == '__main__':
    unittest.main()
