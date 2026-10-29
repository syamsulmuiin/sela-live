"""Privacy-safe native failure boundary; subprocess fixtures do not load models."""
import asyncio
import sys
import unittest
from voice_app import speech


class NativeProcessDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_allowlisted_native_failure_and_exit_code_survive_boundary(self):
        code = ('import sys; sys.stdin.buffer.read(); '
                'sys.stderr.write("PRIVATE transcript and Authorization: secret\\n"); '
                'sys.stderr.write("supertonic3_failure=memory_headroom\\n"); sys.exit(75)')
        with self.assertRaises(RuntimeError) as caught:
            await speech._bounded_process((sys.executable, '-I', '-c', code), b'public fixture', 100,
                                          native_diagnostics=True)
        self.assertEqual(type(caught.exception).__name__, 'NativeTTSFailure')
        self.assertEqual(caught.exception.reason, 'memory_headroom')
        self.assertEqual(caught.exception.returncode, 75)
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertNotIn('secret', repr(caught.exception.__dict__))


class NativeBoundaryEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_noisy_fragmented_and_unknown_stderr_do_not_leak_or_deadlock(self):
        cases = [
            ('import os; os.write(2,b"PRIVATE"*300000+b"\\n"); '
             'os.write(2,b"supertonic3_failure=clip"); os.write(2,b"ping\\n")', 4, 'clipping'),
            ('import os; os.write(2,b"supertonic3_failure=PRIVATE\\n")', 2, 'native_error'),
            ('import os; os.write(2,b"x"*100000+b"supertonic3_failure=clipping\\n")', 9, 'native_error'),
            ('import os; os.write(2,b"supertonic3_failure=clipping")', 1, 'native_error'),
            ('pass', 0, 'empty_output'),
            ('import os,signal; os.kill(os.getpid(), signal.SIGTERM)', None, 'native_error'),
        ]
        for body, exitcode, expected in cases:
            code = 'import sys; sys.stdin.buffer.read(); ' + body
            if exitcode is not None: code += f'; sys.exit({exitcode})'
            with self.subTest(expected=expected):
                with self.assertRaises(speech.NativeTTSFailure) as caught:
                    await asyncio.wait_for(speech._bounded_process(
                        (sys.executable, '-I', '-c', code), b'fixture', 100, native_diagnostics=True), 5)
                self.assertEqual(caught.exception.reason, expected)
                self.assertEqual(caught.exception.returncode, exitcode if exitcode is not None else -15)
                self.assertNotIn('PRIVATE', repr(caught.exception.__dict__))

    async def test_early_exit_during_large_write_preserves_native_reason(self):
        code = 'import sys; sys.stderr.write("supertonic3_failure=memory_headroom\\n"); sys.exit(1)'
        with self.assertRaises(speech.NativeTTSFailure) as caught:
            await speech._bounded_process((sys.executable, '-I', '-c', code), b'x'*1000000,
                                          100, native_diagnostics=True)
        self.assertEqual(caught.exception.reason, 'memory_headroom')
        self.assertEqual(caught.exception.returncode, 1)

    async def test_cancel_reaps_real_child_and_stderr_task(self):
        from unittest.mock import patch
        original = asyncio.create_subprocess_exec
        children = []; spawned = asyncio.Event()
        async def spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child); spawned.set(); return child
        code = 'import os,time; os.write(2,b"PRIVATE"*200000); time.sleep(30)'
        with patch('voice_app.speech.asyncio.create_subprocess_exec', spawn):
            task = asyncio.create_task(speech._bounded_process((sys.executable, '-I', '-c', code),
                                       b'fixture', 100, native_diagnostics=True))
            await asyncio.wait_for(spawned.wait(), 2)
            await asyncio.sleep(.03)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertIsNotNone(children[0].returncode)
        await asyncio.sleep(0)
        self.assertFalse(any('drain_stderr' in t.get_coro().__qualname__ for t in asyncio.all_tasks()))

    async def test_legacy_disabled_mode_remains_generic(self):
        code = 'import sys; sys.stderr.write("supertonic3_failure=clipping\\n"); sys.exit(3)'
        with self.assertRaises(RuntimeError) as caught:
            await speech._bounded_process((sys.executable, '-I', '-c', code), b'', 100)
        self.assertIs(type(caught.exception), RuntimeError)
        self.assertFalse(hasattr(caught.exception, 'reason'))

    async def test_selected_supertonic_adapter_enables_diagnostics_only(self):
        from unittest.mock import patch
        import subprocess
        import test_supertonic3_adapter as fixtures
        with fixtures.configured() as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            engine = speech.Speech(environ=env)
        with patch('voice_app.speech._bounded_process', return_value=fixtures.fixture_wav()) as process:
            await engine.synthesize('Halo')
            self.assertIs(process.call_args.kwargs.get('native_diagnostics'), True)


class ServerNativeDiagnostics(unittest.TestCase):
    def test_server_logs_only_revalidated_reason_exit_and_preserves_public_contract(self):
        from fastapi.testclient import TestClient
        from voice_app.server import create_app
        import tempfile
        class Provider:
            model = 'fixture'
            async def stream(self, messages): yield 'Balasan fixture.'
        for reason, rc, expected_reason, expected_rc in (
            ('memory_headroom', 1, 'memory_headroom', 1),
            ('PRIVATE token', 'PRIVATE password', 'native_error', None),
        ):
            fault = speech.NativeTTSFailure('native_error', 2)
            # Treat metadata as untrusted at the final logging boundary, too.
            fault.reason, fault.returncode = reason, rc
            class Engine:
                stt_available=False; stt_reason='fixture'; tts_label='fixture'
                async def synthesize(self, text): raise fault
            with tempfile.TemporaryDirectory() as directory:
                app = create_app(provider=Provider(), speech=Engine(), data_dir=directory,
                                 public_origin='', local_test_auth=True)
                with TestClient(app, base_url='http://localhost') as client:
                    with client.websocket_connect('/ws', headers={'host':'localhost','origin':'http://localhost'}) as ws:
                        ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
                        events=[]
                        with self.assertLogs('voice_app.server', level='WARNING') as logs:
                            ws.send_json({'type':'text','text':'Pesan fixture'})
                            for _ in range(15):
                                event=ws.receive_json();events.append(event)
                                if event['type']=='error':break
                        self.assertEqual(event['code'], 'tts_failed')
                        self.assertIn(f'native_reason={expected_reason}', str(logs.output))
                        self.assertIn(f'native_exit={expected_rc}', str(logs.output))
                        self.assertNotIn('PRIVATE', str(logs.output)+str(events))
                        state=ws.receive_json()['state']
                        self.assertEqual(state['history'][-1]['heard_text'], '')
                        self.assertEqual(state['history'][-1]['text'], 'Balasan fixture.')


class WorkerReasonTests(unittest.TestCase):
    def test_explicit_worker_reasons_are_fixed_and_unknown_messages_are_discarded(self):
        from voice_app import supertonic3_worker as worker
        self.assertTrue(hasattr(worker, 'failure_reason'), 'safe worker error mapping missing')
        cases = [(ValueError('text_limit'), 'invalid_text'),
                 (ValueError('unsupported_tag'), 'invalid_text'),
                 (ValueError('invalid_audio'), 'invalid_audio'),
                 (ValueError('duration_limit'), 'duration_limit'),
                 (ValueError('clipping'), 'clipping'),
                 (ValueError('runtime_version'), 'runtime_version'),
                 (ValueError('asset_hash'), 'asset_error'),
                 (ValueError('model_revision'), 'asset_error'),
                 (MemoryError('PRIVATE allocation'), 'memory_allocation'),
                 (RuntimeError('PRIVATE request headers'), 'native_error'),
                 (RuntimeError('memory_headroom'), 'native_error'),
                 (ValueError('PRIVATE transcript'), 'native_error')]
        for fault, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(worker.failure_reason(fault), expected)

    def test_missing_malformed_assets_are_explicit_without_native_error_text(self):
        from voice_app import supertonic3_worker as worker
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root/'manifest.json'
            for contents in (None, 'PRIVATE invalid json', '{}'):
                if contents is not None: manifest.write_text(contents)
                with self.assertRaises((ValueError, OSError, KeyError)) as caught:
                    worker.check_assets(root, manifest)
                self.assertEqual(worker.failure_reason(caught.exception), 'asset_error')

    def test_actual_output_validators_distinguish_duration_clipping_and_invalid_audio(self):
        # Exercise the actual validator in the pinned isolated numeric runtime;
        # the application/SDK interpreter need not install numpy or Supertonic.
        import json
        import subprocess
        from pathlib import Path
        script = '''import importlib.util,json,sys
import numpy as np
spec=importlib.util.spec_from_file_location('worker',sys.argv[1])
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
results=[]
for samples in (np.zeros(0), np.array([float('nan')]), np.zeros(44100*30+1), np.array([1.0]), np.zeros(100)):
    runtime=worker.Runtime.__new__(worker.Runtime)
    class Engine:
        def synthesize(self,*args,**kwargs):return samples,None
    runtime.engine,runtime.style=Engine(),None
    try:runtime.synthesize({'text':'Public fixture'})
    except ValueError as exc:results.append(worker.failure_reason(exc))
print(json.dumps(results))
'''
        result = subprocess.run(('/root/voice-lab/autonomous/expressive-tts/venv/bin/python',
                                 '-I', '-c', script, str(Path(speech.__file__).with_name('supertonic3_worker.py'))),
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout),
                         ['invalid_audio', 'invalid_audio', 'duration_limit', 'clipping', 'invalid_audio'])

    def test_memory_admission_and_resource_abort_emit_distinct_safe_markers(self):
        from voice_app import supertonic3_worker as worker
        self.assertTrue(hasattr(worker, 'require_headroom'), 'explicit admission seam missing')
        with self.assertRaises(RuntimeError) as caught:
            worker.require_headroom(1099 * 1024)
        self.assertEqual(worker.failure_reason(caught.exception), 'memory_headroom')
        worker.require_headroom(1100 * 1024)
        worker.guard_resources(500 * 1024, 720 * 1024)
        for available, rss in ((499 * 1024, 100), (2000 * 1024, 721 * 1024)):
            import subprocess
            code = ('from voice_app.supertonic3_worker import guard_resources; '
                    f'guard_resources({available}, {rss}); raise SystemExit(99)')
            result = subprocess.run((sys.executable, '-c', code), capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 75)
            self.assertEqual(result.stderr, b'supertonic3_failure=resource_limit\n')
            self.assertEqual(result.stdout, b'')


if __name__ == '__main__':
    unittest.main()
