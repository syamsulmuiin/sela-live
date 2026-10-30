"""Production bridge contracts; no inference, downloads or production writes."""
import asyncio
import json
import subprocess
import sys
import unittest
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import test_supertonic3_adapter as fixtures
from voice_app.speech import Speech, LocalAudioBusy, NativeTTSFailure

STAGE = Path(__file__).resolve().parents[2]


@contextmanager
def configured(**overrides):
    with fixtures.configured(VOICE_SUPERTONIC3_STYLE_PROFILE='conditioning_b') as env:
        root = Path(env['VOICE_SUPERTONIC3_MANIFEST']).parent
        live = root / 'installed-live'; (live / 'sela_live_tts').mkdir(parents=True)
        for name in ('worker.py', 'source_pins.py', 'controls.py', 'upstream-manifest.json'):
            (live / 'sela_live_tts' / name).write_bytes((STAGE / 'sela_live_tts' / name).read_bytes())
        import hashlib
        worker = live / 'sela_live_tts/worker.py'
        yield env | {'VOICE_SELA_LIVE_TTS':'1', 'VOICE_SELA_LIVE_WORKER':str(worker),
                     'VOICE_SELA_LIVE_WORKER_SHA256':hashlib.sha256(worker.read_bytes()).hexdigest(),
                     'VOICE_SELA_LIVE_PYTHON':sys.executable,
                     'VOICE_SELA_LIVE_LOCK':str(root / 'live.lock')} | overrides


def speech(env):
    with patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
        return Speech(environ=env)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_opt_in_requires_explicit_reviewed_runtime_and_b_anchor(self):
        with configured() as env:
            for flag in ('true', '', '01', 1, True, None):
                with self.subTest(flag=flag), self.assertRaises(ValueError):
                    speech(env | {'VOICE_SELA_LIVE_TTS':flag})
            for key in ('VOICE_SELA_LIVE_WORKER', 'VOICE_SELA_LIVE_WORKER_SHA256',
                        'VOICE_SELA_LIVE_PYTHON', 'VOICE_SELA_LIVE_LOCK'):
                for bad in ('', 'relative', None):
                    with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                        speech(env | {key:bad})
                missing = dict(env); missing.pop(key)
                with self.subTest(missing=key), self.assertRaises(ValueError): speech(missing)
            for changes in ({'VOICE_TTS_BACKEND':'edge'},
                            {'VOICE_SUPERTONIC3_STYLE_PROFILE':'original'},
                            {'VOICE_SELA_LIVE_WORKER_SHA256':'0'*64},
                            {'VOICE_SELA_LIVE_WORKER':'/nonexistent/worker.py'},
                            {'VOICE_SELA_LIVE_PYTHON':'/nonexistent/python'},
                            {'VOICE_SELA_LIVE_LOCK':'/nonexistent/live.lock'}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    speech(env | changes)
            with patch('voice_app.speech._bounded_process', return_value=fixtures.fixture_wav()) as native:
                for flag in ('0', None):
                    disabled = env | {'VOICE_SELA_LIVE_TTS':'0', 'VOICE_SELA_LIVE_WORKER':'nonexistent'}
                    if flag is None: disabled.pop('VOICE_SELA_LIVE_TTS')
                    engine = speech(disabled)
                    await engine.synthesize('Tetap lama.')
                    self.assertEqual(native.call_args.args[0], (env['VOICE_SUPERTONIC3_PYTHON'], '-I',
                        str(STAGE / 'voice_app/supertonic3_worker.py'), 'tts',
                        env['VOICE_SUPERTONIC3_MODEL_PATH'], env['VOICE_SUPERTONIC3_MANIFEST'], 'conditioning_b'))
                    self.assertNotIn('adapter', engine.tts_identity)

    async def test_readiness_checks_actual_live_worker_for_every_routed_profile(self):
        from voice_app.automatic_conditioning import PROFILE_IDS
        with configured(VOICE_AUTOMATIC_CONDITIONING='1') as env:
            with patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as probe:
                Speech(environ=env)
            self.assertEqual(len(probe.call_args_list), len(PROFILE_IDS))
            for call, profile in zip(probe.call_args_list, PROFILE_IDS):
                self.assertEqual(call.args[0], (env['VOICE_SELA_LIVE_PYTHON'], '-I',
                    env['VOICE_SELA_LIVE_WORKER'], 'check', env['VOICE_SUPERTONIC3_MODEL_PATH'],
                    env['VOICE_SUPERTONIC3_MANIFEST'], profile, '12'))
            with patch('voice_app.speech.subprocess.run', side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1)]) as probe:
                with self.assertRaisesRegex(ValueError, 'no fallback'): Speech(environ=env)
                self.assertEqual(probe.call_count, 2)

    async def test_shared_host_admission_excludes_other_apps_and_evaluation_without_asr_probe(self):
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        with configured() as env:
            one, two = speech(env), speech(env)
            entered, release = asyncio.Event(), asyncio.Event()
            async def native(*args, **kwargs):
                entered.set()
                await release.wait()
                return fixtures.fixture_wav()
            eval_gate = AdmissionCoordinator(env['VOICE_SELA_LIVE_LOCK'], production_probe=lambda: False)
            with patch('voice_app.speech._bounded_process', side_effect=native) as process, \
                 patch('sela_live_tts.admission.production_busy', side_effect=AssertionError('production must not probe idle ASR')):
                task = asyncio.create_task(one.synthesize('Pertama.'))
                await entered.wait()
                try:
                    with self.assertRaises(LiveTTSBusy), eval_gate.claim(): pass
                    with self.assertRaises(LocalAudioBusy): await two.synthesize('Kedua.')
                    self.assertEqual(process.call_count, 1)
                finally:
                    release.set()
                    await task
                with eval_gate.claim():
                    with self.assertRaises(LocalAudioBusy): await two.synthesize('Evaluasi aktif.')
                self.assertEqual(process.call_count, 1)
            with eval_gate.claim(): pass

    async def test_evaluation_detects_installed_live_worker_and_uses_same_lock(self):
        from sela_live_tts.admission import AdmissionCoordinator, production_busy, EVALUATION_LOCK
        self.assertEqual(EVALUATION_LOCK, Path('/root/voice-lab/.runtime/live-tts.lock'))
        with configured() as env, patch.dict('os.environ', {'VOICE_SELA_LIVE_LOCK':env['VOICE_SELA_LIVE_LOCK']}):
            self.assertEqual(AdmissionCoordinator().lock_path, Path(env['VOICE_SELA_LIVE_LOCK']))
            proc = Path(env['VOICE_SUPERTONIC3_MANIFEST']).parent / 'proc'; proc.mkdir()
            process = proc / '123'; process.mkdir()
            for argument, cwd in (('/root/voice-lab/sela_live_tts/worker.py', '/root'),
                                  ('sela_live_tts/worker.py', '/root/voice-lab'),
                                  ('/root/voice-lab/sela-live-runtime/sela_live_tts/worker.py', '/root')):
                (process / 'cmdline').write_bytes(b'python\0' + argument.encode() + b'\0')
                (process / 'cwd').unlink(missing_ok=True); (process / 'cwd').symlink_to(cwd)
                with patch.dict('os.environ', {'VOICE_SELA_LIVE_WORKER':'/root/voice-lab/sela-live-runtime/sela_live_tts/worker.py'}):
                    self.assertTrue(production_busy(proc_root=proc), argument)
            (process / 'cmdline').write_bytes(b'python\0/root/sela-live/sela_live_tts/worker.py\0')
            self.assertFalse(production_busy(proc_root=proc))
            (process / 'cmdline').write_bytes(b'python\0-c\0print("/root/voice-lab/sela_live_tts/worker.py")\0')
            self.assertFalse(production_busy(proc_root=proc))

    async def test_repeated_cancellation_reaps_child_before_releasing_shared_lock_and_asr(self):
        import os
        from sela_live_tts.admission import AdmissionCoordinator, LiveTTSBusy
        with configured() as env:
            engine = speech(env)
            engine.local_preview = True; engine.asr_engine = 'zipformer_id_int8'
            children, events = [], []
            @asynccontextmanager
            async def quiesce(timeout):
                events.append('quiesce')
                try: yield
                finally:
                    events.append('resume')
                    self.assertIsNotNone(children[0].returncode, 'ASR resumed before child reap')
                    self.assertTrue(engine._local_slot.locked())
            setattr(engine, '_stream_asr', type('ASR', (), {'quiesce':staticmethod(quiesce)})())
            ready = Path(env['VOICE_SELA_LIVE_LOCK']).with_name('ready')
            script = ('import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                      f'pathlib.Path({str(ready)!r}).write_text("ready"); time.sleep(30)')
            original_spawn = asyncio.create_subprocess_exec
            async def spawn(*args, **kwargs):
                self.assertEqual(events, ['quiesce'])
                child = await original_spawn(sys.executable, '-I', '-c', script, **kwargs)
                children.append(child)
                return child
            gate = AdmissionCoordinator(env['VOICE_SELA_LIVE_LOCK'], production_probe=lambda: False)
            with patch('voice_app.speech.asyncio.create_subprocess_exec', side_effect=spawn):
                task = asyncio.create_task(engine.synthesize('Dibatalkan.'))
                try:
                    async with asyncio.timeout(3):
                        while not ready.exists(): await asyncio.sleep(.005)
                    task.cancel(); await asyncio.sleep(.05)
                    task.cancel(); await asyncio.sleep(.05)
                    self.assertEqual(events, ['quiesce'])
                    with self.assertRaises(LiveTTSBusy), gate.claim(): pass
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(asyncio.shield(task), 4)
                    self.assertEqual(events, ['quiesce', 'resume'])
                    self.assertIsNotNone(children[0].returncode)
                    with self.assertRaises(ProcessLookupError): os.kill(children[0].pid, 0)
                    with gate.claim(): pass
                    self.assertFalse(engine._local_slot.locked())
                finally:
                    for child in children:
                        if child.returncode is None: child.kill(); await child.wait()
                    if not task.done(): task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_worker_drift_after_startup_fails_closed_before_spawn(self):
        with configured() as env:
            engine = speech(env)
            worker = Path(env['VOICE_SELA_LIVE_WORKER'])
            original = worker.read_bytes()
            for mode in ('drift', 'symlink', 'missing'):
                worker.unlink(missing_ok=True)
                if mode == 'drift': worker.write_bytes(original + b'\n')
                if mode == 'symlink': worker.symlink_to(STAGE / 'sela_live_tts/worker.py')
                with self.subTest(mode=mode), patch('voice_app.speech._bounded_process', return_value=fixtures.fixture_wav()) as native:
                    with self.assertRaisesRegex(ValueError, 'no fallback'): await engine.synthesize('Utuh.')
                    native.assert_not_called()
                if worker.is_symlink(): worker.unlink()
            worker.write_bytes(original)

    async def test_shared_lock_rejects_unsafe_parent_or_relative_configuration(self):
        import os
        from sela_live_tts.admission import ProductionAdmissionCoordinator, LiveTTSBusy
        with configured() as env:
            root = Path(env['VOICE_SELA_LIVE_LOCK']).parent
            real = root / 'real'; real.mkdir()
            linked = root / 'linked'; linked.symlink_to(real, target_is_directory=True)
            for path in (linked / 'lock', Path(os.path.relpath(root / 'relative.lock')), root / 'missing' / 'lock'):
                with self.subTest(path=path), self.assertRaises(LiveTTSBusy):
                    with ProductionAdmissionCoordinator(path).claim(): pass
            self.assertFalse((root / 'missing').exists())
            self.assertFalse((real / 'lock').exists())
            for kind in ('fifo', 'symlink', 'hardlink', 'wrong_mode'):
                target = root / kind
                if kind == 'fifo': os.mkfifo(target, 0o600)
                if kind == 'symlink': target.symlink_to(root / 'missing-target')
                if kind == 'hardlink':
                    source = root / 'hardlink-source'; source.touch(mode=0o600); os.link(source, target)
                if kind == 'wrong_mode': target.touch(mode=0o644)
                with self.subTest(kind=kind), self.assertRaises(LiveTTSBusy):
                    with ProductionAdmissionCoordinator(target).claim(): pass

    async def test_live_preserves_router_chunk_snapshot_ack_and_native_failure_without_fallback(self):
        from voice_app.delivery import load_registry
        registry = load_registry()
        with configured(VOICE_AUTOMATIC_CONDITIONING='1') as env:
            engine = speech(env)
            calls = []
            async def native(args, payload, limit, **kwargs):
                calls.append((args, json.loads(payload)))
                return fixtures.fixture_wav()
            with patch('voice_app.speech._bounded_process', side_effect=native):
                for activation, emotion in (('calm', 'sadness'), ('active', 'joy')):
                    for intensity in ('low', 'medium', 'high'):
                        await engine.synthesize('API 21', delivery=registry.resolve(emotion, intensity))
                        self.assertEqual(calls[-1][0][-2:], (f'conditioning_b_{activation}_{intensity}', '12'))
                        self.assertEqual(calls[-1][1], {'text':'a pe i dua puluh satu'})
                delivery = registry.resolve('sadness', 'low')
                text = ('Kalimat panjang ini harus tetap utuh untuk ACK dan riwayat pendengar. ' * 18).strip()
                iterator = engine.synthesize_chunks(text, delivery=delivery)
                start = len(calls)
                parts = [await anext(iterator)]
                object.__setattr__(delivery, 'emotion', 'anger')
                object.__setattr__(delivery, 'intensity', 'high')
                async for part in iterator: parts.append(part)
                self.assertGreater(len(parts), 1)
                self.assertEqual(''.join(x[0] for x in parts), text)
                self.assertEqual([x[2] for x in parts], [False]*(len(parts)-1) + [True])
                self.assertTrue(all(x[0][-2] == 'conditioning_b_calm_low' for x in calls[start:]))
            for reason in ('asset_error', 'memory_headroom', 'resource_limit', 'network_sandbox', 'native_error'):
                failure = NativeTTSFailure(reason, 75)
                with patch('voice_app.speech._bounded_process', side_effect=failure) as native:
                    with self.assertRaises(NativeTTSFailure) as caught: await engine.synthesize('Tidak diulang.')
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(native.call_count, 1)
                    self.assertEqual(native.call_args.args[0][-2:], ('conditioning_b', '12'))
            with patch('voice_app.speech._bounded_process') as native:
                for bad in ('/root/profile.json', 'conditioning_b_unknown', 'conditioning_b/../../bad'):
                    with self.assertRaises(ValueError): await engine._synthesize('Utuh.', None, profile_id=bad)
                native.assert_not_called()

    async def test_real_child_success_safe_failure_output_limit_and_timeout_reap_before_asr_resume(self):
        original_spawn = asyncio.create_subprocess_exec
        original_timeout = asyncio.timeout
        for outcome in ('success', 'failure', 'limit', 'timeout'):
            with self.subTest(outcome=outcome), configured() as env:
                engine = speech(env)
                engine.local_preview = True; engine.asr_engine = 'zipformer_id_int8'
                children, events = [], []
                @asynccontextmanager
                async def quiesce(timeout):
                    events.append('quiesce')
                    try: yield
                    finally:
                        self.assertIsNotNone(children[0].returncode)
                        self.assertTrue(engine._local_slot.locked())
                        events.append('resume')
                setattr(engine, '_stream_asr', type('ASR', (), {'quiesce':staticmethod(quiesce)})())
                script = ('import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(' + repr(fixtures.fixture_wav()) + ')'
                    if outcome == 'success' else
                    'import sys; sys.stderr.write("supertonic3_failure=resource_limit\\n"); sys.exit(75)'
                    if outcome == 'failure' else
                    'import sys; sys.stdout.buffer.write(b"x"*4000001); sys.stdout.buffer.flush()'
                    if outcome == 'limit' else 'import time; time.sleep(30)')
                async def spawn(*args, **kwargs):
                    self.assertEqual(events, ['quiesce'])
                    self.assertEqual(args[2], env['VOICE_SELA_LIVE_WORKER'])
                    self.assertEqual(args[-1], '12')
                    self.assertNotIn('VOICE_STT_API_KEY', kwargs['env'])
                    child = await original_spawn(sys.executable, '-I', '-c', script, **kwargs)
                    children.append(child); return child
                def bounded_timeout(delay):
                    return original_timeout(.2 if outcome == 'timeout' and delay == 90 else delay)
                with patch('voice_app.speech.asyncio.create_subprocess_exec', side_effect=spawn) as spawn_call, \
                     patch('voice_app.speech.asyncio.timeout', side_effect=bounded_timeout):
                    if outcome == 'success': self.assertEqual(await engine.synthesize('Utuh.'), fixtures.fixture_wav())
                    else:
                        expected = NativeTTSFailure if outcome == 'failure' else ValueError if outcome == 'limit' else TimeoutError
                        with self.assertRaises(expected) as caught: await engine.synthesize('Utuh.')
                        if isinstance(caught.exception, NativeTTSFailure):
                            self.assertEqual(caught.exception.reason, 'resource_limit')
                    self.assertEqual(spawn_call.call_count, 1)
                self.assertEqual(events, ['quiesce', 'resume'])
                self.assertFalse(engine._local_slot.locked())

    async def test_opt_in_dispatches_installed_clone_worker_at_quality12(self):
        with configured() as env:
            engine = speech(env)
            with patch('voice_app.speech._bounded_process', return_value=fixtures.fixture_wav()) as native:
                await engine.synthesize('API 21')
            args, payload, limit = native.call_args.args
            self.assertEqual(args, (env['VOICE_SELA_LIVE_PYTHON'], '-I',
                env['VOICE_SELA_LIVE_WORKER'],
                'tts', env['VOICE_SUPERTONIC3_MODEL_PATH'], env['VOICE_SUPERTONIC3_MANIFEST'],
                'conditioning_b', '12'))
            self.assertEqual(json.loads(payload), {'text':'a pe i dua puluh satu'})
            self.assertEqual(limit, 4_000_000)
            self.assertTrue(native.call_args.kwargs['native_diagnostics'])
            self.assertEqual(engine.tts_identity['total_steps'], 12)
            self.assertEqual(engine.tts_identity['backend'], 'supertonic3')
            self.assertEqual(native.call_count, 1)


if __name__ == '__main__':
    unittest.main()
