#!/usr/bin/env python3
"""Guarded, offline Sela Live promotion. CLI is read-only without --apply."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import os
import re
import stat
import tempfile
import fcntl
import uuid


SELECTOR_KEYS = frozenset(('VOICE_PUBLIC_ORIGIN', 'VOICE_MODEL', 'VOICE_STT_BACKEND',
    'VOICE_TTS_BACKEND', 'VOICE_SUPERTONIC3_STYLE_PROFILE', 'VOICE_AUTOMATIC_CONDITIONING',
    'VOICE_ZIPFORMER_STATEFUL', 'VOICE_LOCAL_ASR_ENGINE', 'VOICE_SUPERTONIC3_PYTHON',
    'VOICE_SUPERTONIC3_MODEL_PATH', 'VOICE_SUPERTONIC3_MANIFEST',
    'VOICE_EXPERIMENTAL_DELIVERY_PROSODY'))
ANCHORS = {'VOICE_STT_BACKEND': 'local', 'VOICE_TTS_BACKEND': 'supertonic3',
           'VOICE_SUPERTONIC3_STYLE_PROFILE': 'conditioning_b', 'VOICE_AUTOMATIC_CONDITIONING': '1',
           'VOICE_ZIPFORMER_STATEFUL': '1', 'VOICE_LOCAL_ASR_ENGINE': 'zipformer_id_int8',
           'VOICE_PUBLIC_ORIGIN': 'https://voice.kasirdigital.web.id'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


GUARDS = frozenset('voice_app/' + n for n in ('approved_conditioning.py', 'automatic_conditioning.py', 'supertonic3_worker.py'))
LIVE_KEYS = frozenset('VOICE_SELA_LIVE_' + n for n in (
    'TTS', 'PYTHON', 'WORKER', 'WORKER_SHA256', 'LOCK', 'POLICY', 'DURATION_WORKER',
    'DURATION_WORKER_SHA256', 'SOURCE_MANIFEST_SHA256', 'RUNTIME_MANIFEST', 'RUNTIME_MANIFEST_SHA256'))
DROPIN = 'voice-lab.service.d/90-sela-live-tts.conf'
TESTSOURCE = Path(__file__).resolve().parents[1] / 'tests/setup/test_live_tts_install.py'


RUNTIME_MANIFEST = 'sela_live_tts/natural-runtime-manifest.json'
# Kept explicit and stdlib-only: never import candidate runtime during promotion.
NATURAL_RUNTIME_FILES = frozenset(
    'sela_live_tts/' + name for name in (
        '__init__.py', 'backend.py', 'admission.py', 'controls.py', 'source_pins.py',
        'worker.py', 'natural.py', 'utterance.py', 'duration.py', 'natural_worker.py',
        'duration_worker.py', 'upstream-manifest.json')) | GUARDS | frozenset(
    'vendor/supertonic/' + name for name in (
        '__init__.py', 'cli.py', 'config.py', 'core.py', 'loader.py', 'pipeline.py', 'utils.py',
        'server/__init__.py', 'server/app.py', 'server/audio.py', 'server/routes.py',
        'server/schemas.py', 'server/styles_store.py'))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate manifest key')
        result[key] = value
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def relative(name):
    if type(name) is not str or not name or name.startswith('/') or '\\' in name or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('Unsafe relative path')
    return name


def safe(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe absolute path or symlink: ' + str(path))
    return path


def inventory(root):
    safe(root)
    result = set()
    if root.exists():
        for p in root.rglob('*'):
            safe(p)
            if p.is_file():
                # Python's incidental caches are not deployment source.
                if '__pycache__' not in p.parts and p.suffix != '.pyc':
                    result.add(str(p.relative_to(root)))
            elif not p.is_dir():
                raise ValueError('Nonregular inventory entry')
    return result


def allowed(name):
    relative(name)
    return (name in {'voice_app/speech.py', 'voice_app/sela_live_bridge.py',
                     'sela_live_tts/upstream-manifest.json', 'sela_live_tts/natural-runtime-manifest.json',
                     'vendor/supertonic-LICENSE.txt',
                     'vendor/SUPERTONIC-PROVENANCE.json'} or
            (name.startswith('sela_live_tts/') and name.endswith('.py')) or
            (name.startswith('vendor/supertonic/') and name.endswith('.py')))


class Installer:
    """Fixture roots/hooks are injectable here only; the CLI never exposes them."""
    def __init__(self, plan, *, root, units, snapshots, hooks):
        self.plan = json.loads(json.dumps(plan))
        self.root, self.units, self.snapshots = map(Path, (root, units, snapshots))
        self.hooks = hooks

    def preflight(self, *, observe=True):
        p = self.plan
        if p.get('schema_version') != 1:
            raise ValueError('Unsupported deployment schema')
        approval = p['approval']
        unsigned = {k: v for k, v in p.items() if k != 'approval'}
        bindings = {'installer_sha256': sha(Path(__file__).read_bytes()),
                    'testsource_sha256': sha(TESTSOURCE.read_bytes()),
                    'baseline_sha256': p['baseline_sha256'],
                    'runtime_review_sha256': p['runtime_review_sha256'],
                    'payload_sha256': sha(canonical({k: p[k] for k in ('files', 'preserved', 'live_env')})),
                    'plan_sha256': sha(canonical(unsigned))}
        if approval.get('verdict') != 'approved' or not str(approval.get('reviewer', '')).strip():
            raise ValueError('Missing reviewer approval')
        if any(approval.get(k) != v for k, v in bindings.items()):
            raise ValueError('Exact review binding mismatch')
        artifacts = {'installer': Path(__file__).absolute(), 'testsource': TESTSOURCE,
                     'baseline': Path(p['baseline_path']), 'runtime_review': Path(p['runtime_review_path']),
                     'payload_root': Path(p['payload_root'])}
        modes = {}
        for name, path in artifacts.items():
            safe(path)
            s = path.stat()
            if (not stat.S_ISDIR(s.st_mode) if name == 'payload_root' else not stat.S_ISREG(s.st_mode)) or s.st_mode & 0o022:
                raise ValueError('Unsafe review artifact')
            modes[name] = stat.S_IMODE(s.st_mode)
        if approval.get('artifact_modes') != modes:
            raise ValueError('Review artifact mode drift')
        raw = Path(p['baseline_path']).read_bytes()
        if sha(raw) != p['baseline_sha256']:
            raise ValueError('Frozen baseline digest mismatch')
        self.baseline = json.loads(raw)
        raw = Path(p['runtime_review_path']).read_bytes()
        if sha(raw) != p['runtime_review_sha256']:
            raise ValueError('Runtime review digest mismatch')
        review = json.loads(raw)
        if review.get('verdict') != 'approved' or not str(review.get('reviewer', '')).strip():
            raise ValueError('Runtime review is not approved')
        files, preserved = p['files'], p['preserved']
        required = {'voice_app/speech.py', 'voice_app/sela_live_bridge.py', 'sela_live_tts/__init__.py',
                    'sela_live_tts/worker.py', 'sela_live_tts/upstream-manifest.json',
                    'vendor/supertonic-LICENSE.txt', 'vendor/SUPERTONIC-PROVENANCE.json'}
        if not required <= set(files) or set(preserved) != GUARDS or any(not allowed(n) for n in files):
            raise ValueError('Compiled payload allowlist mismatch')
        if ({RUNTIME_MANIFEST, 'sela_live_tts/natural_worker.py', 'sela_live_tts/duration_worker.py'} & set(files)
                and p['live_env'].get('VOICE_SELA_LIVE_POLICY') != 'natural_v1'):
            raise ValueError('Natural payload requires explicit natural_v1 policy')
        if 'VOICE_SELA_LIVE_POLICY' in p['live_env']:
            natural_files = (NATURAL_RUNTIME_FILES - GUARDS) | {
                'voice_app/speech.py', 'voice_app/sela_live_bridge.py', RUNTIME_MANIFEST,
                'vendor/supertonic-LICENSE.txt', 'vendor/SUPERTONIC-PROVENANCE.json'}
            if set(files) != natural_files:
                raise ValueError('Natural payload inventory mismatch')
            raw = self.match(Path(p['payload_root']) / RUNTIME_MANIFEST, files[RUNTIME_MANIFEST])
            manifest = json.loads(raw, object_pairs_hook=unique_object)
            expected_inventory = {n: {**files, **preserved}[n]['sha256'] for n in NATURAL_RUNTIME_FILES}
            if (type(manifest) is not dict or set(manifest) != {'schema_version', 'files'}
                    or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
                    or type(manifest['files']) is not dict or manifest['files'] != expected_inventory
                    or any(type(d) is not str or re.fullmatch('[0-9a-f]{64}', d) is None
                           for d in manifest['files'].values())):
                raise ValueError('Natural runtime manifest inventory/digest mismatch')
        if inventory(Path(p['payload_root'])) != set(files) | set(preserved):
            raise ValueError('Payload inventory mismatch')
        for group, root in (('app', self.root), ('units', self.units)):
            safe(root)
            for name, record in self.baseline[group].items():
                relative(name)
                self.match(root / name, record)
        for name, record in p['preserved'].items():
            self.match(self.root / name, record)
        for name, record in {**p['files'], **p['preserved']}.items():
            self.match(Path(p['payload_root']) / name, record)
        selectors = self.baseline['selectors']
        if any(selectors.get(k) != v for k, v in ANCHORS.items()):
            raise ValueError('Incorrect baseline selectors')
        expected = {'VOICE_SELA_LIVE_TTS': '1',
                    'VOICE_SELA_LIVE_PYTHON': selectors.get('VOICE_SUPERTONIC3_PYTHON'),
                    'VOICE_SELA_LIVE_WORKER': str(self.root / 'sela_live_tts/worker.py'),
                    'VOICE_SELA_LIVE_WORKER_SHA256': files['sela_live_tts/worker.py']['sha256'],
                    'VOICE_SELA_LIVE_LOCK': str(self.root / '.runtime/live-tts.lock')}
        if 'VOICE_SELA_LIVE_POLICY' in p['live_env']:
            if selectors.get('VOICE_EXPERIMENTAL_DELIVERY_PROSODY', '0') != '0':
                raise ValueError('Natural policy requires disabled experimental prosody')
            expected.update({
                'VOICE_SELA_LIVE_POLICY': 'natural_v1',
                'VOICE_SELA_LIVE_WORKER': str(self.root / 'sela_live_tts/natural_worker.py'),
                'VOICE_SELA_LIVE_WORKER_SHA256': files['sela_live_tts/natural_worker.py']['sha256'],
                'VOICE_SELA_LIVE_DURATION_WORKER': str(self.root / 'sela_live_tts/duration_worker.py'),
                'VOICE_SELA_LIVE_DURATION_WORKER_SHA256': files['sela_live_tts/duration_worker.py']['sha256'],
                'VOICE_SELA_LIVE_SOURCE_MANIFEST_SHA256': files['sela_live_tts/upstream-manifest.json']['sha256'],
                'VOICE_SELA_LIVE_RUNTIME_MANIFEST': str(self.root / 'sela_live_tts/natural-runtime-manifest.json'),
                'VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256': files['sela_live_tts/natural-runtime-manifest.json']['sha256']})
        if p['live_env'] != expected or any(type(v) is not str or not re.fullmatch(r'[A-Za-z0-9_./:-]+', v) for v in expected.values()):
            raise ValueError('Incorrect live selectors')
        python = Path(expected['VOICE_SELA_LIVE_PYTHON'])
        if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError('Missing executable runtime')
        for group, root, names in (('app', self.root, files), ('units', self.units, [DROPIN])):
            for name in names:
                path = safe(root / name)
                if path.exists() and name not in self.baseline[group]:
                    raise ValueError('Unbound existing destination')
        runtime = safe(self.root / '.runtime')
        if runtime.exists() and (not runtime.is_dir() or runtime.stat().st_mode & 0o7777 != 0o700 or runtime.stat().st_uid != os.geteuid()):
            raise ValueError('Unsafe persistent lock directory')
        lock = safe(runtime / 'live-tts.lock')
        if lock.exists():
            self.lock_stat(lock.stat())
        self.check_inventory()
        if observe:
            self.observe(selectors)

    def check_inventory(self, installed=False):
        for group, root, trees in (('app', self.root, set(self.baseline.get('trees', [])) | {'voice_app', 'web', 'voice_assets', 'sela_live_tts', 'vendor/supertonic'}),
                                   ('units', self.units, {'voice-lab.service.d'})):
            expected = set(self.baseline[group])
            if installed:
                expected |= set(self.plan['files']) if group == 'app' else {DROPIN}
            for tree in trees:
                relative(tree)
                actual = {tree + '/' + name for name in inventory(root / tree)}
                if actual != {name for name in expected if name.startswith(tree + '/')}:
                    raise ValueError('Live source/unit inventory drift')

    @staticmethod
    def lock_stat(s):
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
            raise ValueError('Unsafe persistent lock')

    @staticmethod
    def match(path, record):
        safe(path)
        s = path.stat()
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
            raise ValueError('Nonregular or hardlinked file')
        raw = path.read_bytes()
        if sha(raw) != record['sha256'] or s.st_mode & 0o7777 != record['mode']:
            raise ValueError('File hash/mode drift: ' + str(path))
        return raw

    def observe(self, selectors):
        actual = self.hooks.observe()
        if actual.get('healthy') is not True or actual.get('selectors') != selectors:
            raise ValueError('Runtime health/selectors mismatch')

    @staticmethod
    def atomic(path, raw, mode):
        safe(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.sela-write-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                os.fchmod(stream.fileno(), mode)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def archive(self):
        # Never learn an approved baseline from a fresh archive.
        self.preflight(observe=False)
        safe(self.snapshots)
        self.snapshots.mkdir(mode=0o700, parents=True, exist_ok=True)
        s = self.snapshots.stat()
        if not stat.S_ISDIR(s.st_mode) or stat.S_IMODE(s.st_mode) != 0o700 or s.st_uid != os.geteuid():
            raise ValueError('Unsafe snapshot parent')
        snapshot = self.snapshots / uuid.uuid4().hex
        snapshot.mkdir(mode=0o700)
        manifest = {'app': {}, 'units': {}}
        for group, root, names in (('app', self.root, self.plan['files']),
                                   ('units', self.units, [DROPIN])):
            for name in names:
                path = safe(root / name)
                record: dict = {'exists': path.exists()}
                if record['exists']:
                    approved = self.baseline[group][name]
                    raw = self.match(path, approved)
                    record.update(sha256=approved['sha256'], mode=approved['mode'])
                    destination = snapshot / group / name
                    self.atomic(destination, raw, 0o400)
                    self.match(destination, {'sha256': record['sha256'], 'mode': 0o400})
                manifest[group][name] = record
        self.atomic(snapshot / 'snapshot.json', canonical(manifest), 0o400)
        self.match(snapshot / 'snapshot.json', {'sha256': sha(canonical(manifest)), 'mode': 0o400})
        for directory in sorted((p for p in snapshot.rglob('*') if p.is_dir()), reverse=True):
            directory.chmod(0o500)
        snapshot.chmod(0o500)
        return snapshot, manifest

    def readback(self):
        self.check_inventory(installed=True)
        for name, record in self.plan['files'].items():
            self.match(self.root / name, record)
        self.match(self.units / DROPIN, {'sha256': sha(self.dropin()), 'mode': 0o644})
        for group, root in (('app', self.root), ('units', self.units)):
            for name, record in self.baseline[group].items():
                if not (group == 'app' and name in self.plan['files']) and not (group == 'units' and name == DROPIN):
                    self.match(root / name, record)
        self.observe({**self.baseline['selectors'], **self.plan['live_env']})

    def dropin(self):
        return ('[Service]\n' + ''.join('Environment="' + k + '=' + v + '"\n'
                for k, v in sorted(self.plan['live_env'].items()))).encode()

    def run(self, *, apply=False):
        if not apply:
            self.preflight()
            return {'status': 'ready', 'applied': False}
        fd = os.open(safe(self.root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.preflight()
            return self.apply()
        finally:
            os.close(fd)

    def apply(self):
        snapshot, manifest = self.archive()
        receipt = {'status': 'archived', 'applied': False, 'snapshot': str(snapshot)}
        receipt_path = self.snapshots / (snapshot.name + '.receipt.json')
        self.atomic(receipt_path, canonical(receipt), 0o600)
        try:
            self.hooks.stop()
            runtime = safe(self.root / '.runtime')
            runtime.mkdir(mode=0o700, exist_ok=True)
            if runtime.stat().st_mode & 0o7777 != 0o700 or runtime.stat().st_uid != os.geteuid():
                raise ValueError('Unsafe persistent lock directory')
            lock = runtime / 'live-tts.lock'
            fd = os.open(safe(lock), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                s = os.fstat(fd)
                if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
                    raise ValueError('Unsafe persistent lock')
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for name, record in self.plan['files'].items():
                    source = Path(self.plan['payload_root']) / name
                    raw = self.match(source, record)
                    self.atomic(self.root / name, raw, record['mode'])
                self.atomic(self.units / DROPIN, self.dropin(), 0o644)
            finally:
                os.close(fd)
            self.hooks.restart()
            self.readback()
            receipt.update(status='installed', applied=True)
        except BaseException as exc:
            receipt.update(status='rolling_back', error=type(exc).__name__)
            errors = []
            try:
                self.hooks.stop()
            except BaseException as failure:
                errors.append('stop:' + type(failure).__name__)
            if not errors:  # Never rewrite code without confirmed quiescence.
                for group, root in (('app', self.root), ('units', self.units)):
                    for name, record in manifest[group].items():
                        try:
                            path = safe(root / name)
                            if record['exists']:
                                raw = (snapshot / group / name).read_bytes()
                                if sha(raw) != record['sha256']:
                                    raise ValueError('Snapshot corruption')
                                self.atomic(path, raw, record['mode'])
                                self.match(path, record)
                            elif path.exists():
                                path.unlink()
                        except BaseException as failure:
                            errors.append('restore:' + type(failure).__name__)
            if not errors:
                try:
                    # Verify all protected bytes/modes and removed destinations
                    # BEFORE any recovery restart, not after starting mixed code.
                    for group, root in (('app', self.root), ('units', self.units)):
                        for name, record in self.baseline[group].items():
                            self.match(root / name, record)
                        for name, record in manifest[group].items():
                            if not record['exists'] and safe(root / name).exists():
                                raise ValueError('New destination remains after recovery')
                    self.check_inventory()
                except BaseException as failure:
                    errors.append('pre_restart:' + type(failure).__name__)
            if not errors:
                try:
                    self.hooks.restart()
                    for group, root in (('app', self.root), ('units', self.units)):
                        for name, record in self.baseline[group].items():
                            self.match(root / name, record)
                    self.check_inventory()
                    self.observe(self.baseline['selectors'])
                except BaseException as failure:
                    errors.append('recovery:' + type(failure).__name__)
                    # Restart may have started a process before failing; a
                    # post-restart validation error also leaves it unverified.
                    # Stop once more and never report success if that stop fails.
                    receipt['recovery_final_stop_succeeded'] = False
                    try:
                        self.hooks.stop()
                        receipt['recovery_final_stop_succeeded'] = True
                    except BaseException as final_failure:
                        errors.append('final_stop:' + type(final_failure).__name__)
            receipt.update(status='rollback_failed' if errors else 'rolled_back', cleanup_errors=errors)
        try:
            self.atomic(receipt_path, canonical(receipt), 0o600)
        except BaseException as failure:
            receipt.update(status='receipt_failed', applied=False, receipt_error=type(failure).__name__)
        return receipt


class SystemHooks:
    """Fixed production service and loopback probes; no inference or credentials."""
    @staticmethod
    def command(*args):
        import subprocess
        result = subprocess.run(['/usr/bin/systemctl', *args], capture_output=True,
                                text=True, timeout=60, check=False)
        if result.returncode:
            raise ValueError('systemd command failed')
        return result.stdout

    @staticmethod
    def process(pid):
        proc = Path('/proc') / str(pid)
        selectors = {}
        for item in (proc / 'environ').read_bytes().split(b'\0'):
            key, sep, value = item.partition(b'=')
            name = key.decode('ascii', errors='ignore')
            if sep and name in SELECTOR_KEYS | LIVE_KEYS:
                selectors[name] = value.decode('utf-8')
        sockets = set()
        for fd in (proc / 'fd').iterdir():
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            if target.startswith('socket:['):
                sockets.add(target[8:-1])
        listeners = []
        for table in ('tcp', 'tcp6'):
            for line in (proc / 'net' / table).read_text().splitlines()[1:]:
                row = line.split()
                if row[3] == '0A' and row[9] in sockets:
                    listeners.append(row[1])
        return selectors, listeners == ['0100007F:' + format(8091, '04X')] and (proc / 'cwd').resolve() == Path('/root/voice-lab')

    @staticmethod
    def http(path, *, host='127.0.0.1:8091', websocket=False):
        import http.client
        connection = http.client.HTTPConnection('127.0.0.1', 8091, timeout=5)
        headers = {'Host': host}
        if websocket:
            headers.update({'Connection': 'Upgrade', 'Upgrade': 'websocket',
                            'Sec-WebSocket-Version': '13',
                            'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
                            'Origin': 'https://voice.kasirdigital.web.id'})
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(4097)
        finally:
            connection.close()

    def observe(self):
        properties = dict(line.split('=', 1) for line in self.command('show', 'voice-lab.service',
                          '--property=ActiveState,SubState,MainPID,WorkingDirectory').splitlines() if '=' in line)
        if properties.get('ActiveState') != 'active' or properties.get('SubState') != 'running' or properties.get('WorkingDirectory') != '/root/voice-lab':
            raise ValueError('Service is not active at fixed application root')
        pid = int(properties['MainPID'])
        if pid <= 1:
            raise ValueError('Missing running process')
        selectors, guarded = self.process(pid)
        if not guarded:
            raise ValueError('Process listener/root guard mismatch')
        status, raw = self.http('/health')
        if status != 200 or json.loads(raw) != {'status': 'ok'}:
            raise ValueError('Live health mismatch')
        if self.http('/api/config')[0] != 401 or self.http('/health', host='untrusted.invalid')[0] != 400 or self.http('/ws', websocket=True)[0] != 403:
            raise ValueError('Live authentication/Host guard mismatch')
        return {'healthy': True, 'selectors': selectors}

    def stop(self):
        self.command('stop', 'voice-lab.service')

    def restart(self):
        import time
        self.command('daemon-reload')
        self.command('restart', 'voice-lab.service')
        # Startup verifies seven pinned profiles, each with a 15s safety cap.
        # Keep a finite startup allowance without rolling back a healthy cold boot.
        deadline = time.monotonic() + 120
        for _ in range(480):
            if time.monotonic() >= deadline:
                break
            try:
                status, raw = self.http('/health')
                if status == 200 and json.loads(raw) == {'status': 'ok'}:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        raise ValueError('Restart health deadline exceeded')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        path = safe(Path(args.plan).absolute())
        plan = json.loads(path.read_bytes())
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode not in (0o600, 0o644) or plan.get('approval', {}).get('plan_mode') != mode:
            raise ValueError('Plan mode drift')
        installer = Installer(plan, root=Path('/root/voice-lab'), units=Path('/etc/systemd/system'),
                              snapshots=Path('/root/sela-live-install-snapshots'), hooks=SystemHooks())
        receipt = installer.run(apply=args.apply)
        print(json.dumps(receipt))
        return 0 if receipt['status'] in ('ready', 'installed') else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'rejected', 'error': type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
