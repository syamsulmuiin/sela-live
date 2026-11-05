"""Explicit installed cloned-SDK worker selection; never an evaluation adapter."""
from pathlib import Path
from contextlib import contextmanager


# Exact reviewed dependency inventory; a new dependency needs review here too.
NATURAL_RUNTIME_FILES = frozenset(
    'sela_live_tts/' + name for name in (
        '__init__.py', 'backend.py', 'admission.py', 'controls.py', 'source_pins.py',
        'worker.py', 'natural.py', 'utterance.py', 'duration.py', 'natural_worker.py',
        'duration_worker.py', 'upstream-manifest.json')) | frozenset(
    'voice_app/' + name for name in (
        'approved_conditioning.py', 'automatic_conditioning.py', 'supertonic3_worker.py')) | frozenset(
    'vendor/supertonic/' + name for name in (
        '__init__.py', 'cli.py', 'config.py', 'core.py', 'loader.py', 'pipeline.py', 'utils.py',
        'server/__init__.py', 'server/app.py', 'server/audio.py', 'server/routes.py',
        'server/schemas.py', 'server/styles_store.py')) | frozenset((
    'sela_live/__init__.py', 'sela_live/audio/__init__.py', 'sela_live/audio/stt.py',
    'sela_live/audio/tts.py', 'voice_app/zipformer_state.py'))


def _pinned_source(path, digest):
    """Bounded read with no-follow directory descriptors, including ancestors."""
    import hashlib
    import os
    import re
    import stat
    if (type(path) is not str or not path.startswith('/') or '\\' in path
            or any(p in ('', '.', '..') for p in path.split('/')[1:])
            or type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None):
        raise ValueError('Invalid natural runtime pin')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        pieces = path.split('/')[1:]
        for piece in pieces[:-1]:
            child = os.open(piece, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(pieces[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        with os.fdopen(child, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('Not a regular natural source')
            raw = source.read(1_000_001)
        if len(raw) > 1_000_000 or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Natural runtime source drift')
        return raw
    finally:
        os.close(fd)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate natural manifest key')
        result[key] = value
    return result


class SelaLiveBridge:
    def __init__(self, env):
        import os
        import re
        keys = ('VOICE_SELA_LIVE_PYTHON', 'VOICE_SELA_LIVE_WORKER',
                'VOICE_SELA_LIVE_LOCK', 'VOICE_SELA_LIVE_WORKER_SHA256')
        if any(type(env.get(key)) is not str or not env[key] for key in keys):
            raise ValueError('Sela Live requires explicit installed runtime, worker, digest and lock; no fallback')
        self.python = env[keys[0]]
        self.worker, self.lock_path = Path(env[keys[1]]), Path(env[keys[2]])
        self.digest = env[keys[3]]
        paths = (Path(self.python), self.worker, self.lock_path)
        if (any(not p.is_absolute() or '..' in p.parts for p in paths)
                or re.fullmatch('[0-9a-f]{64}', self.digest) is None
                or not Path(self.python).is_file() or not os.access(self.python, os.X_OK)
                or not self.lock_path.parent.is_dir()
                or any(p.is_symlink() for p in (self.lock_path, *self.lock_path.parents))):
            raise ValueError('Invalid installed Sela Live configuration; no fallback')
        self._verify_worker()
        self.natural = env.get('VOICE_SELA_LIVE_POLICY') == 'natural_v1'
        if self.natural:
            self.duration_worker = env.get('VOICE_SELA_LIVE_DURATION_WORKER')
            self.duration_digest = env.get('VOICE_SELA_LIVE_DURATION_WORKER_SHA256')
            self.source_digest = env.get('VOICE_SELA_LIVE_SOURCE_MANIFEST_SHA256')
            self.runtime_manifest = env.get('VOICE_SELA_LIVE_RUNTIME_MANIFEST')
            self.runtime_digest = env.get('VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256')
            self.verify_runtime()

    def verify_runtime(self):
        """No candidate imports until the external hash and full set are checked."""
        import json
        try:
            raw = _pinned_source(self.runtime_manifest, self.runtime_digest)
            data = json.loads(raw, object_pairs_hook=_unique_object)
            if (type(data) is not dict or set(data) != {'schema_version', 'files'}
                    or type(data['schema_version']) is not int or data['schema_version'] != 1
                    or type(data['files']) is not dict or set(data['files']) != NATURAL_RUNTIME_FILES):
                raise ValueError('Invalid natural runtime inventory')
            root = self.worker.parent.parent
            if (self.worker != root / 'sela_live_tts/natural_worker.py'
                    or self.duration_worker != str(root / 'sela_live_tts/duration_worker.py')
                    or self.runtime_manifest != str(root / 'sela_live_tts/natural-runtime-manifest.json')):
                raise ValueError('Natural worker layout mismatch')
            files = data['files']
            for relative, digest in files.items():
                _pinned_source(str(root / relative), digest)
            for relative, expected in (
                    ('sela_live_tts/natural_worker.py', self.digest),
                    ('sela_live_tts/duration_worker.py', self.duration_digest),
                    ('sela_live_tts/upstream-manifest.json', self.source_digest)):
                if type(expected) is not str or files[relative] != expected:
                    raise ValueError('Independent natural pin mismatch')
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError('Reviewed natural runtime unavailable; no fallback') from None

    def _verify_worker(self):
        try:
            from .approved_conditioning import _read_pinned
            _read_pinned(self.worker, self.digest)
        except (OSError, ValueError):
            raise ValueError('Reviewed Sela Live worker unavailable; no fallback') from None

    @contextmanager
    def claim(self):
        from sela_live_tts.admission import ProductionAdmissionCoordinator, LiveTTSBusy
        from .speech import LocalAudioBusy
        # Only acquisition errors are translated; worker errors retain metadata.
        from contextlib import ExitStack
        with ExitStack() as retained:
            try:
                retained.enter_context(ProductionAdmissionCoordinator(self.lock_path).claim())
            except LiveTTSBusy:
                raise LocalAudioBusy('Sela Live host audio worker busy') from None
            yield

    async def run(self, native, args, payload, limit, *, env):
        """Keep host admission and ASR quiescence until cancellation is reaped.

        Cancel the child-owning task only once. Further caller cancellations
        cannot interrupt its terminate/kill/wait finally block.
        """
        import asyncio
        task = asyncio.create_task(native(args, payload, limit, env=env, native_diagnostics=True))
        try:
            return await asyncio.shield(task)
        except BaseException:
            if not task.done() and not task.cancelling():
                task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise

    def args(self, action, assets, manifest, profile_id):
        self._verify_worker()
        return (self.python, '-I', str(self.worker), action, assets, manifest, profile_id, '12')
