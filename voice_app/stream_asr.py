"""Single offline native ASR process; cancellation never releases live capacity.

No unbounded queue: previews drop when busy; one final waiter is permitted.
All PCM/text lives in bounded pipes/memory only. Caller owns stale-result checks.
"""
import asyncio
import json
import struct
import math
from contextlib import asynccontextmanager

class PersistentASR:
    def __init__(self, args, env, *, final_args=None, idle_seconds=60):
        if type(idle_seconds) not in (int, float) or not math.isfinite(idle_seconds) or not 0 < idle_seconds <= 600:
            raise ValueError('ASR idle deadline must be finite and bounded')
        self.idle_seconds = idle_seconds
        self._idle_task = None
        self._closing = False
        self.final_args = final_args or args
        self._active_args = None
        self.args, self.env = args, env
        self._lock = asyncio.Lock()
        self._proc = None
        self._job = None
        self._final_waiting = False
        self.inference_count = 0
        self.last_metrics = {}

    @property
    def pid(self):
        return self._proc.pid if self._proc and self._proc.returncode is None else None

    @property
    def busy(self):
        return self._lock.locked() or self._final_waiting or self._closing

    def _cancel_idle(self):
        task, self._idle_task = self._idle_task, None
        if task and task is not asyncio.current_task():
            task.cancel()

    def _arm_idle(self):
        self._cancel_idle()
        async def expire():
            await asyncio.sleep(self.idle_seconds)
            async with self._lock:
                await self._reap()
        self._idle_task = asyncio.create_task(expire())

    async def _start(self, final=False):
        self._cancel_idle()
        args = self.final_args if final else self.args
        if self.pid is not None and self._active_args == args:
            return
        await self._reap()
        self._active_args = args
        self._proc = await asyncio.create_subprocess_exec(*args, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=65536)
        if await self._proc.stdout.readline() != b'READY\n':
            raise RuntimeError('Local streaming ASR failed to initialize')

    async def _reap(self):
        proc, self._proc = self._proc, None
        if proc:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(),2)
                except TimeoutError:
                    proc.kill()
            await proc.wait()

    async def ready(self):
        if self._closing:
            raise RuntimeError('ASR closing')
        # Include lock admission in the budget. Cleanup is inside ownership:
        # timing out/cancelling a waiter must never reap another caller's work.
        async with asyncio.timeout(25):
            async with self._lock:
                try:
                    await self._start()
                    self._arm_idle()
                except BaseException:
                    await self._reap()
                    raise

    async def _exchange(self, wav, final=False, *, start=True):
        # This task owns the lock and is shielded from its response consumer.
        async with self._lock:
            try:
                async with asyncio.timeout(90):
                    if start:
                        await self._start(final)
                    elif self.pid is None:
                        return ''
                    else:
                        self._cancel_idle()
                    self._proc.stdin.write(struct.pack('!I',len(wav)) + wav)
                    await self._proc.stdin.drain()
                    size = struct.unpack('!I', await self._proc.stdout.readexactly(4))[0]
                    if not 0 < size <= 65536:
                        raise ValueError('ASR output exceeds limit')
                    body=json.loads(await self._proc.stdout.readexactly(size))
                    text=body.get('text')
                    if not isinstance(text,str) or len(text)>4000:
                        raise ValueError('Invalid ASR text')
                    self.last_metrics = body.get('metrics', {})
                    if not isinstance(self.last_metrics, dict):
                        raise ValueError('Invalid ASR metrics')
                    cpu = self.last_metrics.get('cpu_seconds', 0)
                    if type(cpu) not in (int, float) or not math.isfinite(cpu) or not 0 <= cpu <= 185:
                        raise ValueError('Invalid ASR CPU metric')
                    self.inference_count += 1
                    # A request may consume up to 86 CPU seconds. Recycle at
                    # 70, leaving >100 seconds below the 180/185 hard ceiling.
                    if self.last_metrics.get('cpu_seconds', 0) >= 70:
                        await self._reap()
                    else:
                        self._arm_idle()
                    return text.strip()
            except BaseException:
                await self._reap()
                raise

    def _validate_payload(self, wav):
        if type(wav) is not bytes or not 0 < len(wav) <= 640044:
            raise ValueError('Invalid ASR payload')

    async def transcribe(self, wav):
        self._validate_payload(wav)
        if self._final_waiting or self._closing:
            from .speech import LocalAudioBusy
            raise LocalAudioBusy('One final ASR waiter already exists')
        self._final_waiting = True
        try:
            if self._job and not self._job.done():
                await asyncio.shield(self._job)
            job=asyncio.create_task(self._exchange(wav, final=True))
            self._job=job
            job.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            return await asyncio.shield(job)
        finally:
            self._final_waiting=False

    async def preview(self, wav):
        self._validate_payload(wav)
        if self.busy or (self._job and not self._job.done()):
            return None
        # No await between admission and registration: no competing preview
        # can enter before _exchange acquires the lock.
        job=asyncio.create_task(self._exchange(wav))
        self._job=job
        job.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return await asyncio.shield(job)

    @asynccontextmanager
    async def quiesce(self, wait_seconds=3):
        """Retain an existing model, but exclude all native work during TTS.

        Speech holds its local admission slot first. A detached ASR producer
        keeps its own capacity on cancellation/timeout; never cancel or reap it.
        """
        try:
            async with asyncio.timeout(wait_seconds):
                if self._job and not self._job.done():
                    await asyncio.shield(self._job)
                await self._lock.acquire()
        except TimeoutError as exc:
            from .speech import LocalAudioBusy
            raise LocalAudioBusy('ASR still active before TTS') from exc
        try:
            self._cancel_idle()
            yield
        finally:
            self._arm_idle()
            self._lock.release()

    async def close(self):
        self._closing = True
        try:
            self._cancel_idle()
            if self._job and not self._job.done():
                await asyncio.shield(self._job)
        finally:
            async with self._lock:
                self._cancel_idle()
                await self._reap()
            self._closing = False
