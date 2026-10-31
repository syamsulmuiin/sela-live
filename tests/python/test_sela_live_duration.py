"""Harmless real children only: never import a native model runtime."""
import asyncio
import hashlib
import sys
import os
from unittest.mock import patch
from sela_live_tts import duration

import tempfile
import unittest
from pathlib import Path
SOURCE_MANIFEST = Path(__file__).resolve().parents[2] / 'sela_live_tts/upstream-manifest.json'

from sela_live_tts.duration import DurationSession


class Admission:
    poll_interval = .005
    def __init__(self):
        self.held = False
        self.children = []
    def check_production(self):
        pass
    def claim(self):
        owner = self
        class Lease:
            def __enter__(self):
                if owner.held:
                    raise RuntimeError('busy')
                owner.held = True
            def __exit__(self, *args):
                for child in owner.children:
                    if child.returncode is None:
                        raise AssertionError('released before reap')
                owner.held = False
        return Lease()


class DurationTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, directory, body, **options):
        worker = Path(directory) / 'child.py'
        worker.write_text("import sys,json,time,os\n" + body)
        worker.with_name('upstream-manifest.json').write_bytes(SOURCE_MANIFEST.read_bytes())
        admission = Admission()
        async def spawn(*args, **kwargs):
            child = await asyncio.create_subprocess_exec(*args, **kwargs)
            admission.children.append(child)
            return child
        return DurationSession(python=sys.executable, assets=directory, model_manifest=worker,
            worker=worker, worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest(),
            source_manifest_sha256=hashlib.sha256(SOURCE_MANIFEST.read_bytes()).hexdigest(),
            process_factory=spawn, coordinator=admission, **options)

    async def test_last_result_eof_does_not_race_its_delivery(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, ready + "sys.stdin.readline();print('{\"id\":64,\"seconds\":2,\"tokens\":9}',flush=True)")
            async with session:
                session._count = 63
                original = session.process.stdin.drain
                async def delayed_drain():
                    await original()
                    await asyncio.sleep(.05)
                session.process.stdin.drain = delayed_drain
                self.assertEqual(await session.predict('Halo.'), {'seconds':2.0,'tokens':9})

    async def test_real_serve_predictor_refusal_then_valid_same_child(self):
        worker_path = SOURCE_MANIFEST.with_name('duration_worker.py')
        body = f"import runpy\nw=runpy.run_path({str(worker_path)!r})\n" + '''
class Tensor:
 def __init__(self, count=1, value=10.5):
  self.shape, self.size, self.value = (1,count), 1, value
 def __truediv__(self, speed):
  return Tensor(value=self.value/speed)
 def reshape(self, *args):
  return [self.value]
class DP:
 def run(self, names, inputs):
  if inputs['text_ids'].shape[-1] > 512:
   raise RuntimeError('over-limit DP must never run')
  return [Tensor()]
def processor(texts, lang):
 return Tensor(len(texts[0])+9), Tensor()
w['serve'](w['Predictor'](processor, DP(), Tensor()), sys.stdin.buffer, sys.stdout, time.monotonic()+10)
'''
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, body)
            async with session:
                child = session.process
                for text, tokens in ((('Halo. '*100).strip(), 608), ('a'*504, 513)):
                    self.assertEqual(await session.predict(text), {
                        'seconds':None, 'tokens':tokens, 'refusal':'model_token_limit'})
                    self.assertIs(session.process, child)
                    self.assertIsNone(child.returncode)
                    self.assertTrue(session.coordinator.held)
                self.assertEqual(await session.predict('Halo.'), {'seconds':10.0, 'tokens':14})
                self.assertEqual(session._count, 3)
            self.assertIsNotNone(child.returncode)
            self.assertFalse(session.coordinator.held)
            self.assertTrue(all(task.done() for task in session.owned_tasks))

    async def test_malformed_refusals_fail_closed_and_reap(self):
        import json
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        valid = {'id':1, 'seconds':None, 'tokens':608, 'refusal':'model_token_limit'}
        cases = {
            'fake_duration': {**valid, 'seconds':1.0},
            'bool_duration': {**valid, 'seconds':True},
            'low_tokens': {**valid, 'tokens':512},
            'zero_tokens': {**valid, 'tokens':0},
            'high_tokens': {**valid, 'tokens':32769},
            'bool_tokens': {**valid, 'tokens':True},
            'float_tokens': {**valid, 'tokens':608.0},
            'string_tokens': {**valid, 'tokens':'608'},
            'wrong_reason': {**valid, 'refusal':'text_limit'},
            'null_reason': {**valid, 'refusal':None},
            'bool_reason': {**valid, 'refusal':True},
            'extra_field': {**valid, 'extra':1},
            'wrong_id': {**valid, 'id':2},
            'bool_id': {**valid, 'id':True},
            'float_id': {**valid, 'id':1.0},
            'null_without_refusal': {'id':1, 'seconds':None, 'tokens':608},
            'normal_with_refusal': {**valid, 'seconds':1.0, 'tokens':9},
            'numeric_overlimit': {'id':1, 'seconds':1.0, 'tokens':513},
        }
        for key in valid:
            cases['missing_' + key] = {k:v for k,v in valid.items() if k != key}
        for name, value in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                body = ready + f"sys.stdin.readline();print({json.dumps(value)!r},flush=True);time.sleep(30)"
                session = self.fixture(directory, body)
                await session.__aenter__()
                with self.assertRaises(ValueError):
                    await session.predict('Halo.')
                self.assertFalse(session._active)
                self.assertFalse(session.coordinator.held)
                self.assertIsNotNone(session.process.returncode)
                self.assertTrue(all(task.done() for task in session.owned_tasks))
                with self.assertRaises(RuntimeError):
                    await session.predict('Lagi.')
                await session.__aexit__(None, None, None)

    async def test_refusal_upper_token_boundary_is_nonfatal(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        body = ready + "for line in sys.stdin:\n q=json.loads(line);print(json.dumps({'id':q['id'],'seconds':None,'tokens':32768,'refusal':'model_token_limit'}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, body)
            async with session:
                for _ in range(2):
                    self.assertEqual(await session.predict('a'), {
                        'seconds':None, 'tokens':32768, 'refusal':'model_token_limit'})
                    self.assertTrue(session._active)
            self.assertFalse(session.coordinator.held)
            self.assertIsNotNone(session.process.returncode)

    async def test_utf8_bom_stdout_is_rejected(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory:
            body = ready + "sys.stdin.readline();os.write(1,b'\\xef\\xbb\\xbf'+b'{\"id\":1,\"seconds\":2,\"tokens\":9}\\n');time.sleep(30)"
            session = self.fixture(directory, body)
            with self.assertRaises(ValueError):
                async with session:
                    await session.predict('Halo.')

    async def test_result_token_limit_is_model_input_limit(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, ready + 'sys.stdin.readline();print(\'{"id":1,"seconds":1,"tokens":513}\',flush=True);time.sleep(30)')
            with self.assertRaises(ValueError):
                async with session:
                    await session.predict('Halo.')

    async def test_safe_stderr_marker_retained_without_native_text(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, ready + "sys.stdin.readline();os.write(2,b'private transcript\\nsupertonic3_failure=memory_headroom\\n');time.sleep(.03)")
            with self.assertRaises(ValueError):
                async with session:
                    await session.predict('Halo.')
            self.assertEqual(session.failure_reason, 'memory_headroom')
            self.assertIsInstance(session.returncode, int)
            self.assertNotIn('private transcript', repr(session.__dict__))

    async def test_all_64_results_survive_clean_eof(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, ready + "for i in range(64):\n q=json.loads(sys.stdin.readline());print(json.dumps({'id':q['id'],'seconds':2,'tokens':9}),flush=True)\n")
            async with session:
                for _ in range(64):
                    self.assertEqual((await session.predict('Halo.'))['tokens'], 9)
                with self.assertRaises(ValueError):
                    await session.predict('Halo.')

    async def test_cleanup_failure_never_releases_lease(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\ntime.sleep(30)"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, ready)
            await session.__aenter__()
            original = session.process.wait
            async def failed_wait():
                raise RuntimeError('fixture wait failed')
            session.process.wait = failed_wait
            with self.assertRaisesRegex(RuntimeError, 'fixture wait failed'):
                await session.__aexit__(None,None,None)
            self.assertTrue(session.coordinator.held)
            session.process.wait = original
            session._cleanup_task = None
            await session.__aexit__(None,None,None)
            self.assertFalse(session.coordinator.held)

    async def test_cleanup_closes_overflowed_pipe_transports(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, ready + "sys.stdin.readline();os.write(1,b'x'*200000);time.sleep(30)")
            with self.assertRaises(ValueError):
                async with session:
                    await session.predict('Halo.')
            self.assertTrue(session.process.stdout._transport.is_closing())
            self.assertTrue(session.process.stderr._transport.is_closing())

    async def test_harmless_hostile_children_are_reaped(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        cases = {
            'timeout': ready + 'sys.stdin.readline();time.sleep(30)',
            'eof': ready + 'sys.stdin.readline()',
            'malformed': ready + "sys.stdin.readline();print('not-json',flush=True);time.sleep(30)",
            'duplicate': ready + 'sys.stdin.readline();print(\'{"id":1,"id":1,"seconds":1,"tokens":1}\',flush=True);time.sleep(30)',
            'nonfinite': ready + 'sys.stdin.readline();print(\'{"id":1,"seconds":NaN,"tokens":1}\',flush=True);time.sleep(30)',
            'wrong_id': ready + 'sys.stdin.readline();print(\'{"id":2,"seconds":1,"tokens":1}\',flush=True);time.sleep(30)',
            'stdout_line': ready + "sys.stdin.readline();os.write(1,b'x'*5000);time.sleep(30)",
            'stderr_overflow': ready + "sys.stdin.readline();os.write(2,b'x'*9000);time.sleep(30)",
            'stdout_total': ready + "sys.stdin.readline();os.write(1,b'{}\\n'*12000);time.sleep(30)",
        }
        for name, body in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                session = self.fixture(directory, body, query_timeout=.2)
                with self.assertRaises((ValueError, TimeoutError)):
                    async with session:
                        await session.predict('Halo.')
                self.assertFalse(session.coordinator.held)
                self.assertTrue(all(child.returncode is not None for child in session.coordinator.children))
                self.assertTrue(all(task.done() for task in session.owned_tasks))

    async def test_repeated_cancel_during_spawn_keeps_lock_until_reap(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, 'time.sleep(30)')
            original = session.factory
            created, release = asyncio.Event(), asyncio.Event()
            async def delayed_spawn(*args, **kwargs):
                child = await original(*args, **kwargs)
                created.set()
                await release.wait()
                return child
            session.factory = delayed_spawn
            task = asyncio.create_task(session.__aenter__())
            await asyncio.wait_for(created.wait(), 2)
            for _ in range(8):
                task.cancel()
                await asyncio.sleep(.005)
                self.assertTrue(session.coordinator.held)
                self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(session.coordinator.held)
            self.assertTrue(all(child.returncode is not None for child in session.coordinator.children))
            self.assertTrue(all(task.done() for task in session.owned_tasks))

    async def test_query_cancellation_repeated_cleanup(self):
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, ready + 'sys.stdin.readline();time.sleep(30)')
            await session.__aenter__()
            task = asyncio.create_task(session.predict('Halo.'))
            await asyncio.sleep(.02)
            for _ in range(10):
                task.cancel()
                await asyncio.sleep(.001)
            with self.assertRaises(asyncio.CancelledError):
                await task
            await session.__aexit__(None,None,None)
            self.assertFalse(session.coordinator.held)
            self.assertTrue(all(child.returncode is not None for child in session.coordinator.children))
            self.assertTrue(all(task.done() for task in session.owned_tasks))

    async def test_worker_drift_and_unsafe_paths_rejected_before_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, 'time.sleep(30)')
            link = Path(directory) / 'python'
            link.symlink_to(sys.executable)
            self.assertEqual(duration._path(link, interpreter=True), link)
            with self.assertRaises(ValueError):
                duration._path(link)
            session.worker.write_text('raise SystemExit(0)')
            with self.assertRaisesRegex(ValueError, 'worker drift'):
                await session.__aenter__()
            self.assertEqual(session.coordinator.children, [])
            self.assertFalse(session.coordinator.held)

    async def test_environment_observed_inside_real_child(self):
        body = "assert not any(k in os.environ for k in ('API_KEY','HTTPS_PROXY','PYTHONPATH','LD_PRELOAD'))\nassert os.environ['HOME']=='/nonexistent'\nprint(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\ntime.sleep(30)"
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, body)
            with patch.dict(os.environ, {'API_KEY':'secret','HTTPS_PROXY':'secret','PYTHONPATH':'evil','LD_PRELOAD':'evil'}):
                async with session:
                    self.assertTrue(session.coordinator.held)

    async def test_production_priority_and_idle_deadline_reap(self):
        from sela_live_tts.admission import LiveTTSBusy
        ready = "print(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\ntime.sleep(30)"
        for mode in ('production', 'deadline'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                session = self.fixture(directory, ready, session_timeout=.1 if mode == 'deadline' else 70)
                async with session:
                    if mode == 'production':
                        def busy():
                            raise LiveTTSBusy('Production speech has priority')
                        session.coordinator.check_production = busy
                    await asyncio.sleep(.15)
                    with self.assertRaises((LiveTTSBusy, TimeoutError)):
                        await session.predict('Halo.')
                self.assertFalse(session.coordinator.held)
                self.assertTrue(all(task.done() for task in session.owned_tasks))

    async def test_source_manifest_checked_before_every_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.fixture(directory, 'time.sleep(30)')
            session.worker.with_name('upstream-manifest.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'manifest drift'):
                await session.__aenter__()
            self.assertEqual(session.coordinator.children, [])
            self.assertFalse(session.coordinator.held)

    async def test_offline_environment_is_fixed_allowlist(self):
        with patch.dict(os.environ, {'API_KEY':'secret', 'HTTPS_PROXY':'secret',
                                    'PATH':'evil', 'HOME':'evil', 'LD_PRELOAD':'evil'}):
            env = duration._offline_env()
        self.assertNotIn('API_KEY', env)
        self.assertNotIn('HTTPS_PROXY', env)
        self.assertNotIn('LD_PRELOAD', env)
        self.assertEqual(env['PATH'], '/usr/bin:/bin')
        self.assertEqual(env['HOME'], '/nonexistent')

    async def test_serial_session_and_profile_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / 'child.py'
            worker.write_text("import sys,json\nprint(json.dumps({'type':'ready','protocol':1,'max_queries':64,'native_network_denied':True}),flush=True)\nfor line in sys.stdin:\n q=json.loads(line);print(json.dumps({'id':q['id'],'seconds':2.5,'tokens':7}),flush=True)\n")
            worker.with_name('upstream-manifest.json').write_bytes(SOURCE_MANIFEST.read_bytes())
            admission = Admission()
            async def spawn(*args, **kwargs):
                child = await asyncio.create_subprocess_exec(sys.executable, '-I', str(worker), **kwargs)
                admission.children.append(child)
                return child
            session = DurationSession(python=sys.executable, assets=directory, model_manifest=worker,
                worker=worker, worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest(),
                source_manifest_sha256=hashlib.sha256(SOURCE_MANIFEST.read_bytes()).hexdigest(), process_factory=spawn, coordinator=admission)
            async with session:
                self.assertTrue(admission.held)
                self.assertEqual(await session.predict('Halo.'), {'seconds':2.5, 'tokens':7})
                with self.assertRaises(ValueError):
                    await session.predict('Halo.', profile_id='conditioning_b_calm_low')
                self.assertEqual(await session.predict('Lagi.'), {'seconds':2.5, 'tokens':7})
            self.assertFalse(admission.held)
            self.assertTrue(all(task.done() for task in session.owned_tasks))


class WorkerValidationTests(unittest.TestCase):
    def test_preprocessed_overlimit_refuses_without_dp_then_bounded_dp_runs(self):
        import numpy as np
        from sela_live_tts import duration_worker as worker
        from unittest.mock import Mock
        width = 608
        ids = None
        def processor(texts, lang):
            nonlocal ids
            self.assertEqual((texts, lang), (['a'], 'id'))
            ids = np.zeros((1, width), dtype=np.int64)
            return ids, np.ones((1, 1, width), dtype=np.float32)
        dp = Mock()
        dp.run.return_value = [np.asarray([10.5], dtype=np.float32)]
        predict = worker.Predictor(processor, dp, object())
        for width in (513, 608, 32768):
            with self.subTest(width=width):
                self.assertEqual(predict('a'), {
                    'seconds':None, 'tokens':width, 'refusal':'model_token_limit'})
        dp.run.assert_not_called()
        width = 512
        self.assertEqual(predict('a'), {'seconds':10.0, 'tokens':512})
        dp.run.assert_called_once()
        self.assertIs(dp.run.call_args.args[1]['text_ids'], ids)

    def test_relocated_manifest_digest_checked_before_guard_import(self):
        import json
        from sela_live_tts import duration_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('source_pins.py').write_bytes(SOURCE_MANIFEST.with_name('source_pins.py').read_bytes())
            data = json.loads(SOURCE_MANIFEST.read_bytes())
            data['sdk_root'] = str(SOURCE_MANIFEST.parents[1] / 'vendor/supertonic')
            manifest = root / 'upstream-manifest.json'
            manifest.write_text(json.dumps(data, sort_keys=True))
            expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
            original = worker.load_module
            imported = []
            def record(name, path):
                imported.append(name)
                return original(name, path)
            with patch.object(worker, 'HERE', root), patch.object(worker, 'load_module', side_effect=record):
                verified, guard = worker.verify_native(expected)
                self.assertEqual(verified['sdk_root'], data['sdk_root'])
                self.assertTrue(callable(guard.isolate))
                self.assertEqual(imported, ['_duration_source_pins','_duration_native_guard'])
                imported.clear()
                with self.assertRaises(ValueError):
                    worker.verify_native('0'*64)
                self.assertEqual(imported, ['_duration_source_pins'])
                imported.clear()
                root.joinpath('source_pins.py').write_text('raise RuntimeError("must not execute")')
                with self.assertRaises(ValueError):
                    worker.verify_native(expected)
                self.assertEqual(imported, [])

    def test_worker_query_budget_and_input_caps(self):
        import io,json,time
        from sela_live_tts import duration_worker as worker
        calls = []
        def predict(text):
            calls.append(text)
            return {'seconds':3.,'tokens':10}
        raw = b''.join(json.dumps({'id':i,'text':'Halo.'}).encode()+b'\n' for i in range(1,66))
        output = io.StringIO()
        with patch.object(worker, 'arm_deadline') as timer:
            worker.serve(predict, io.BytesIO(raw), output, time.monotonic()+70)
        self.assertEqual(len(calls), 64)
        self.assertEqual(len(output.getvalue().splitlines()), 65)
        self.assertTrue(all(0 < call.args[0] <= 70 for call in timer.call_args_list))
        self.assertTrue(all(call.args[0] <= 10 for call in timer.call_args_list[1::2]))
        for raw in (b'x'*4097, b'{"id":1,"text":"<bad>"}\n', b'{"id":1,"text":""}\n'):
            with self.subTest(raw=raw[:20]), patch.object(worker,'arm_deadline'), self.assertRaises(ValueError):
                worker.serve(predict, io.BytesIO(raw), io.StringIO(), time.monotonic()+70)

    def test_native_resource_limits_are_preserved(self):
        from types import SimpleNamespace
        from sela_live_tts import duration_worker as worker
        seen = []
        resource = SimpleNamespace(RLIMIT_CPU=1,RLIMIT_AS=2,RLIMIT_FSIZE=3,RLIMIT_NOFILE=4,
                                   setrlimit=lambda *args: seen.append(args))
        with patch.dict(sys.modules, {'resource':resource}), patch('signal.alarm') as alarm:
            worker.install_limits()
        self.assertEqual(seen, [(1,(60,65)),(2,(3*1024**3,3*1024**3)),(3,(4_000_000,4_000_000)),(4,(128,128))])
        alarm.assert_called_once_with(75)
        self.assertEqual(worker.offline_environment(), duration._offline_env())

    def test_boot_isolates_before_runtime_and_serves_fixture(self):
        import io
        from types import SimpleNamespace
        from sela_live_tts import duration_worker as worker
        events = []
        guard = SimpleNamespace(isolate=lambda: events.append('isolate') or True,
            check_assets=lambda *a: events.append('assets'),
            selected_profile_path=lambda *a: events.append('profile') or Path('/fixture/profile.json'))
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text('{"files":[]}')
            with patch.object(worker, 'verify_native', side_effect=lambda sha: (events.append('pins') or ({'sdk_root':directory}, guard))), \
                 patch.object(worker, 'install_limits', side_effect=lambda: events.append('limits')), \
                 patch.object(worker, 'check_versions', side_effect=lambda: events.append('versions')), \
                 patch.object(worker, 'start_resource_guard', side_effect=lambda g: events.append('memory')), \
                 patch.object(worker, 'load_predictor', side_effect=lambda *a: events.append('runtime') or (lambda text: {'seconds':2.0,'tokens':9})), \
                 patch.object(worker, 'arm_deadline'), patch.dict(os.environ, {'API_KEY':'never-pass'}):
                worker.main(['serve',directory,str(manifest),'conditioning_b','a'*64],
                            stdin=io.BytesIO(b'{"id":1,"text":"Halo."}\n'), stdout=output)
        self.assertEqual(events, ['pins','limits','isolate','assets','profile','versions','memory','runtime'])
        import json
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(lines[0]['type'], 'ready')
        self.assertEqual(lines[1], {'id':1,'seconds':2.0,'tokens':9})

    def test_loader_opens_only_duration_graph_and_style_dp(self):
        from sela_live_tts import duration_worker as worker
        from types import SimpleNamespace
        events = []
        class Array:
            def reshape(self, *shape):
                events.append(('shape', shape))
                return self
        np = SimpleNamespace(float32='float32', asarray=lambda value, dtype: Array())
        ort = SimpleNamespace(SessionOptions=lambda: SimpleNamespace(),
            GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL='all'),
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL='sequential'),
            InferenceSession=lambda path, **kw: events.append(('graph', str(path), kw)) or object())
        def processor(path):
            events.append(('processor', str(path)))
            return object()
        with tempfile.TemporaryDirectory() as directory:
            style = Path(directory) / 'style.json'
            style.write_text('{"style_dp":{"dims":[1,8,16],"data":[]}}')
            runtime = worker.load_predictor(Path(directory), style, ort=ort, np=np, processor_class=processor)
        self.assertIsInstance(runtime, worker.Predictor)
        graphs = [event for event in events if event[0] == 'graph']
        self.assertEqual(len(graphs), 1)
        self.assertTrue(graphs[0][1].endswith('/onnx/duration_predictor.onnx'))
        self.assertEqual(graphs[0][2]['providers'], ['CPUExecutionProvider'])
        opts = graphs[0][2]['sess_options']
        self.assertEqual((opts.intra_op_num_threads, opts.inter_op_num_threads, opts.execution_mode), (2,1,'sequential'))
        self.assertIn(('shape', (1,8,16)), events)

    def test_prediction_uses_exact_preprocessor_dp_and_speed(self):
        from sela_live_tts import duration_worker as worker
        class Tensor:
            def __init__(self, value=0, count=7):
                self.value, self.shape, self.size = value, (1, count), 1
            def __truediv__(self, speed):
                return Tensor(self.value / speed)
            def reshape(self, *args):
                return [self.value]
        ids, mask, style_dp = Tensor(), Tensor(), Tensor()
        calls = []
        def processor(texts, lang):
            calls.append((texts, lang))
            return ids, mask
        class DP:
            def run(self, output_names, inputs):
                calls.append(inputs)
                return [Tensor(10.5)]
        predict = worker.Predictor(processor, DP(), style_dp)
        self.assertEqual(predict('Halo.'), {'seconds':10.0, 'tokens':7})
        self.assertEqual(calls[0], (['Halo.'], 'id'))
        self.assertIs(calls[1]['text_ids'], ids)
        self.assertIs(calls[1]['text_mask'], mask)
        self.assertIs(calls[1]['style_dp'], style_dp)
        for count in (0, 32769):
            with self.subTest(count=count), self.assertRaises(ValueError):
                worker.Predictor(lambda texts, lang: (Tensor(count=count), mask), DP(), style_dp)('a')

    def test_framing_and_prefix_validation(self):
        from sela_live_tts import duration_worker as worker
        self.assertEqual(worker.parse_request(b'{"id":1,"text":"Halo."}\n', 1), (1, 'Halo.'))
        for raw in (b'{"id":1,"id":1,"text":"a"}\n', b'{"id":true,"text":"a"}\n',
                    b'{"id":2,"text":"a"}\n', b'{"id":1,"text":"a"}',
                    b'{"id":1,"text":"a","speed":1}\n', b'{"id":1,"text":NaN}\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                worker.parse_request(raw, 1)
        for profile in ('conditioning_b_evil', 'conditioning_b_active_low/../../x', 'original'):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                worker.validate_profile(profile)
        self.assertEqual(worker.validate_profile('conditioning_b_active_low'), 'conditioning_b_active_low')


if __name__ == '__main__':
    unittest.main()
