"""Host admission regressions: temporary locks and harmless children only."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from sela_live_tts.backend import SelaLiveTTS


class AdmissionBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.lock_path = Path(self.directory.name) / 'evaluation.lock'

    def client(self, native, **kwargs):
        from sela_live_tts.admission import AdmissionCoordinator
        kwargs['coordinator'] = AdmissionCoordinator(self.lock_path, production_probe=lambda: False)
        return SelaLiveTTS(native_call=native, **kwargs)

    async def test_two_instances_cannot_enter_native_concurrently(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum = 0
        async def native(*args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            entered.set()
            try:
                await release.wait()
            finally:
                active -= 1
            raise RuntimeError('fixture completed')
        first = asyncio.create_task(self.client(native).synthesize('Tetap.'))
        await entered.wait()
        second = asyncio.create_task(self.client(native).synthesize('Tetap.'))
        try:
            await asyncio.sleep(.03)
            self.assertEqual(maximum, 1, 'S1: two adapter instances entered native concurrently')
            self.assertTrue(second.done(), 'contention must fail fast')
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)

    async def test_excess_arrivals_have_zero_queued_waiters(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def native(*args, **kwargs):
            entered.set()
            await release.wait()
            raise RuntimeError('fixture completed')
        client = self.client(native)
        first = asyncio.create_task(client.synthesize('Tetap.'))
        await entered.wait()
        arrivals = [asyncio.create_task(client.synthesize('Tetap.')) for _ in range(4)]
        try:
            await asyncio.sleep(.03)
            self.assertTrue(all(t.done() for t in arrivals), 'L1: excess arrivals queued behind active native')
            from sela_live_tts.admission import LiveTTSBusy
            self.assertTrue(all(isinstance(t.exception(), LiveTTSBusy) for t in arrivals))
        finally:
            release.set()
            await asyncio.gather(first, *arrivals, return_exceptions=True)

    async def test_production_start_or_probe_error_cancels_owned_work_and_keeps_lock(self):
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        for fail_probe in (False, True):
            with self.subTest(fail_probe=fail_probe):
                production = False
                entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
                def probe():
                    if production and fail_probe:
                        raise PermissionError('fixture probe error')
                    return production
                coordinator = AdmissionCoordinator(self.lock_path, production_probe=probe, poll_interval=.005)
                async def native(*args, **kwargs):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleaning.set()
                        await release.wait()
                client = SelaLiveTTS(native_call=native, coordinator=coordinator)
                task = asyncio.create_task(client.synthesize('Tetap.'))
                await entered.wait()
                production = True
                try:
                    try:
                        await asyncio.wait_for(cleaning.wait(), .3)
                    except asyncio.TimeoutError:
                        self.fail('production change did not cancel owned evaluation')
                    task.cancel()  # repeated caller cancellation cannot unlock early
                    await asyncio.sleep(0)
                    self.assertTrue(client._slot.locked())
                    contender = AdmissionCoordinator(self.lock_path, production_probe=lambda: False)
                    with self.assertRaises(LiveTTSBusy), contender.claim():
                        self.fail('host lock released during cleanup')
                finally:
                    release.set()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                with AdmissionCoordinator(self.lock_path, production_probe=lambda: False).claim():
                    pass

    async def test_production_busy_before_admission_never_calls_native(self):
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        async def native(*args, **kwargs):
            self.fail('production busy must prevent native start')
        client = SelaLiveTTS(native_call=native, coordinator=AdmissionCoordinator(
            self.lock_path, production_probe=lambda: True))
        with self.assertRaises(LiveTTSBusy):
            await client.synthesize('Tetap.')

    async def test_native_errors_forward_unchanged_and_release(self):
        from voice_app.speech import NativeTTSFailure
        errors = [NativeTTSFailure(reason, 2) for reason in (
            'memory_headroom', 'resource_limit', 'network_sandbox', 'runtime_version',
            'duration_limit', 'asset_error')] + [OSError('fixture native IO')]
        for error in errors:
            async def native(*args, **kwargs):
                raise error
            client = self.client(native)
            with self.assertRaises(type(error)) as raised:
                await client.synthesize('Tetap.')
            self.assertIs(raised.exception, error)
            from sela_live_tts.admission import AdmissionCoordinator
            with AdmissionCoordinator(self.lock_path, production_probe=lambda: False).claim():
                pass

    async def test_process_backed_cleanup_holds_host_lock_through_repeated_cancellation(self):
        import os
        import sys
        from voice_app.speech import _bounded_process
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        for production_start in (False, True):
            with self.subTest(production_start=production_start):
                pidfile = Path(self.directory.name) / 'owned-child.pid'
                pidfile.unlink(missing_ok=True)
                production = False
                coordinator = AdmissionCoordinator(self.lock_path, production_probe=lambda: production,
                                                   poll_interval=.005)
                script = ('import os,signal,time,pathlib;'
                          'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                          f'p=pathlib.Path({str(pidfile)!r});'
                          'ready=p.with_name(p.name+".ready");'
                          'ready.write_text(str(os.getpid()));ready.replace(p);'
                          'time.sleep(30)')
                async def native(*args, **kwargs):
                    return await _bounded_process((sys.executable, '-I', '-c', script), b'', 100)
                client = SelaLiveTTS(native_call=native, coordinator=coordinator)
                task = asyncio.create_task(client.synthesize('Tetap.'))
                try:
                    async with asyncio.timeout(2):
                        while not pidfile.exists():
                            await asyncio.sleep(.005)
                    pid = int(pidfile.read_text())
                    if production_start:
                        production = True
                    else:
                        task.cancel()
                    await asyncio.sleep(.08)
                    os.kill(pid, 0)
                    self.assertTrue(client._slot.locked())
                    contender = AdmissionCoordinator(self.lock_path, production_probe=lambda: False)
                    with self.assertRaises(LiveTTSBusy), contender.claim():
                        self.fail('lock released before child reap')
                    if not production_start:
                        task.cancel()
                        task.cancel()
                    expected = LiveTTSBusy if production_start else asyncio.CancelledError
                    with self.assertRaises(expected):
                        await asyncio.wait_for(asyncio.shield(task), 4)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                    self.assertFalse(client._slot.locked())
                    with contender.claim():
                        pass
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


class ProductionProbeTests(unittest.TestCase):
    def test_all_production_native_workers_detected_from_read_only_snapshot(self):
        from sela_live_tts.admission import production_busy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / '123'
            process.mkdir()
            for worker in ('supertonic3_worker.py', 'zipformer_worker.py',
                           'zipformer_stateful_worker.py', 'local_audio_worker.py'):
                with self.subTest(worker=worker):
                    (process / 'cmdline').write_bytes(
                        b'python\0' + f'/root/voice-lab/voice_app/{worker}'.encode() + b'\0')
                    self.assertTrue(production_busy(proc_root=root))
            (process / 'cmdline').write_bytes(b'python\0/root/sela-live/voice_app/supertonic3_worker.py\0')
            self.assertFalse(production_busy(proc_root=root))
            (process / 'cmdline').write_bytes(b'python\0-c\0print("supertonic3_worker.py")\0')
            self.assertFalse(production_busy(proc_root=root))

    def test_relative_production_worker_uses_process_cwd(self):
        from sela_live_tts.admission import production_busy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / '123'
            process.mkdir()
            (process / 'cwd').symlink_to('/root/voice-lab')
            (process / 'cmdline').write_bytes(b'python\0voice_app/zipformer_worker.py\0')
            self.assertTrue(production_busy(proc_root=root))

    def test_live_worker_snapshot_missing_cwd_fails_closed(self):
        from sela_live_tts.admission import production_busy, LiveTTSBusy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / '123'
            process.mkdir()
            (process / 'cmdline').write_bytes(b'python\0voice_app/zipformer_worker.py\0')
            with self.assertRaises(LiveTTSBusy):
                production_busy(proc_root=root)

    def test_probe_errors_fail_closed(self):
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        with tempfile.TemporaryDirectory() as directory:
            def broken():
                raise PermissionError('fixture')
            coordinator = AdmissionCoordinator(Path(directory) / 'lock', production_probe=broken)
            with self.assertRaises(LiveTTSBusy), coordinator.claim():
                self.fail('must not admit')


class HostFlockTests(unittest.TestCase):
    def test_subprocess_and_instances_share_persistent_inode(self):
        import subprocess
        import sys
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evaluation.lock'
            coordinator = AdmissionCoordinator(path, production_probe=lambda: False)
            code = ("import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); "
                    "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)")
            with coordinator.claim():
                inode = path.stat().st_ino
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                result = subprocess.run([sys.executable, '-I', '-c', code, str(path)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'BlockingIOError', result.stderr)
                with self.assertRaises(LiveTTSBusy), AdmissionCoordinator(path, production_probe=lambda: False).claim():
                    self.fail('second instance admitted')
            result = subprocess.run([sys.executable, '-I', '-c', code, str(path)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with coordinator.claim():
                self.assertEqual(path.stat().st_ino, inode)

    def test_external_flock_owner_blocks_coordinator_until_process_reaped(self):
        import subprocess
        import sys
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evaluation.lock'
            code = ("import fcntl,os,sys; "
                    "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600); "
                    "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); "
                    "print('locked',flush=True); sys.stdin.read(1)")
            proc = subprocess.Popen([sys.executable, '-I', '-c', code, str(path)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            coordinator = AdmissionCoordinator(path, production_probe=lambda: False)
            try:
                self.assertIsNotNone(proc.stdout)
                self.assertEqual(proc.stdout.readline(), b'locked\n')
                with self.assertRaises(LiveTTSBusy), coordinator.claim():
                    self.fail('external owner ignored')
            finally:
                proc.communicate(b'x', timeout=3)
            self.assertEqual(proc.returncode, 0)
            with coordinator.claim():
                pass

    def test_symlink_fifo_wrong_mode_and_hardlink_fail_closed(self):
        import os
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'
            target.touch(mode=0o600)
            symlink = root / 'symlink'
            symlink.symlink_to(target)
            fifo = root / 'fifo'
            os.mkfifo(fifo, 0o600)
            wrong_mode = root / 'wrong-mode'
            wrong_mode.touch(mode=0o644)
            hardlink = root / 'hardlink'
            os.link(target, hardlink)
            for path in (symlink, fifo, wrong_mode, hardlink):
                with self.subTest(path=path), self.assertRaises(LiveTTSBusy):
                    with AdmissionCoordinator(path, production_probe=lambda: False).claim():
                        self.fail('unsafe lock admitted')
            self.assertTrue(symlink.is_symlink())
            self.assertEqual(target.read_bytes(), b'')
