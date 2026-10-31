"""Owned asynchronous DP-only session. Callers supply reviewed source digests.

Production must own/quiesce Speech's local/ASR slot before injecting its
ProductionAdmissionCoordinator. The default coordinator is evaluation-only.
"""
import asyncio
import hashlib
import json
import math
import os
import re
import stat
import signal
from pathlib import Path

from .admission import AdmissionCoordinator, LiveTTSBusy

MAX_QUERIES = 64
LINE_BYTES = 4096
STDOUT_BYTES = 32768
STDERR_BYTES = 8192
PROFILES = ('conditioning_b',) + tuple(
    f'conditioning_b_{a}_{i}' for a in ('calm', 'active') for i in ('low', 'medium', 'high'))


def validate_text(text):
    if (type(text) is not str or not text.strip() or len(text) > 800
            or len(text.encode('utf8')) > 3200):
        raise ValueError('text_limit')
    if any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise ValueError('text_control')
    stripped = re.sub(r'<(?:laugh|breath|sigh)>', '', text)
    if '<' in stripped or '>' in stripped or len(re.findall(r'<(?:laugh|breath|sigh)>', text)) > 3:
        raise ValueError('unsupported_tag')
    return text


def _offline_env():
    """No caller credentials, proxies, loader hooks or cache configuration."""
    return dict(PATH='/usr/bin:/bin', HOME='/nonexistent', LANG='C.UTF-8',
                HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS='2',
                OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')


def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Explicit reviewed SHA256 required')
    return value


def _path(value, *, directory=False, interpreter=False):
    path = Path(value)
    if (not path.is_absolute() or '..' in path.parts
            or (not interpreter and any(p.is_symlink() for p in (path, *path.parents)))):
        raise ValueError('Unsafe duration path')
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError('Invalid duration path')
    return path


class DurationSession:
    """Single-use async context; no queued predictions; fixed session profile.

    process_factory has asyncio.create_subprocess_exec's signature. It and the
    coordinator are trusted dependency-injection seams, not request controls.
    """
    def __init__(self, *, worker_sha256, source_manifest_sha256,
                 python='/root/voice-lab/autonomous/expressive-tts/venv/bin/python',
                 assets='/root/voice-lab/autonomous/expressive-tts/assets',
                 model_manifest='/root/voice-lab/autonomous/expressive-tts/assets-manifest.json',
                 worker=None, profile_id='conditioning_b', coordinator=None,
                 process_factory=None, ready_timeout=15., query_timeout=5., session_timeout=70.):
        self.worker_sha256 = _digest(worker_sha256)
        self.source_manifest_sha256 = _digest(source_manifest_sha256)
        self.python = _path(python, interpreter=True)
        if not os.access(self.python, os.X_OK):
            raise ValueError('Executable interpreter required')
        self.assets = _path(assets, directory=True)
        self.model_manifest = _path(model_manifest)
        self.worker = _path(Path(__file__).absolute().with_name('duration_worker.py') if worker is None else worker)
        if type(profile_id) is not str or profile_id not in PROFILES:
            raise ValueError('Invalid duration profile')
        for value, maximum in ((ready_timeout, 15), (query_timeout, 10), (session_timeout, 70)):
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError('Invalid bounded deadline')
        self.profile_id = profile_id
        self.ready_timeout, self.query_timeout, self.session_timeout = ready_timeout, query_timeout, session_timeout
        self.coordinator = AdmissionCoordinator() if coordinator is None else coordinator
        self.factory = asyncio.create_subprocess_exec if process_factory is None else process_factory
        self.owned_tasks = []
        self.process = None
        self._lease = None
        self._entered = False
        self._active = False
        self._busy = False
        self._count = 0
        self._cleanup_task = None
        self.failure_reason = None
        self.returncode = None

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.owned_tasks.append(task)
        return task

    async def __aenter__(self):
        if self._entered:
            raise RuntimeError('Duration session is single-use')
        self._entered = True
        self._queue = asyncio.Queue(maxsize=2)
        self._failure = asyncio.get_running_loop().create_future()
        try:
            self._lease = self.coordinator.claim()
            self._lease.__enter__()
        except BaseException:
            self._lease = None
            raise
        try:
            # Check every spawn, not just construction; never trust-on-first-use.
            worker = _path(self.worker)
            if worker.stat().st_size > 1_000_000 or hashlib.sha256(worker.read_bytes()).hexdigest() != self.worker_sha256:
                raise ValueError('Duration worker drift')
            from .source_pins import verify_sources
            verify_sources(worker.with_name('upstream-manifest.json'), self.source_manifest_sha256)
            _path(self.assets, directory=True)
            _path(self.model_manifest)
            _path(self.python, interpreter=True)
            env = _offline_env()
            self._spawn = self._task(self.factory(str(self.python), '-I', str(worker), 'serve',
                str(self.assets), str(self.model_manifest), self.profile_id, self.source_manifest_sha256,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env, limit=LINE_BYTES + 1, start_new_session=True))
            self._started = asyncio.get_running_loop().time()
            self.process = await asyncio.wait_for(asyncio.shield(self._spawn), self.ready_timeout)
            self._task(self._stdout())
            self._task(self._stderr())
            self._task(self._monitor())
            ready = await self._receive(self.ready_timeout)
            if (type(ready) is not dict or set(ready) != {'type', 'protocol', 'max_queries', 'native_network_denied'}
                    or ready['type'] != 'ready' or type(ready['protocol']) is not int or ready['protocol'] != 1
                    or type(ready['max_queries']) is not int or ready['max_queries'] != 64
                    or ready['native_network_denied'] is not True):
                raise ValueError('Invalid duration ready')
            self.ready = ready
            self._active = True
            return self
        except BaseException:
            await self._close()
            raise

    def _fail(self, error):
        if not self._failure.done():
            # Store as a result, avoiding unobserved Future exceptions.
            self._failure.set_result(error)

    async def _stdout(self):
        total = 0
        last = None
        try:
            while True:
                line = await self.process.stdout.readline()
                total += len(line)
                if not line:
                    if self._count == MAX_QUERIES and type(last) is dict and last.get('id') == MAX_QUERIES:
                        return
                    raise ValueError('Duration child EOF')
                if len(line) > LINE_BYTES or total > STDOUT_BYTES or not line.endswith(b'\n'):
                    raise ValueError('Duration stdout limit')
                value = json.loads(line.decode('utf8'), object_pairs_hook=_unique_object,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
                self._queue.put_nowait(value)
                last = value
        except Exception:
            self._fail(ValueError('Duration invalid stdout'))

    async def _stderr(self):
        total = 0
        pending = b''
        safe = frozenset({'asset_error','invalid_request','invalid_text','resource_limit',
                          'runtime_version','network_sandbox','duration_limit','output_limit',
                          'memory_headroom','memory_allocation','native_error'})
        try:
            while chunk := await self.process.stderr.read(1024):
                total += len(chunk)
                if total > STDERR_BYTES:
                    raise ValueError('Duration stderr limit')
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    if len(line) + 1 > LINE_BYTES:
                        raise ValueError('Duration stderr limit')
                    prefix = b'supertonic3_failure='
                    if line.startswith(prefix):
                        reason = line[len(prefix):].decode('ascii', errors='replace')
                        if reason in safe:
                            self.failure_reason = reason
                if len(pending) >= LINE_BYTES:
                    raise ValueError('Duration stderr limit')
        except Exception:
            self._fail(ValueError('Duration invalid stderr'))

    async def _monitor(self):
        try:
            while True:
                self.coordinator.check_production()
                if asyncio.get_running_loop().time() - self._started >= self.session_timeout:
                    raise TimeoutError('Duration session deadline')
                await asyncio.sleep(min(.05, max(.001, self.coordinator.poll_interval)))
        except Exception as error:
            self._fail(error)
            # Also terminate an idle session; context may be between queries.
            if self.process.returncode is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass

    async def _receive(self, timeout):
        get = self._task(self._queue.get())
        try:
            done, _ = await asyncio.wait((get, self._failure), timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if self._failure in done:
                raise self._failure.result()
            if get not in done:
                raise TimeoutError('Duration query deadline')
            return get.result()
        finally:
            if not get.done():
                get.cancel()
            await asyncio.gather(get, return_exceptions=True)

    async def predict(self, normalized_text, *, profile_id=None):
        validate_text(normalized_text)
        if profile_id is not None and (type(profile_id) is not str or profile_id != self.profile_id):
            raise ValueError('Duration session profile mismatch')
        if not self._active:
            raise RuntimeError('Duration session inactive')
        if self._busy:
            raise LiveTTSBusy('Duration already active; zero queued waiters')
        if self._count >= MAX_QUERIES:
            raise ValueError('Duration query budget')
        self._busy = True
        try:
            self.coordinator.check_production()
            self._count += 1
            payload = json.dumps({'id':self._count, 'text':normalized_text}, ensure_ascii=False, allow_nan=False).encode() + b'\n'
            async with asyncio.timeout(self.query_timeout):
                if self._failure.done():
                    raise self._failure.result()
                self.process.stdin.write(payload)
                await self.process.stdin.drain()
                result = await self._receive(self.query_timeout)
            if (type(result) is dict and set(result) == {'id', 'seconds', 'tokens', 'refusal'}
                    and type(result['id']) is int and result['id'] == self._count
                    and result['seconds'] is None
                    and type(result['refusal']) is str and result['refusal'] == 'model_token_limit'
                    and type(result['tokens']) is int and 513 <= result['tokens'] <= 32768):
                self.coordinator.check_production()
                return {'seconds': None, 'tokens': result['tokens'], 'refusal': result['refusal']}
            if (type(result) is not dict or set(result) != {'id', 'seconds', 'tokens'}
                    or type(result['id']) is not int or result['id'] != self._count
                    or type(result['seconds']) not in (int, float)
                    or not math.isfinite(result['seconds']) or not 0 < result['seconds'] <= 3600
                    or type(result['tokens']) is not int or not 1 <= result['tokens'] <= 512):
                raise ValueError('Invalid duration result')
            self.coordinator.check_production()
            return {'seconds':float(result['seconds']), 'tokens':result['tokens']}
        except BaseException:
            await self._close()
            raise
        finally:
            self._busy = False

    async def _cleanup(self):
        terminal = False
        try:
            spawn = getattr(self, '_spawn', None)
            if spawn is not None:
                # A cancelled enter must still own a process created mid-spawn.
                try:
                    self.process = await spawn
                except BaseException:
                    pass
            if self.process is not None:
                if self.process.stdin is not None:
                    self.process.stdin.close()
                # Own a fresh process group, including inherited-pipe children.
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            tasks = [t for t in self.owned_tasks if t is not asyncio.current_task()]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.process is not None:
                # Failed readers can leave StreamReader paused at its high water
                # mark. Drain after kill, without accumulating any native output.
                async def discard(stream):
                    if stream is not None:
                        while await stream.read(2048):
                            pass
                await asyncio.gather(discard(self.process.stdout), discard(self.process.stderr))
                self.returncode = await self.process.wait()
            terminal = True
        finally:
            if terminal and self._lease is not None:
                self._lease.__exit__(None, None, None)
                self._lease = None

    async def _close(self):
        self._active = False
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup())
        cancelled = False
        while not self._cleanup_task.done():
            try:
                await asyncio.shield(self._cleanup_task)
            except asyncio.CancelledError:
                cancelled = True
        self._cleanup_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def __aexit__(self, *args):
        await self._close()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result
