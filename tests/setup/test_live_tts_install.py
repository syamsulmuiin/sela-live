"""Disposable roots only: no production service or inference operations."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / 'tools/install_live_tts.py'


def module():
    spec = importlib.util.spec_from_file_location('live_installer', SOURCE)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Hooks:
    def __init__(self, selectors):
        self.selectors = selectors
        self.events = []

    def observe(self):
        self.events.append('observe')
        return {'healthy': True, 'selectors': dict(self.selectors)}

    def stop(self):
        self.events.append('stop')

    def restart(self):
        self.events.append('restart')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root, self.units, self.payload = [self.base / n for n in ('app', 'units', 'payload')]
        for p in (self.root, self.units, self.payload):
            p.mkdir()
        self.m = module()

    def write(self, root, name, raw=b'old'):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        p.chmod(0o644)
        return {'sha256': self.m.sha(raw), 'mode': 0o644}

    def fixture(self):
        m = self.m
        guards = {n: self.write(self.root, n) for n in m.GUARDS}
        old = dict(guards)
        old['voice_app/speech.py'] = self.write(self.root, 'voice_app/speech.py')
        old['web/app.js'] = self.write(self.root, 'web/app.js')
        old['voice_assets/profile.json'] = self.write(self.root, 'voice_assets/profile.json')
        python = self.base / 'audio/bin/python'
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b'fixture executable, never run')
        python.chmod(0o755)
        selectors = {'VOICE_SUPERTONIC3_PYTHON': str(python), 'VOICE_STT_BACKEND': 'local', 'VOICE_TTS_BACKEND': 'supertonic3',
                     'VOICE_SUPERTONIC3_STYLE_PROFILE': 'conditioning_b',
                     'VOICE_AUTOMATIC_CONDITIONING': '1', 'VOICE_ZIPFORMER_STATEFUL': '1',
                     'VOICE_LOCAL_ASR_ENGINE': 'zipformer_id_int8',
                     'VOICE_MODEL': 'gpt-6.1-sol',
                     'VOICE_PUBLIC_ORIGIN': 'https://voice.kasirdigital.web.id'}
        self.hooks = Hooks(selectors)
        baseline = {'app': old, 'units': {'voice-lab.service': self.write(self.units, 'voice-lab.service')},
                    'selectors': selectors, 'trees': ['voice_app', 'web', 'voice_assets']}
        for n in guards:
            self.write(self.payload, n)
        files = {n: self.write(self.payload, n, b'new') for n in (
            'voice_app/speech.py', 'voice_app/sela_live_bridge.py',
            'sela_live_tts/__init__.py', 'sela_live_tts/worker.py',
            'sela_live_tts/upstream-manifest.json', 'vendor/supertonic/sdk.py',
            'vendor/supertonic-LICENSE.txt', 'vendor/SUPERTONIC-PROVENANCE.json')}
        self.baseline = self.base / 'baseline.json'
        self.baseline.write_text(json.dumps(baseline))
        self.runtime = self.base / 'runtime-review.json'
        self.runtime.write_text(json.dumps({'verdict': 'approved', 'reviewer': 'runtime-reviewer'}))
        self.plan = {'schema_version': 1, 'baseline_path': str(self.baseline),
                     'baseline_sha256': m.sha(self.baseline.read_bytes()),
                     'runtime_review_path': str(self.runtime), 'runtime_review_sha256': m.sha(self.runtime.read_bytes()),
                     'payload_root': str(self.payload), 'files': files, 'preserved': guards,
                     'live_env': {'VOICE_SELA_LIVE_TTS': '1', 'VOICE_SELA_LIVE_PYTHON': str(python),
                                  'VOICE_SELA_LIVE_WORKER': str(self.root / 'sela_live_tts/worker.py'),
                                  'VOICE_SELA_LIVE_LOCK': str(self.root / '.runtime/live-tts.lock'),
                                  'VOICE_SELA_LIVE_WORKER_SHA256': files['sela_live_tts/worker.py']['sha256']}}
        self.approve()
        return m.Installer(self.plan, root=self.root, units=self.units,
                           snapshots=self.base / 'snapshots', hooks=self.hooks)

    def approve(self):
        p = self.plan
        p.pop('approval', None)
        p['approval'] = {'reviewer': 'installer-reviewer', 'verdict': 'approved',
                         'artifact_modes': {name: path.stat().st_mode & 0o7777 for name, path in {
                             'installer': SOURCE, 'testsource': Path(__file__), 'baseline': self.baseline,
                             'runtime_review': self.runtime, 'payload_root': self.payload}.items()},
                         'plan_mode': 0o644,
                         'installer_sha256': self.m.sha(SOURCE.read_bytes()),
                         'testsource_sha256': self.m.sha(Path(__file__).read_bytes()),
                         'baseline_sha256': p['baseline_sha256'],
                         'runtime_review_sha256': p['runtime_review_sha256'],
                         'payload_sha256': self.m.sha(self.m.canonical({'files': p['files'], 'preserved': p['preserved'], 'live_env': p['live_env']})),
                         'plan_sha256': self.m.sha(self.m.canonical(p))}

    def natural_fixture(self):
        installer = self.fixture()
        names = ('backend.py', 'admission.py', 'controls.py', 'source_pins.py',
                 'natural.py', 'utterance.py', 'duration.py', 'natural_worker.py', 'duration_worker.py')
        for name in names:
            relative = 'sela_live_tts/' + name
            self.plan['files'][relative] = self.write(self.payload, relative, ('# ' + name).encode())
        (self.payload / 'vendor/supertonic/sdk.py').unlink()
        del self.plan['files']['vendor/supertonic/sdk.py']
        for name in ('__init__.py', 'cli.py', 'config.py', 'core.py', 'loader.py', 'pipeline.py', 'utils.py',
                     'server/__init__.py', 'server/app.py', 'server/audio.py', 'server/routes.py',
                     'server/schemas.py', 'server/styles_store.py'):
            relative = 'vendor/supertonic/' + name
            self.plan['files'][relative] = self.write(self.payload, relative, ('# ' + name).encode())
        source = 'sela_live_tts/upstream-manifest.json'
        self.plan['files'][source] = self.write(self.payload, source, self.m.canonical({
            'live_root': str(self.root / 'sela_live_tts'), 'guard_root': str(self.root / 'voice_app'),
            'sdk_root': str(self.root / 'vendor/supertonic')}))
        for relative in self.m.CORE_FILES:
            self.plan['files'][relative] = self.write(self.payload, relative, ('# ' + relative).encode())
        self.refresh_natural_inventory()
        env = self.plan['live_env']
        env.update({'VOICE_SELA_LIVE_POLICY': 'natural_v1',
                    'VOICE_SELA_LIVE_WORKER': str(self.root / 'sela_live_tts/natural_worker.py'),
                    'VOICE_SELA_LIVE_WORKER_SHA256': self.plan['files']['sela_live_tts/natural_worker.py']['sha256'],
                    'VOICE_SELA_LIVE_DURATION_WORKER': str(self.root / 'sela_live_tts/duration_worker.py'),
                    'VOICE_SELA_LIVE_DURATION_WORKER_SHA256': self.plan['files']['sela_live_tts/duration_worker.py']['sha256'],
                    'VOICE_SELA_LIVE_SOURCE_MANIFEST_SHA256': self.plan['files'][source]['sha256'],
                    'VOICE_SELA_LIVE_RUNTIME_MANIFEST': str(self.root / 'sela_live_tts/natural-runtime-manifest.json'),
                    'VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256': self.plan['files']['sela_live_tts/natural-runtime-manifest.json']['sha256']})
        baseline = json.loads(self.baseline.read_bytes())
        baseline['selectors']['VOICE_EXPERIMENTAL_DELIVERY_PROSODY'] = '0'
        baseline['selectors'].update({k: v for k, v in self.plan['live_env'].items()
                                      if k in ('VOICE_SELA_LIVE_TTS', 'VOICE_SELA_LIVE_PYTHON',
                                               'VOICE_SELA_LIVE_LOCK')})
        old_worker = self.write(self.root, 'sela_live_tts/worker.py', b'old quality12 worker')
        baseline['app']['sela_live_tts/worker.py'] = old_worker
        baseline['selectors'].update({
            'VOICE_SELA_LIVE_WORKER': str(self.root / 'sela_live_tts/worker.py'),
            'VOICE_SELA_LIVE_WORKER_SHA256': old_worker['sha256']})
        baseline['units'][self.m.DROPIN] = self.write(self.units, self.m.DROPIN, b'[Service]\n# quality12\n')
        self.baseline.write_text(json.dumps(baseline))
        self.hooks.selectors = dict(baseline['selectors'])
        self.plan['baseline_sha256'] = self.m.sha(self.baseline.read_bytes())
        self.approve()
        installer.plan = self.plan
        return installer

    def refresh_natural_inventory(self, value=None):
        records = {**self.plan['files'], **self.plan['preserved']}
        expected = {n: r['sha256'] for n, r in records.items()
                    if n in self.m.GUARDS or n in self.m.CORE_FILES or n == 'sela_live_tts/upstream-manifest.json'
                    or n.endswith('.py') and n.startswith(('sela_live_tts/', 'vendor/supertonic/'))}
        raw = self.m.canonical({'schema_version': 1, 'files': expected} if value is None else value)
        self.plan['files']['sela_live_tts/natural-runtime-manifest.json'] = self.write(
            self.payload, 'sela_live_tts/natural-runtime-manifest.json', raw)

    def test_natural_exact_plan_is_read_only_and_preserves_legacy_migration(self):
        installer = self.natural_fixture()
        self.assertEqual(installer.run(), {'status': 'ready', 'applied': False})
        self.assertEqual(self.hooks.events, ['observe'])
        self.assertFalse((self.base / 'snapshots').exists())
        self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'old')

    def test_natural_manifest_requires_exact_inventory_before_archive(self):
        import shutil
        cases = ('missing', 'extra', 'traversal', 'absolute', 'duplicate', 'top_duplicate',
                 'schema', 'boolean_schema', 'extra_field', 'wrong_digest', 'invalid_digest',
                 'symlink', 'tamper', 'missing_payload', 'extra_payload')
        for case in cases:
            with self.subTest(case=case):
                installer = self.natural_fixture()
                name = 'sela_live_tts/natural-runtime-manifest.json'
                data = json.loads((self.payload / name).read_bytes())
                key = 'sela_live_tts/natural.py'
                if case in ('missing', 'traversal', 'absolute'):
                    value = data['files'].pop(key)
                    if case != 'missing':
                        data['files']['../natural.py' if case == 'traversal' else '/natural.py'] = value
                elif case == 'extra':
                    data['files']['sela_live_tts/extra.py'] = 'a' * 64
                elif case == 'schema':
                    data['schema_version'] = 2
                elif case == 'boolean_schema':
                    data['schema_version'] = True
                elif case == 'extra_field':
                    data['other'] = 1
                elif case in ('wrong_digest', 'invalid_digest'):
                    data['files'][key] = 'a' * 64 if case == 'wrong_digest' else True
                elif case == 'missing_payload':
                    del self.plan['files'][key]
                    (self.payload / key).unlink()
                    data['files'].pop(key)
                elif case == 'extra_payload':
                    extra = 'sela_live_tts/extra.py'
                    self.plan['files'][extra] = self.write(self.payload, extra, b'extra')
                    data['files'][extra] = self.plan['files'][extra]['sha256']
                raw = self.m.canonical(data)
                if case == 'duplicate':
                    raw = raw.replace(b'"files":{', b'"files":{"sela_live_tts/natural.py":"' + data['files'][key].encode() + b'",', 1)
                elif case == 'top_duplicate':
                    raw = b'{"schema_version":1,' + raw[1:]
                self.plan['files'][name] = self.write(self.payload, name, raw)
                self.plan['live_env']['VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256'] = self.m.sha(raw)
                self.approve()
                installer.plan = self.plan
                if case in ('symlink', 'tamper'):
                    path = self.payload / key
                    if case == 'symlink':
                        path.unlink()
                        path.symlink_to(self.payload / 'sela_live_tts/worker.py')
                    else:
                        path.write_bytes(b'tampered')
                with self.assertRaises((ValueError, OSError, KeyError)):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
                self.assertFalse((self.base / 'snapshots').exists())
                for path in (self.root, self.units, self.payload):
                    shutil.rmtree(path)
                    path.mkdir()

    def test_natural_process_observation_includes_policy_pins_and_legacy_selectors(self):
        from unittest.mock import patch
        import os
        installer = self.natural_fixture()
        procroot = self.base / 'proc'
        proc = procroot / '123'
        (proc / 'net').mkdir(parents=True)
        (proc / 'fd').mkdir()
        expected = {**self.hooks.selectors, **self.plan['live_env']}
        (proc / 'environ').write_bytes(b'API_KEY=not-a-secret\0' + b''.join(
            (k + '=' + v).encode() + b'\0' for k, v in expected.items()))
        os.symlink('socket:[456]', proc / 'fd/7')
        os.symlink('/root/voice-lab', proc / 'cwd')
        (proc / 'net/tcp').write_text('header\n0: 0100007F:' + format(8091, '04X') + ' 00000000:0000 0A 0 0 0 0 0 456\n')
        (proc / 'net/tcp6').write_text('header\n')
        with patch.object(self.m, 'Path', lambda value: procroot if value == '/proc' else Path(value)):
            actual, guarded = self.m.SystemHooks.process(123)
        self.assertTrue(guarded)
        self.assertEqual(actual, expected)

    def test_natural_payload_cannot_be_promoted_without_explicit_policy(self):
        installer = self.natural_fixture()
        self.plan['live_env'] = {k: v for k, v in self.hooks.selectors.items()
                                 if k in ('VOICE_SELA_LIVE_TTS', 'VOICE_SELA_LIVE_PYTHON',
                                          'VOICE_SELA_LIVE_WORKER', 'VOICE_SELA_LIVE_WORKER_SHA256',
                                          'VOICE_SELA_LIVE_LOCK')}
        self.plan['live_env']['VOICE_SELA_LIVE_WORKER_SHA256'] = self.plan['files']['sela_live_tts/worker.py']['sha256']
        self.approve()
        installer.plan = self.plan
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertEqual(self.hooks.events, [])
        self.assertFalse((self.base / 'snapshots').exists())

    def test_natural_requires_nonexperimental_baseline_and_exact_independent_pins(self):
        for key in ('VOICE_SELA_LIVE_POLICY', 'VOICE_SELA_LIVE_WORKER',
                    'VOICE_SELA_LIVE_WORKER_SHA256', 'VOICE_SELA_LIVE_DURATION_WORKER',
                    'VOICE_SELA_LIVE_DURATION_WORKER_SHA256', 'VOICE_SELA_LIVE_SOURCE_MANIFEST_SHA256',
                    'VOICE_SELA_LIVE_RUNTIME_MANIFEST', 'VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256',
                    'VOICE_SELA_LIVE_LOCK', 'VOICE_SELA_LIVE_PYTHON', 'baseline_prosody'):
            with self.subTest(key=key):
                installer = self.natural_fixture()
                if key == 'baseline_prosody':
                    baseline = json.loads(self.baseline.read_bytes())
                    baseline['selectors']['VOICE_EXPERIMENTAL_DELIVERY_PROSODY'] = '1'
                    self.baseline.write_bytes(self.m.canonical(baseline))
                    self.plan['baseline_sha256'] = self.m.sha(self.baseline.read_bytes())
                    self.hooks.selectors = baseline['selectors']
                else:
                    self.plan['live_env'][key] = 'wrong'
                self.approve()
                installer.plan = self.plan
                with self.assertRaises(ValueError):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
                self.assertFalse((self.base / 'snapshots').exists())

    def natural_installing_fixture(self):
        installer = self.natural_fixture()
        original = self.hooks.restart
        def restart():
            original()
            path = self.units / self.m.DROPIN
            self.hooks.selectors = dict(installer.baseline['selectors'])
            if path.exists() and path.read_bytes() == installer.dropin():
                self.hooks.selectors.update(installer.plan['live_env'])
        self.hooks.restart = restart
        return installer

    def invoke_real_cli(self, installer, *, apply=False):
        from unittest.mock import patch
        import contextlib
        import io
        path = self.base / 'plan.json'
        path.write_bytes(self.m.canonical(self.plan))
        path.chmod(0o644)
        actual_class = self.m.Installer
        def construct(plan, **fixed):
            self.assertEqual(fixed['root'], Path('/root/voice-lab'))
            installer.plan = plan
            self.assertIsInstance(installer, actual_class)
            return installer
        output = io.StringIO()
        with patch.object(self.m, 'Installer', construct), contextlib.redirect_stdout(output):
            code = self.m.main(['--plan', str(path)] + (['--apply'] if apply else []))
        return code, json.loads(output.getvalue())

    def test_recovery_post_restart_error_stops_unverified_service(self):
        for point in ('restart','observe','inventory'):
            with self.subTest(point=point):
                self.setUp()
                installer=self.natural_installing_fixture()
                hooks=self.hooks;hooks.active=True;starts=[]
                original_start=hooks.restart;original_stop=hooks.stop
                original_observe=hooks.observe;original_inventory=installer.check_inventory
                baseline=json.loads(self.baseline.read_bytes())['selectors']
                def start():
                    original_start();starts.append(True);hooks.active=True
                    if len(starts)==1:raise OSError('candidate restart failed after starting')
                    hooks.selectors=dict(baseline)
                    if point=='restart':raise OSError('recovery restart failed after starting')
                def stop():original_stop();hooks.active=False
                def observe():
                    result=original_observe()
                    if len(starts)==2 and point=='observe':raise ValueError('recovery observation failed')
                    return result
                def inventory(*args,**kwargs):
                    if len(starts)==2 and point=='inventory':raise ValueError('recovery inventory failed')
                    return original_inventory(*args,**kwargs)
                hooks.restart=start;hooks.stop=stop;hooks.observe=observe;installer.check_inventory=inventory
                receipt=installer.run(apply=True)
                self.assertEqual(receipt['status'],'rollback_failed')
                self.assertFalse(hooks.active,'Unverified recovered service must be stopped')
                self.assertEqual(hooks.events.count('restart'),2)
                self.assertEqual(hooks.events.count('stop'),3)
                self.assertEqual(hooks.events[-1],'stop')
                self.assertTrue(receipt['recovery_final_stop_succeeded'])

    def test_failed_final_recovery_stop_never_claims_stopped(self):
        installer=self.natural_installing_fixture();hooks=self.hooks;hooks.active=True;starts=[];stops=[]
        original_start=hooks.restart;original_stop=hooks.stop;original_observe=hooks.observe
        baseline=json.loads(self.baseline.read_bytes())['selectors']
        def start():
            original_start();starts.append(True);hooks.active=True
            if len(starts)==1:raise OSError('candidate partial restart')
            hooks.selectors=dict(baseline)
        def stop():
            original_stop();stops.append(True)
            if len(stops)==3:raise OSError('final stop failed')
            hooks.active=False
        def observe():
            result=original_observe()
            if len(starts)==2:raise ValueError('recovery observation failed')
            return result
        hooks.restart=start;hooks.stop=stop;hooks.observe=observe
        receipt=installer.run(apply=True)
        self.assertEqual(receipt['status'],'rollback_failed')
        self.assertTrue(hooks.active)
        self.assertFalse(receipt['recovery_final_stop_succeeded'])
        self.assertIn('final_stop:OSError',receipt['cleanup_errors'])
        self.assertEqual(len(starts),2)

    def test_natural_real_cli_preflight_apply_and_exact_readback(self):
        installer = self.natural_installing_fixture()
        self.assertEqual(self.invoke_real_cli(installer), (0, {'status': 'ready', 'applied': False}))
        self.assertFalse((self.base / 'snapshots').exists())
        runtime = self.root / '.runtime'
        runtime.mkdir(mode=0o700)
        lock = runtime / 'live-tts.lock'
        lock.write_bytes(b'')
        lock.chmod(0o600)
        inode = lock.stat().st_ino
        code, receipt = self.invoke_real_cli(installer, apply=True)
        self.assertEqual(code, 0)
        self.assertEqual(receipt['status'], 'installed')
        self.assertTrue(receipt['applied'])
        self.assertEqual(self.hooks.events, ['observe', 'observe', 'stop', 'restart', 'observe'])
        installer.readback()
        self.assertEqual(lock.stat().st_ino, inode)
        for key, value in installer.baseline['selectors'].items():
            if key not in installer.plan['live_env']:
                self.assertEqual(self.hooks.selectors[key], value)
        saved = json.loads((Path(receipt['snapshot']) / 'snapshot.json').read_bytes())
        self.assertTrue(saved['app']['sela_live_tts/worker.py']['exists'])
        self.assertFalse(saved['app']['sela_live_tts/natural_worker.py']['exists'])

    def test_natural_real_cli_failure_restores_quality12_before_recovery_restart(self):
        import shutil
        for failure in ('write', 'restart', 'readback', 'selectors'):
            with self.subTest(failure=failure):
                installer = self.natural_installing_fixture()
                runtime = self.root / '.runtime'
                runtime.mkdir(mode=0o700)
                lock = runtime / 'live-tts.lock'
                lock.write_bytes(b'')
                lock.chmod(0o600)
                inode = lock.stat().st_ino
                original = installer.atomic
                fired = []
                def atomic(path, raw, mode):
                    if failure == 'write' and path == self.root / 'sela_live_tts/natural_worker.py' and not fired:
                        fired.append(True)
                        raise OSError('fixture partial write')
                    original(path, raw, mode)
                installer.atomic = atomic
                restart = self.hooks.restart
                def checked_restart():
                    if not fired and failure != 'write':
                        fired.append(True)
                        if failure == 'restart':
                            raise OSError('fixture cold boot failure')
                        restart()
                        if failure == 'readback':
                            (self.root / 'sela_live_tts/natural_worker.py').write_bytes(b'corrupt')
                        else:
                            self.hooks.selectors['VOICE_SELA_LIVE_POLICY'] = 'wrong'
                    else:
                        # Recovery must not restart a mixed tree.
                        for group, root in (('app', self.root), ('units', self.units)):
                            for name, record in installer.baseline[group].items():
                                installer.match(root / name, record)
                        installer.check_inventory()
                        restart()
                self.hooks.restart = checked_restart
                code, receipt = self.invoke_real_cli(installer, apply=True)
                self.assertEqual(code, 1)
                self.assertEqual(receipt['status'], 'rolled_back')
                self.assertEqual(receipt['cleanup_errors'], [])
                self.assertFalse(receipt['applied'])
                self.assertEqual(lock.stat().st_ino, inode)
                self.assertEqual(self.hooks.selectors, installer.baseline['selectors'])
                self.assertFalse((self.root / 'sela_live_tts/natural_worker.py').exists())
                self.assertEqual(json.loads((self.base / 'snapshots' / (Path(receipt['snapshot']).name + '.receipt.json')).read_bytes()), receipt)
                for path in (self.root, self.units, self.payload, self.base / 'snapshots'):
                    shutil.rmtree(path)
                    if path.name != 'snapshots':
                        path.mkdir()

    def test_legacy_real_cli_preflight_and_apply_use_real_installer(self):
        installer = self.installing_fixture()
        self.assertEqual(self.invoke_real_cli(installer), (0, {'status': 'ready', 'applied': False}))
        code, receipt = self.invoke_real_cli(installer, apply=True)
        self.assertEqual(code, 0)
        self.assertEqual(receipt['status'], 'installed')
        installer.readback()

    def test_natural_exact_review_bindings_remain_mandatory(self):
        for key in ('installer_sha256', 'testsource_sha256', 'baseline_sha256',
                    'runtime_review_sha256', 'payload_sha256', 'plan_sha256', 'artifact_modes', 'reviewer'):
            with self.subTest(key=key):
                installer = self.natural_fixture()
                installer.plan['approval'][key] = ''
                with self.assertRaises(ValueError):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
                self.assertFalse((self.base / 'snapshots').exists())

    def test_preflight_valid_exact_plan_does_not_mutate(self):
        installer = self.fixture()
        self.assertEqual(installer.run()['status'], 'ready')
        self.assertEqual(self.hooks.events, ['observe'])
        self.assertFalse((self.base / 'snapshots').exists())
        self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'old')

    def test_rejects_non_allowlisted_or_unlisted_payload_before_snapshot(self):
        for bad in ('voice_app/auth.py', '../escaped.py', '/absolute.py',
                    'sela_live_tts/../escape.py', 'sela_live_tts//collision.py',
                    'vendor/supertonic/key.json', 'sela_live_tts/secrets.env'):
            with self.subTest(bad=bad):
                installer = self.fixture()
                installer.plan['files'][bad] = {'sha256': '0' * 64, 'mode': 0o644}
                # Even a reviewer cannot expand the compiled allowlist.
                self.plan = installer.plan
                self.approve()
                installer.plan = self.plan
                with self.assertRaises((ValueError, OSError)):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
        installer = self.fixture()
        self.write(self.payload, 'extra.py', b'extra')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            installer.run()
        self.assertEqual(self.hooks.events, [])

    def test_live_environment_is_data_only_and_preserves_selectors(self):
        changes = ({'VOICE_STT_BACKEND': 'browser'}, {'API_KEY': 'secret'},
                   {'VOICE_SELA_LIVE_TTS': '0'}, {'VOICE_SELA_LIVE_WORKER': '/elsewhere/worker.py'},
                   {'VOICE_SELA_LIVE_WORKER_SHA256': 'a' * 64},
                   {'VOICE_SELA_LIVE_PYTHON': '/evil/bin/python'},
                   {'VOICE_SELA_LIVE_LOCK': '/tmp/release/lock'},
                   {'VOICE_SELA_LIVE_PYTHON': '/python\nExecStart=/evil'})
        for change in changes:
            with self.subTest(change=change):
                installer = self.fixture()
                self.plan['live_env'].update(change)
                self.approve()
                installer.plan = self.plan
                with self.assertRaises(ValueError):
                    installer.run()
                self.assertEqual(self.hooks.events, [])
        installer = self.fixture()
        self.hooks.selectors['VOICE_SUPERTONIC3_STYLE_PROFILE'] = 'other'
        with self.assertRaisesRegex(ValueError, 'selectors'):
            installer.run()
        self.assertFalse((self.base / 'snapshots').exists())

    def installing_fixture(self):
        installer = self.fixture()
        original_restart = self.hooks.restart
        def restart():
            original_restart()
            dropin = self.units / self.m.DROPIN
            if dropin.exists() and dropin.read_bytes() == installer.dropin():
                self.hooks.selectors = {**installer.baseline['selectors'], **installer.plan['live_env']}
            else:
                self.hooks.selectors = dict(installer.baseline['selectors'])
        self.hooks.restart = restart
        return installer

    def test_apply_archives_writes_restarts_and_exact_readback(self):
        installer = self.installing_fixture()
        receipt = installer.run(apply=True)
        self.assertEqual(receipt['status'], 'installed')
        self.assertTrue(receipt['applied'])
        self.assertEqual(self.hooks.events, ['observe', 'stop', 'restart', 'observe'])
        for name, record in installer.plan['files'].items():
            installer.match(self.root / name, record)
        snapshot = Path(receipt['snapshot'])
        manifest = json.loads((snapshot / 'snapshot.json').read_bytes())
        self.assertFalse(manifest['app']['voice_app/sela_live_bridge.py']['exists'])
        self.assertTrue(manifest['app']['voice_app/speech.py']['exists'])
        self.assertFalse(manifest['units'][self.m.DROPIN]['exists'])
        self.assertEqual((snapshot / 'app/voice_app/speech.py').read_bytes(), b'old')
        self.assertEqual((snapshot / 'snapshot.json').stat().st_mode & 0o777, 0o400)
        self.assertEqual((self.root / 'web/app.js').read_bytes(), b'old')
        lock = self.root / '.runtime/live-tts.lock'
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        self.assertEqual(lock.parent.stat().st_mode & 0o777, 0o700)

    def test_unbound_existing_destination_or_dropin_rejected_before_archive(self):
        for group, name in (('app', 'voice_app/sela_live_bridge.py'),
                            ('units', self.m.DROPIN)):
            with self.subTest(group=group):
                installer = self.fixture()
                self.write(self.root if group == 'app' else self.units, name, b'unreviewed')
                with self.assertRaises(ValueError):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
                self.assertFalse((self.base / 'snapshots').exists())
                (self.root if group == 'app' else self.units).joinpath(name).unlink()

    def test_failed_restart_restores_existing_and_missing_with_persistent_lock(self):
        installer = self.installing_fixture()
        original = self.hooks.restart
        calls = []
        def restart():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('fixture failure')
            original()
        self.hooks.restart = restart
        receipt = installer.run(apply=True)
        self.assertEqual(receipt['status'], 'rolled_back')
        self.assertFalse(receipt['applied'])
        self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'old')
        self.assertFalse((self.root / 'voice_app/sela_live_bridge.py').exists())
        self.assertFalse((self.units / self.m.DROPIN).exists())
        self.assertTrue((self.root / '.runtime/live-tts.lock').exists())
        self.assertEqual(json.loads((self.base / 'snapshots' / (Path(receipt['snapshot']).name + '.receipt.json')).read_bytes()), receipt)

    def test_unsafe_lock_and_nonregular_source_rejected_before_archive(self):
        import os
        installer = self.fixture()
        runtime = self.root / '.runtime'
        runtime.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertEqual(self.hooks.events, [])
        self.assertFalse((self.base / 'snapshots').exists())
        runtime.rmdir()
        path = self.payload / 'sela_live_tts/worker.py'
        path.unlink()
        os.symlink(self.payload / 'sela_live_tts/__init__.py', path)
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertEqual(self.hooks.events, [])

    def test_cli_constructs_only_fixed_roots_and_explicit_apply(self):
        from unittest.mock import patch
        import contextlib
        import io
        self.fixture()
        path = self.base / 'plan.json'
        path.write_text(json.dumps(self.plan))
        calls = []
        class HarmlessInstaller:
            def __init__(inner, plan, **kwargs):
                calls.append(kwargs)
            def run(inner, *, apply=False):
                calls.append(apply)
                return {'status': 'installed' if apply else 'ready', 'applied': apply}
        with patch.object(self.m, 'Installer', HarmlessInstaller), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.m.main(['--plan', str(path)]), 0)
            self.assertEqual(self.m.main(['--plan', str(path), '--apply']), 0)
        self.assertEqual(str(calls[0]['root']), '/root/voice-lab')
        self.assertEqual(str(calls[0]['units']), '/etc/systemd/system')
        self.assertEqual(str(calls[0]['snapshots']), '/root/sela-live-install-snapshots')
        self.assertIs(calls[1], False)
        self.assertIs(calls[3], True)

    def test_system_hooks_use_fixed_safe_commands_and_guarded_observation(self):
        from unittest.mock import patch
        m = self.m
        calls = []
        def command(*args):
            calls.append(args)
            if args[0] == 'show':
                return 'ActiveState=active\nSubState=running\nMainPID=123\nWorkingDirectory=/root/voice-lab\n'
            return ''
        hooks = m.SystemHooks()
        hooks.command = command
        hooks.process = lambda pid: ({'VOICE_MODEL': 'gpt-6.1-sol'}, True)
        responses = iter([(200, b'{"status":"ok"}'), (401, b''), (400, b''), (403, b'')])
        hooks.http = lambda *args, **kwargs: next(responses)
        self.assertEqual(hooks.observe(), {'healthy': True, 'selectors': {'VOICE_MODEL': 'gpt-6.1-sol'}})
        hooks.stop()
        hooks.http = lambda *args, **kwargs: (200, b'{"status":"ok"}')
        hooks.restart()
        self.assertEqual(calls[1:], [('stop', 'voice-lab.service'), ('daemon-reload',), ('restart', 'voice-lab.service')])
        hooks.process = lambda pid: ({}, False)
        with self.assertRaises(ValueError):
            hooks.observe()

    def test_mode_binding_and_preserved_guard_drift_rejected(self):
        installer = self.fixture()
        installer.plan['approval']['artifact_modes'] = {'installer': 0}
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertEqual(self.hooks.events, [])
        self.assertFalse((self.base / 'snapshots').exists())

    def test_writer_serialization_rejects_before_observe_or_archive(self):
        import os
        import fcntl
        installer = self.fixture()
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                installer.run(apply=True)
        finally:
            os.close(fd)
        self.assertEqual(self.hooks.events, [])
        self.assertFalse((self.base / 'snapshots').exists())

    def test_unlisted_existing_runtime_source_and_unit_override_rejected(self):
        for root_name, name in (('app', 'sela_live_tts/shadow.py'),
                                ('units', 'voice-lab.service.d/99-override.conf')):
            with self.subTest(name=name):
                installer = self.fixture()
                path = self.root if root_name == 'app' else self.units
                self.write(path, name)
                with self.assertRaises(ValueError):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
                self.assertFalse((self.base / 'snapshots').exists())
                (path / name).unlink()

    def test_exact_review_digests_reject_before_archive(self):
        for key in ('installer_sha256', 'testsource_sha256', 'baseline_sha256',
                    'runtime_review_sha256', 'payload_sha256', 'plan_sha256'):
            with self.subTest(key=key):
                installer = self.fixture()
                installer.plan['approval'][key] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'binding'):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
                self.assertFalse((self.base / 'snapshots').exists())
        for key in ('reviewer', 'testsource_sha256'):
            installer = self.fixture()
            installer.plan['approval'].pop(key)
            with self.assertRaises(ValueError):
                installer.run(apply=True)
            self.assertEqual(self.hooks.events, [])

    def test_reviewed_input_bytes_and_modes_drift_rejected(self):
        for target in ('baseline', 'runtime', 'payload', 'guard', 'unit', 'payload_mode'):
            with self.subTest(target=target):
                installer = self.fixture()
                paths = {'baseline': self.baseline, 'runtime': self.runtime,
                         'payload': self.payload / 'sela_live_tts/worker.py',
                         'guard': self.root / 'voice_app/approved_conditioning.py',
                         'unit': self.units / 'voice-lab.service',
                         'payload_mode': self.payload / 'sela_live_tts/worker.py'}
                path = paths[target]
                if target == 'payload_mode':
                    path.chmod(0o600)
                else:
                    path.write_bytes(path.read_bytes() + b' drift')
                with self.assertRaises((ValueError, OSError)):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
                self.assertFalse((self.base / 'snapshots').exists())

    def test_rollback_after_partial_write_and_exact_postreadback_failure(self):
        for failure in ('write', 'readback', 'selectors'):
            with self.subTest(failure=failure):
                installer = self.installing_fixture()
                original = installer.atomic
                fired = []
                def atomic(path, raw, mode):
                    if failure == 'write' and path == self.root / 'voice_app/sela_live_bridge.py' and not fired:
                        fired.append(True)
                        raise OSError('fixture write failure')
                    original(path, raw, mode)
                installer.atomic = atomic
                restart = self.hooks.restart
                def corrupt_once():
                    restart()
                    if not fired and failure in ('readback', 'selectors'):
                        fired.append(True)
                        if failure == 'readback':
                            (self.root / 'sela_live_tts/worker.py').write_bytes(b'corrupt')
                        else:
                            self.hooks.selectors['VOICE_SELA_LIVE_TTS'] = '0'
                self.hooks.restart = corrupt_once
                receipt = installer.run(apply=True)
                self.assertEqual(receipt['status'], 'rolled_back')
                self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'old')
                self.assertFalse((self.root / 'sela_live_tts/worker.py').exists())
                self.assertFalse(receipt['applied'])
                # Each subcase starts with a clean fixture tree.
                import shutil
                shutil.rmtree(self.base / 'snapshots')

    def test_prior_dropin_mode_and_persistent_lock_inode_survive_rollback(self):
        import os
        installer = self.installing_fixture()
        old = self.write(self.units, self.m.DROPIN, b'[Service]\n# prior\n')
        path = self.units / self.m.DROPIN
        path.chmod(0o600)
        old['mode'] = 0o600
        baseline = json.loads(self.baseline.read_bytes())
        baseline['units'][self.m.DROPIN] = old
        self.baseline.write_text(json.dumps(baseline))
        self.plan['baseline_sha256'] = self.m.sha(self.baseline.read_bytes())
        self.approve()
        installer.plan = self.plan
        runtime = self.root / '.runtime'
        runtime.mkdir(mode=0o700)
        lock = runtime / 'live-tts.lock'
        lock.write_bytes(b'')
        lock.chmod(0o600)
        inode = lock.stat().st_ino
        restart = self.hooks.restart
        called = []
        def fail_once():
            if not called:
                called.append(True)
                raise OSError('fixture restart failure')
            restart()
        self.hooks.restart = fail_once
        receipt = installer.run(apply=True)
        self.assertEqual(receipt['status'], 'rolled_back')
        installer.match(path, old)
        self.assertEqual(lock.stat().st_ino, inode)

    def test_cleanup_failures_have_terminal_failure_receipt(self):
        installer = self.installing_fixture()
        self.hooks.restart = lambda: (_ for _ in ()).throw(OSError('fixture restart failure'))
        receipt = installer.run(apply=True)
        self.assertEqual(receipt['status'], 'rollback_failed')
        self.assertTrue(receipt['cleanup_errors'])
        self.assertFalse(receipt['applied'])

    def test_observe_time_source_drift_rejected_before_archive_or_stop(self):
        installer = self.fixture()
        observe = self.hooks.observe
        def drift():
            result = observe()
            (self.root / 'voice_app/speech.py').write_bytes(b'changed during observation')
            return result
        self.hooks.observe = drift
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertEqual(self.hooks.events, ['observe'])
        self.assertFalse((self.base / 'snapshots').exists())

    def test_cli_plan_mode_drift_rejected_before_constructing_installer(self):
        from unittest.mock import patch
        self.fixture()
        path = self.base / 'plan.json'
        path.write_text(json.dumps(self.plan))
        path.chmod(0o600)
        with patch.object(self.m, 'Installer') as factory:
            self.assertEqual(self.m.main(['--plan', str(path)]), 2)
            factory.assert_not_called()

    def test_snapshot_readback_corruption_rejected_before_stop(self):
        installer = self.fixture()
        original = installer.atomic
        def corrupt(path, raw, mode):
            if 'snapshots' in path.parts and path.name == 'speech.py':
                raw = b'corrupt archive'
            original(path, raw, mode)
        installer.atomic = corrupt
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertEqual(self.hooks.events, ['observe'])
        self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'old')

    def test_http_probe_valid_websocket_key_and_no_credentials(self):
        from unittest.mock import patch
        import base64
        requests = []
        class Connection:
            def __init__(inner, *args, **kwargs):
                self.assertEqual(args, ('127.0.0.1', 8091))
            def request(inner, method, path, headers):
                requests.append((method, path, headers))
            def getresponse(inner):
                return inner
            status = 403
            def read(inner, limit):
                self.assertEqual(limit, 4097)
                return b''
            def close(inner):
                pass
        with patch('http.client.HTTPConnection', Connection):
            self.assertEqual(self.m.SystemHooks.http('/ws', websocket=True), (403, b''))
        headers = requests[0][2]
        self.assertEqual(len(base64.b64decode(headers['Sec-WebSocket-Key'], validate=True)), 16)
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('Cookie', headers)

    def test_real_orchestration_order_archive_before_stop_before_write(self):
        installer = self.installing_fixture()
        original_archive = installer.archive
        original_atomic = installer.atomic
        def archive():
            result = original_archive()
            self.hooks.events.append('archive')
            return result
        def atomic(path, raw, mode):
            if self.root in path.parents and path.suffix == '.py':
                self.hooks.events.append('write')
            original_atomic(path, raw, mode)
        installer.archive = archive
        installer.atomic = atomic
        self.assertEqual(installer.run(apply=True)['status'], 'installed')
        events = self.hooks.events
        self.assertEqual(events[:3], ['observe', 'archive', 'stop'])
        self.assertLess(events.index('stop'), events.index('write'))
        self.assertLess(max(i for i, event in enumerate(events) if event == 'write'), events.index('restart'))
        self.assertEqual(events[-2:], ['restart', 'observe'])

    def test_guard_health_and_every_baseline_anchor_rejected_before_archive(self):
        for key in self.m.ANCHORS:
            with self.subTest(key=key):
                installer = self.fixture()
                baseline = json.loads(self.baseline.read_bytes())
                baseline['selectors'][key] = 'wrong'
                self.baseline.write_text(json.dumps(baseline))
                self.plan['baseline_sha256'] = self.m.sha(self.baseline.read_bytes())
                self.approve()
                installer.plan = self.plan
                with self.assertRaises(ValueError):
                    installer.run(apply=True)
                self.assertEqual(self.hooks.events, [])
        installer = self.fixture()
        self.hooks.observe = lambda: {'healthy': False, 'selectors': self.hooks.selectors}
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertFalse((self.base / 'snapshots').exists())

    def test_safe_process_observation_filters_secrets_and_checks_owned_listener(self):
        from unittest.mock import patch
        import os
        m = self.m
        procroot = self.base / 'proc'
        proc = procroot / '123'
        (proc / 'net').mkdir(parents=True)
        (proc / 'fd').mkdir()
        (proc / 'environ').write_bytes(b'API_KEY=not-a-real-secret\0VOICE_MODEL=gpt-6.1-sol\0VOICE_SELA_LIVE_TTS=1\0UNKNOWN=x\0')
        os.symlink('socket:[456]', proc / 'fd/7')
        os.symlink('/root/voice-lab', proc / 'cwd')
        (proc / 'net/tcp').write_text('header\n0: 0100007F:' + format(8091, '04X') + ' 00000000:0000 0A 0 0 0 0 0 456\n')
        (proc / 'net/tcp6').write_text('header\n')
        realpath = Path
        def path(value):
            return procroot if value == '/proc' else realpath(value)
        with patch.object(m, 'Path', path):
            selectors, guarded = m.SystemHooks.process(123)
            self.assertTrue(guarded)
            self.assertEqual(selectors, {'VOICE_MODEL': 'gpt-6.1-sol', 'VOICE_SELA_LIVE_TTS': '1'})
            (proc / 'net/tcp').write_text('header\n0: 00000000:' + format(8091, '04X') + ' 00000000:0000 0A 0 0 0 0 0 456\n')
            self.assertFalse(m.SystemHooks.process(123)[1])

    def test_restart_waits_boundedly_for_health_without_inference(self):
        from unittest.mock import patch
        hooks = self.m.SystemHooks()
        hooks.command = lambda *args: ''
        calls = []
        def http(path):
            self.assertEqual(path, '/health')
            calls.append(path)
            if len(calls) == 1:
                raise ConnectionRefusedError('fixture starting')
            return (200, b'{"status":"ok"}')
        hooks.http = http
        with patch('time.sleep'):
            hooks.restart()
        self.assertEqual(len(calls), 2)
        hooks.http = lambda path: (503, b'')
        with patch('time.sleep'), self.assertRaises(ValueError):
            hooks.restart()

    def test_restart_allows_seven_profile_preflights_but_retains_wall_deadline(self):
        from unittest.mock import patch
        hooks = self.m.SystemHooks()
        hooks.command = lambda *args: ''
        calls = []
        def http(path):
            calls.append(path)
            return (200, b'{"status":"ok"}') if len(calls) == 31 else (503, b'')
        hooks.http = http
        with patch('time.sleep'):
            hooks.restart()
        self.assertEqual(len(calls), 31)
        calls.clear()
        with patch('time.monotonic', side_effect=[0, 121]), patch('time.sleep'), self.assertRaises(ValueError):
            hooks.restart()
        self.assertEqual(calls, [])

    def test_snapshot_parent_must_be_private_before_stop(self):
        installer = self.fixture()
        snapshots = self.base / 'snapshots'
        snapshots.mkdir(mode=0o777)
        snapshots.chmod(0o777)
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertNotIn('stop', self.hooks.events)
        self.assertEqual(list(snapshots.iterdir()), [])

    def test_terminal_receipt_on_restore_and_receipt_write_failures(self):
        for failure in ('restore', 'receipt'):
            with self.subTest(failure=failure):
                installer = self.installing_fixture()
                atomic = installer.atomic
                counts = []
                def fail(path, raw, mode):
                    if failure == 'restore' and path == self.root / 'voice_app/speech.py' and raw == b'old':
                        raise OSError('fixture restore failure')
                    if failure == 'receipt' and path.name.endswith('.receipt.json'):
                        counts.append(1)
                        if len(counts) > 1:
                            raise OSError('fixture receipt failure')
                    atomic(path, raw, mode)
                installer.atomic = fail
                if failure == 'restore':
                    self.hooks.restart = lambda: (_ for _ in ()).throw(OSError('fixture restart failure'))
                receipt = installer.run(apply=True)
                self.assertEqual(receipt['status'], 'rollback_failed' if failure == 'restore' else 'receipt_failed')
                self.assertFalse(receipt['applied'])
                self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'new')
                if failure == 'restore':
                    # Clear only fixture data left by an intentionally failed rollback.
                    import shutil
                    for path in (self.root, self.units, self.payload, self.base / 'snapshots'):
                        shutil.rmtree(path)
                        if path.name != 'snapshots':
                            path.mkdir()

    def test_existing_unsafe_or_busy_shared_lock_is_never_replaced(self):
        import os
        import fcntl
        installer = self.fixture()
        runtime = self.root / '.runtime'
        runtime.mkdir(mode=0o700)
        lock = runtime / 'live-tts.lock'
        lock.write_bytes(b'')
        lock.chmod(0o644)
        inode = lock.stat().st_ino
        with self.assertRaises(ValueError):
            installer.run(apply=True)
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(self.hooks.events, [])
        lock.chmod(0o600)
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            receipt = installer.run(apply=True)
        finally:
            os.close(fd)
        self.assertEqual(receipt['status'], 'rolled_back')
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'old')

    def test_recovery_inventory_drift_is_not_reported_as_rolled_back(self):
        installer = self.installing_fixture()
        original_restart = self.hooks.restart
        calls = []
        def restart():
            calls.append(1)
            if len(calls) == 1:
                raise OSError('fixture restart failure')
            original_restart()
            self.write(self.root, 'sela_live_tts/unreviewed.py', b'unreviewed')
        self.hooks.restart = restart
        receipt = installer.run(apply=True)
        self.assertEqual(receipt['status'], 'rollback_failed')
        self.assertFalse(receipt['applied'])

    def test_failed_recovery_stop_never_restores_or_restarts(self):
        installer = self.installing_fixture()
        stops, restarts, restores = [], [], []
        atomic = installer.atomic
        def stop():
            stops.append(1)
            if len(stops) == 2:
                raise OSError('fixture stop failed')
        def restart():
            restarts.append(1)
            raise OSError('fixture initial restart failed')
        def write(path, raw, mode):
            if path == self.root / 'voice_app/speech.py' and raw == b'old':
                restores.append(1)
            return atomic(path, raw, mode)
        self.hooks.stop, self.hooks.restart, installer.atomic = stop, restart, write
        receipt = installer.run(apply=True)
        self.assertEqual(receipt['status'], 'rollback_failed')
        self.assertEqual(restores, [])
        self.assertEqual(len(restarts), 1)
        self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'new')

    def test_failed_restoration_never_restarts_candidate_source(self):
        installer = self.installing_fixture()
        restarts = []
        atomic = installer.atomic
        def restart():
            restarts.append((self.root / 'voice_app/speech.py').read_bytes())
            raise OSError('fixture initial restart failed')
        def write(path, raw, mode):
            if path == self.root / 'voice_app/speech.py' and raw == b'old':
                raise OSError('fixture restoration failed')
            return atomic(path, raw, mode)
        self.hooks.restart, installer.atomic = restart, write
        receipt = installer.run(apply=True)
        self.assertEqual(receipt['status'], 'rollback_failed')
        self.assertEqual(restarts, [b'new'])

    def test_baseline_inventory_is_verified_before_recovery_restart(self):
        installer = self.installing_fixture()
        restarts = []
        atomic = installer.atomic
        def restart():
            restarts.append(1)
            raise OSError('fixture initial restart failed')
        def write(path, raw, mode):
            result = atomic(path, raw, mode)
            if path == self.root / 'voice_app/speech.py' and raw == b'old':
                self.write(self.root, 'voice_app/unreviewed.py', b'unreviewed')
            return result
        self.hooks.restart, installer.atomic = restart, write
        receipt = installer.run(apply=True)
        self.assertEqual(receipt['status'], 'rollback_failed')
        self.assertEqual(len(restarts), 1)
        self.assertEqual((self.root / 'voice_app/speech.py').read_bytes(), b'old')

    def test_default_cli_is_read_only(self):
        self.assertTrue(SOURCE.exists(), 'installer is missing')
        spec = importlib.util.spec_from_file_location('live_installer', SOURCE)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'plan.json'
            path.write_text('{}')
            self.assertEqual(m.main(['--plan', str(path)]), 2)
            self.assertEqual(list(Path(temp).iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
