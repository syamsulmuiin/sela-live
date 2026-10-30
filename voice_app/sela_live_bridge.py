"""Explicit installed cloned-SDK worker selection; never an evaluation adapter."""
from pathlib import Path
from contextlib import contextmanager


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
