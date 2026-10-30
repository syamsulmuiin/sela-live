"""Model-free probe lifecycle regression tests; fixtures never touch production."""
import ast
import asyncio
import unittest
import subprocess
import sys


class ImportTests(unittest.TestCase):
    def test_import_has_no_application_or_filesystem_side_effects(self):
        code = """
import runpy, pathlib, unittest.mock as m, sys
before = list(sys.path)
with m.patch.object(pathlib.Path, 'mkdir', side_effect=RuntimeError('import mkdir')), m.patch.object(pathlib.Path, 'read_text', side_effect=RuntimeError('import read')):
    runpy.run_path('tools/tts_same_text_probe.py', run_name='probe_fixture')
if sys.path != before or 'sela_live_tts.backend' in sys.modules or 'voice_app.speech' in sys.modules:
    raise RuntimeError('application import side effect')
"""
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / 'tools/tts_same_text_probe.py'


def load_monitor():
    tree = ast.parse(PROBE.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in ('monitored', 'finish_cleanup', 'ProbeError', 'CleanupFailure', 'require')]
    namespace = {'asyncio': asyncio, 'busy': lambda: False}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PROBE), 'exec'), namespace)
    return namespace['monitored']


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_outer_cancel_waits_for_child_finally(self):
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def child():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                cleaned.set()
        task = asyncio.create_task(load_monitor()(child()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set(), 'L2: monitor returned with child still alive')


    async def test_production_starts_cancels_and_reaps_child(self):
        active, cleaned = asyncio.Event(), asyncio.Event()
        async def child():
            active.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                cleaned.set()
        with self.assertRaisesRegex(RuntimeError, 'production_busy'):
            await load_monitor()(child(), busy_probe=active.is_set, interval=.001)
        self.assertTrue(cleaned.is_set())

    async def test_busy_before_start_never_enters_child(self):
        calls = []
        async def child():
            calls.append('entered')
        with self.assertRaisesRegex(RuntimeError, 'production_busy'):
            await load_monitor()(child(), busy_probe=lambda: True)
        self.assertEqual(calls, [])

    async def test_repeated_cancel_waits_for_cleanup(self):
        started, cleaning, release, cleaned = [asyncio.Event() for _ in range(4)]
        async def child():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()
        task = asyncio.create_task(load_monitor()(child()))
        await started.wait()
        task.cancel()
        await cleaning.wait()
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())

    async def test_cleanup_failure_is_not_swallowed(self):
        started = asyncio.Event()
        async def child():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise OSError('fixture cleanup failure')
        task = asyncio.create_task(load_monitor()(child()))
        await started.wait()
        task.cancel()
        with self.assertRaisesRegex(RuntimeError, 'cleanup_failure'):
            await task


class GateTests(unittest.TestCase):
    def test_baseline_preflight_rejects_profile_parts_hash_before_native(self):
        import runpy, copy, json
        module = runpy.run_path(str(PROBE))
        original = json.loads(Path('/root/voice-lab/autonomous/one-minute-c2a5a7l8/receipt.json').read_text())
        validate = module.get('validate_baseline')
        self.assertIsNotNone(validate, 'missing explicit preflight gates')
        partition = lambda text: [p['source'] for p in original['parts']]
        validate(original, partition)
        for field in ('profile', 'parts', 'text', 'last', 'sha'):
            bad = copy.deepcopy(original)
            if field == 'profile': bad['choice']['profile_id'] = 'other'
            if field == 'parts': bad['parts'].pop()
            if field == 'text': bad['text'] += ' '
            if field == 'last': bad['parts'][0]['last'] = True
            if field == 'sha': bad['parts'][0]['sha256'] = 'bad'
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(bad, partition)


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_partial_receipt_is_terminal_and_retry_unique(self):
        import runpy, tempfile, json, io, wave, hashlib
        module = runpy.run_path(str(PROBE))
        original = json.loads(module['SOURCE'].read_text())
        wav = io.BytesIO()
        with wave.open(wav, 'wb') as w:
            w.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            w.writeframes(b'\x02\x00' * 100)
        raw = wav.getvalue()
        original['parts'][4]['sha256'] = hashlib.sha256(raw).hexdigest()
        calls = []
        async def native(steps, text, profile):
            calls.append(steps)
            if len(calls) == 3:
                raise RuntimeError('SECRET_UNSAFE_ERROR')
            return raw
        async def converter(wav, mp3):
            raise AssertionError('converter should not run')
        with tempfile.TemporaryDirectory(dir='/root/.hermes/cache/scratch') as tmp:
            root = Path(tmp)
            parity = root / 'parity.wav'
            parity.write_bytes(raw)
            original['parts'][4]['wav'] = str(parity)
            baseline = root / 'baseline.json'
            baseline.write_text(json.dumps(original))
            kwargs = dict(baseline_path=baseline, expected_baseline_sha=hashlib.sha256(baseline.read_bytes()).hexdigest(), artifact_root=root/'attempts', native=native, converter=converter, busy_probe=lambda: False, partition=lambda text: [p['source'] for p in original['parts']], trim=lambda raw, **kw: raw, preflight=lambda: {'fixture': True}, snapshot=lambda: {'fixture': 'unchanged'})
            with self.assertRaises(RuntimeError):
                await module['main'](**kwargs)
            dirs = list((root/'attempts').iterdir())
            self.assertEqual(len(dirs), 1)
            receipt = json.loads((dirs[0]/'receipt.json').read_text())
            self.assertEqual(receipt['status'], 'failed')
            self.assertEqual(receipt['native_call_count'], 3)
            self.assertTrue(receipt['cleanup_complete'])
            self.assertEqual(len(receipt['partial_artifacts']), 2)
            self.assertNotIn('SECRET', json.dumps(receipt))
            calls.clear()
            with self.assertRaises(RuntimeError):
                await module['main'](**kwargs)
            self.assertEqual(len(list((root/'attempts').iterdir())), 2)


class ProbeFixture:
    def __init__(self, test):
        import tempfile, runpy, json, io, wave, hashlib
        self.json, self.hashlib = json, hashlib
        self.module = runpy.run_path(str(PROBE))
        self.tmp = tempfile.TemporaryDirectory(dir='/root/.hermes/cache/scratch')
        test.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = json.loads(self.module['SOURCE'].read_text())
        out = io.BytesIO()
        with wave.open(out, 'wb') as writer:
            writer.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            writer.writeframes(bytes([2, 0])*100)
        self.raw = out.getvalue()
        for index, part in enumerate(self.original['parts']):
            path = self.root/f'baseline-{index}.wav'
            path.write_bytes(self.raw)
            part.update(wav=str(path), sha256=hashlib.sha256(self.raw).hexdigest())
        self.baseline = self.root/'baseline.json'
        self.baseline.write_text(json.dumps(self.original))
        self.calls = []
        async def native(steps, source, profile):
            self.calls.append((steps, source, profile))
            return self.raw
        async def converter(wav, mp3):
            mp3.write_bytes(b'fixture converter result, not a real MP3')
        self.kwargs = dict(baseline_path=self.baseline, expected_baseline_sha=hashlib.sha256(self.baseline.read_bytes()).hexdigest(), artifact_root=self.root/'attempts', native=native, converter=converter, busy_probe=lambda: False, partition=lambda text: [p['source'] for p in self.original['parts']], trim=lambda raw, **kw: raw, preflight=lambda: {'fixture': True}, snapshot=lambda: {'fixture': 'unchanged'})

    def receipt(self):
        return self.json.loads(next((self.root/'attempts').glob('*/receipt.json')).read_text())


class MoreRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_drift_failure_retains_partial_artifact_inventory(self):
        fixture = ProbeFixture(self)
        snapshots = []
        def snapshot():
            snapshots.append('read')
            return {'fixture': 'before' if len(snapshots) == 1 else 'after'}
        fixture.kwargs['snapshot'] = snapshot
        with self.assertRaises(ValueError):
            await fixture.module['main'](**fixture.kwargs)
        receipt = fixture.receipt()
        self.assertEqual(receipt['status'], 'failed')
        self.assertFalse(receipt['production_source_unchanged'])
        self.assertEqual(len(receipt['partial_artifacts']), 10)
        self.assertTrue(receipt['cleanup_complete'])

    async def test_pin_preflight_rechecked_before_completion(self):
        fixture = ProbeFixture(self)
        calls = []
        def preflight():
            calls.append('checked')
            if len(calls) > 1:
                raise ValueError('pin_drift')
            return {'fixture': True}
        fixture.kwargs['preflight'] = preflight
        with self.assertRaises(ValueError):
            await fixture.module['main'](**fixture.kwargs)
        self.assertEqual(fixture.receipt()['status'], 'failed')

    async def test_baseline_artifact_hash_failure_prevents_all_native_calls(self):
        fixture = ProbeFixture(self)
        fixture.original['parts'][0]['sha256'] = '0'*64
        fixture.baseline.write_text(fixture.json.dumps(fixture.original))
        fixture.kwargs['expected_baseline_sha'] = fixture.hashlib.sha256(fixture.baseline.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            await fixture.module['main'](**fixture.kwargs)
        self.assertEqual(fixture.calls, [])
        self.assertEqual(fixture.receipt()['native_call_count'], 0)

    async def test_converter_cannot_change_verified_parts_or_final_wav(self):
        for target in ('part', 'final'):
            fixture = ProbeFixture(self)
            async def converter(wav, mp3):
                mp3.write_bytes(b'fixture')
                path = next(wav.parent.glob('quality12-chunk-*.wav')) if target == 'part' else wav
                path.write_bytes(fixture.raw)
                if target == 'part':
                    path.write_bytes(b'corrupted')
            fixture.kwargs['converter'] = converter
            with self.subTest(target=target), self.assertRaises(ValueError):
                await fixture.module['main'](**fixture.kwargs)
            self.assertEqual(fixture.receipt()['status'], 'failed')

    async def test_child_cleanup_failure_cannot_claim_cleanup_complete(self):
        fixture = ProbeFixture(self)
        started = asyncio.Event()
        async def native(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise OSError('SECRET cleanup detail')
        fixture.kwargs['native'] = native
        owner = asyncio.create_task(fixture.module['main'](**fixture.kwargs))
        await started.wait()
        owner.cancel()
        with self.assertRaises(RuntimeError):
            await owner
        receipt = fixture.receipt()
        self.assertEqual(receipt['status'], 'failed')
        self.assertFalse(receipt['cleanup_complete'])
        self.assertEqual(receipt['reason'], 'cleanup_failure')


class AcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_exact_eight_calls_sources_hashes_receipt(self):
        fixture = ProbeFixture(self)
        result = await fixture.module['main'](**fixture.kwargs)
        receipt = fixture.receipt()
        self.assertEqual(receipt, result)
        self.assertEqual(receipt['status'], 'completed')
        self.assertTrue(receipt['cleanup_complete'])
        self.assertFalse(receipt['listening_accepted'])
        self.assertEqual(receipt['native_call_count'], 8)
        self.assertEqual([c[0] for c in fixture.calls], [8]+[12]*7)
        self.assertEqual([c[1] for c in fixture.calls], [fixture.original['parts'][4]['source'].strip()]+[p['source'].strip() for p in fixture.original['parts']])
        self.assertEqual([c[2] for c in fixture.calls], ['conditioning_b']*8)
        self.assertEqual(''.join(p['source'] for p in receipt['parts']), fixture.original['text'])
        self.assertEqual([p['last'] for p in receipt['parts']], [False]*6+[True])
        for key in ('wav', 'mp3'):
            self.assertEqual(fixture.hashlib.sha256(Path(receipt[key]).read_bytes()).hexdigest(), receipt[key+'_sha256'])
        self.assertEqual(len(receipt['partial_artifacts']), 10)

    async def test_invalid_baseline_and_pins_prevent_all_native_calls(self):
        for kind in ('hash', 'profile', 'parts', 'layout', 'missing', 'pins'):
            fixture = ProbeFixture(self)
            if kind == 'hash': fixture.kwargs['expected_baseline_sha'] = '0'*64
            elif kind == 'profile': fixture.original['choice']['profile_id'] = 'wrong'
            elif kind == 'parts': fixture.original['parts'].append(fixture.original['parts'][0])
            elif kind == 'layout': fixture.kwargs['partition'] = lambda text: [text]
            elif kind == 'pins':
                def fail(): raise ValueError('fixture_pin_failure')
                fixture.kwargs['preflight'] = fail
            if kind in ('profile', 'parts'):
                fixture.baseline.write_text(fixture.json.dumps(fixture.original))
                fixture.kwargs['expected_baseline_sha'] = fixture.hashlib.sha256(fixture.baseline.read_bytes()).hexdigest()
            if kind == 'missing': fixture.baseline.unlink()
            with self.subTest(kind=kind), self.assertRaises((ValueError, FileNotFoundError)):
                await fixture.module['main'](**fixture.kwargs)
            self.assertEqual(fixture.calls, [])
            self.assertEqual(fixture.receipt()['native_call_count'], 0)
            self.assertEqual(fixture.receipt()['status'], 'failed')

    async def test_busy_before_start_receipt_zero_calls(self):
        fixture = ProbeFixture(self)
        fixture.kwargs['busy_probe'] = lambda: True
        with self.assertRaisesRegex(ValueError, 'production_busy'):
            await fixture.module['main'](**fixture.kwargs)
        self.assertEqual(fixture.receipt()['native_call_count'], 0)
        self.assertEqual(fixture.receipt()['status'], 'failed')

    async def test_cancelled_receipt_is_written_only_after_repeated_cancel_cleanup(self):
        fixture = ProbeFixture(self)
        started, cleaning, release, cleaned = [asyncio.Event() for _ in range(4)]
        async def native(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()
        fixture.kwargs['native'] = native
        owner = asyncio.create_task(fixture.module['main'](**fixture.kwargs))
        await started.wait()
        owner.cancel()
        await cleaning.wait()
        for _ in range(3):
            owner.cancel()
            await asyncio.sleep(0)
        self.assertFalse(owner.done())
        self.assertEqual(fixture.receipt()['status'], 'started')
        release.set()
        with self.assertRaises(asyncio.CancelledError): await owner
        self.assertTrue(cleaned.is_set())
        self.assertEqual(fixture.receipt()['status'], 'cancelled')
        self.assertTrue(fixture.receipt()['cleanup_complete'])
        self.assertEqual(fixture.receipt()['native_call_count'], 1)

    async def test_converter_deadline_and_overflow_have_terminal_receipts(self):
        for kind in ('deadline', 'overflow'):
            fixture = ProbeFixture(self)
            async def converter(wav, mp3):
                code = 'import time; time.sleep(20)' if kind == 'deadline' else 'import os,time; os.write(1,b"x"*100000); time.sleep(20)'
                await fixture.module['bounded_process']([sys.executable, '-I', '-c', code], timeout=.15 if kind == 'deadline' else 2, output_cap=1024, busy_probe=lambda: False)
            fixture.kwargs['converter'] = converter
            with self.subTest(kind=kind), self.assertRaises((RuntimeError, TimeoutError)):
                await fixture.module['main'](**fixture.kwargs)
            self.assertEqual(fixture.receipt()['status'], 'failed')
            self.assertEqual(fixture.receipt()['native_call_count'], 8)
            self.assertTrue(fixture.receipt()['cleanup_complete'])

    async def test_encoding_failure_receipt_and_no_more_calls(self):
        fixture = ProbeFixture(self)
        async def converter(wav, mp3):
            mp3.write_bytes(b'partial')
            raise RuntimeError('SECRET converter diagnostic')
        fixture.kwargs['converter'] = converter
        with self.assertRaises(RuntimeError): await fixture.module['main'](**fixture.kwargs)
        receipt = fixture.receipt()
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['native_call_count'], 8)
        self.assertEqual(len(receipt['partial_artifacts']), 10)
        self.assertNotIn('SECRET', fixture.json.dumps(receipt))

    async def test_parity_and_invalid_wav_fail_closed(self):
        for kind in ('parity', 'empty', 'truncated', 'wrong_rate'):
            fixture = ProbeFixture(self)
            raw = bytearray(fixture.raw)
            if kind == 'parity': raw[-1] = 1
            elif kind == 'empty': raw = bytearray()
            elif kind == 'truncated': raw.pop()
            elif kind == 'wrong_rate': raw[24:28] = (16000).to_bytes(4, 'little')
            async def native(*args): return bytes(raw)
            fixture.kwargs['native'] = native
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                await fixture.module['main'](**fixture.kwargs)
            self.assertEqual(fixture.receipt()['native_call_count'], 1)
            self.assertEqual(fixture.receipt()['status'], 'failed')

    async def test_optimized_interpreter_keeps_acceptance_gates(self):
        code = '''
import runpy, json, pathlib
m=runpy.run_path('tools/tts_same_text_probe.py')
d=json.loads(m['SOURCE'].read_text()); d['choice']['profile_id']='wrong'
checks=[lambda:m['require'](False,'gate'),lambda:m['wav_pcm'](b''),lambda:m['validate_baseline'](d,lambda text:[p['source'] for p in d['parts']])]
for check in checks:
    try: check()
    except ValueError: pass
    else: raise RuntimeError('optimized gate bypass')
'''
        process = await asyncio.create_subprocess_exec(sys.executable, '-O', '-B', '-c', code, cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        self.assertEqual(process.returncode, 0, stderr.decode())


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_dependencies_readonly_preflight(self):
        import runpy
        module = runpy.run_path(str(PROBE))
        dependencies = module.get('default_dependencies')
        self.assertIsNotNone(dependencies, 'missing production preflight wiring')
        deps = await dependencies()
        evidence = deps['preflight']()
        original = __import__('json').loads(module['SOURCE'].read_text())
        self.assertEqual(len(module['validate_baseline'](original, deps['partition'])), 7)
        self.assertEqual(evidence['actual_sdk_origin'], evidence['expected_sdk_origin'])
        self.assertEqual(evidence['profile_id'], 'conditioning_b')
        self.assertTrue(Path(evidence['ffmpeg']).is_file())
        self.assertIn('production_app_top_level_files', deps['snapshot']())

    async def test_real_ffmpeg_roundtrip_fixture_without_native_inference(self):
        import runpy, tempfile, wave
        module = runpy.run_path(str(PROBE))
        deps = await module['default_dependencies']()
        with tempfile.TemporaryDirectory(dir='/root/.hermes/cache/scratch') as tmp:
            wav, mp3 = Path(tmp)/'fixture.wav', Path(tmp)/'fixture.mp3'
            with wave.open(str(wav), 'wb') as writer:
                writer.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
                writer.writeframes(bytes([2, 0])*4410)
            await deps['converter'](wav, mp3)
            self.assertGreater(mp3.stat().st_size, 0)
            self.assertLess(mp3.stat().st_size, 4_000_000)

    async def test_isolated_sdk_origin_inspection_does_not_import_sdk(self):
        import runpy, tempfile
        module = runpy.run_path(str(PROBE))
        inspect_origin = module.get('inspect_sdk_origin')
        self.assertIsNotNone(inspect_origin, 'missing actual isolated SDK origin inspection')
        with tempfile.TemporaryDirectory(dir='/root/.hermes/cache/scratch') as tmp:
            root = Path(tmp)/'supertonic'
            root.mkdir()
            (root/'__init__.py').write_text('raise RuntimeError("SDK imported")')
            evidence = await inspect_origin(Path(sys.executable), root, lambda: False)
            self.assertEqual(evidence['actual_sdk_origin'], str(root/'__init__.py'))
            self.assertEqual(evidence['expected_sdk_origin'], evidence['actual_sdk_origin'])
            self.assertFalse(evidence['sdk_imported'])


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_child_reaped_after_repeated_cancel_and_production_start(self):
        import runpy, tempfile, os
        bounded = runpy.run_path(str(PROBE))['bounded_process']
        with tempfile.TemporaryDirectory(dir='/root/.hermes/cache/scratch') as tmp:
            for kind in ('cancel', 'production'):
                marker = Path(tmp)/kind
                code = f'import os,time,signal,sys; signal.signal(signal.SIGTERM,lambda *a:(time.sleep(.12),sys.exit(0))); open({str(marker)!r},"w").write(str(os.getpid())); time.sleep(20)'
                task = asyncio.create_task(bounded([sys.executable, '-I', '-c', code], busy_probe=marker.exists if kind == 'production' else lambda: False))
                async with asyncio.timeout(3):
                    while not marker.exists(): await asyncio.sleep(.005)
                pid = int(marker.read_text())
                if kind == 'cancel':
                    task.cancel()
                    for _ in range(3):
                        await asyncio.sleep(.01)
                        task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                else:
                    with self.assertRaisesRegex(RuntimeError, 'production_busy'): await task
                with self.assertRaises(ProcessLookupError): os.kill(pid, 0)

    async def test_overflow_closes_pipe_transports_not_only_pid(self):
        import runpy
        from unittest.mock import patch
        module = runpy.run_path(str(PROBE))
        children = []
        spawn = asyncio.create_subprocess_exec
        async def capture(*args, **kwargs):
            child = await spawn(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(asyncio, 'create_subprocess_exec', capture):
            with self.assertRaises(RuntimeError):
                await module['bounded_process']([sys.executable, '-I', '-c', 'import os,time; os.write(1,b"x"*100000); time.sleep(20)'], output_cap=1024, busy_probe=lambda: False)
        self.assertEqual(len(children), 1)
        self.assertTrue(children[0].stdout.at_eof(), 'stdout transport not drained/closed')
        self.assertTrue(children[0].stderr.at_eof(), 'stderr transport not drained/closed')

    async def test_success_bounded_stdout_for_isolated_origin_inspection(self):
        import runpy
        bounded = runpy.run_path(str(PROBE))['bounded_process']
        output = await bounded([sys.executable, '-I', '-c', 'print("fixture-origin")'], busy_probe=lambda: False)
        self.assertEqual(output, b'fixture-origin\n')

    async def test_bounded_process_deadline_overflow_failure_and_reaping(self):
        import runpy, tempfile, os
        module = runpy.run_path(str(PROBE))
        bounded = module.get('bounded_process')
        self.assertIsNotNone(bounded, 'missing bounded async converter lifecycle')
        with tempfile.TemporaryDirectory(dir='/root/.hermes/cache/scratch') as tmp:
            for kind, body, timeout in [
                ('deadline', 'import time; time.sleep(30)', .15),
                ('overflow', 'import os; os.write(1, b"x"*100000); import time; time.sleep(30)', 2),
                ('failure', 'raise SystemExit(7)', 2),
            ]:
                pidfile = Path(tmp)/kind
                code = f'import os; open({str(pidfile)!r}, "w").write(str(os.getpid())); ' + body
                with self.subTest(kind=kind), self.assertRaises((RuntimeError, TimeoutError)):
                    await bounded([sys.executable, '-I', '-c', code], timeout=timeout, output_cap=1024, busy_probe=lambda: False)
                pid = int(pidfile.read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)


if __name__ == '__main__':
    unittest.main()
