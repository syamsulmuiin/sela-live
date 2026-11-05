"""Offline vertical Speech -> natural planner/renderer integration contracts."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import test_sela_live_integration as legacy
from voice_app.speech import Speech, LocalAudioBusy

ROOT = Path(__file__).resolve().parents[2]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def configured(**overrides):
    with legacy.configured(VOICE_AUTOMATIC_CONDITIONING='1') as env:
        root = Path(env['VOICE_SELA_LIVE_WORKER']).parents[1]
        for directory, names in (
            ('sela_live_tts', ('__init__.py', 'backend.py', 'admission.py', 'controls.py',
                              'source_pins.py', 'worker.py', 'natural.py', 'utterance.py',
                              'duration.py', 'natural_worker.py', 'duration_worker.py')),
            ('voice_app', ('approved_conditioning.py', 'automatic_conditioning.py', 'supertonic3_worker.py', 'zipformer_state.py')),
            ('sela_live', ('__init__.py', 'audio/__init__.py', 'audio/stt.py', 'audio/tts.py')),
            ('vendor/supertonic', ('__init__.py', 'cli.py', 'config.py', 'core.py', 'loader.py',
                                   'pipeline.py', 'utils.py', 'server/__init__.py', 'server/app.py',
                                   'server/audio.py', 'server/routes.py', 'server/schemas.py', 'server/styles_store.py'))):
            for name in names:
                target = root / directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                source = ROOT / directory / name
                target.write_bytes(source.read_bytes())
        path = root / 'sela_live_tts/natural-runtime-manifest.json'
        files = {str(p.relative_to(root)): sha(p.read_bytes()) for p in root.rglob('*.py')}
        files['sela_live_tts/upstream-manifest.json'] = sha((root/'sela_live_tts/upstream-manifest.json').read_bytes())
        path.write_text(json.dumps({'schema_version':1, 'files':files}))
        worker = root/'sela_live_tts/natural_worker.py'
        duration = root/'sela_live_tts/duration_worker.py'
        yield env | {'VOICE_SELA_LIVE_POLICY':'natural_v1',
                     'VOICE_SELA_LIVE_WORKER':str(worker),
                     'VOICE_SELA_LIVE_WORKER_SHA256':sha(worker.read_bytes()),
                     'VOICE_SELA_LIVE_DURATION_WORKER':str(duration),
                     'VOICE_SELA_LIVE_DURATION_WORKER_SHA256':sha(duration.read_bytes()),
                     'VOICE_SELA_LIVE_SOURCE_MANIFEST_SHA256':files['sela_live_tts/upstream-manifest.json'],
                     'VOICE_SELA_LIVE_RUNTIME_MANIFEST':str(path),
                     'VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256':sha(path.read_bytes())} | overrides


class NaturalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_speech_uses_whole_context_and_retains_one_response_lease(self):
        from voice_app.delivery import load_registry
        from sela_live_tts.admission import AdmissionCoordinator,LiveTTSBusy
        with configured() as env:
            engine=legacy.speech(env);events=[];predicted=[];rendered=[]
            engine.local_preview=True;engine.asr_engine='zipformer_id_int8'
            gate=AdmissionCoordinator(env['VOICE_SELA_LIVE_LOCK'],production_probe=lambda:False)
            @asynccontextmanager
            async def quiesce(timeout):
                events.append('quiesce')
                try:yield
                finally:
                    self.assertTrue(engine._local_slot.locked())
                    events.append('resume')
            engine._stream_asr=type('ASR',(),{'quiesce':staticmethod(quiesce)})()
            @asynccontextmanager
            async def duration(**kwargs):
                self.assertTrue(engine._local_slot.locked())
                with self.assertRaises(LiveTTSBusy),gate.claim():pass
                class Predictor:
                    async def predict(self,text,*,profile_id=None):
                        predicted.append((text,profile_id))
                        return {'seconds':10.,'tokens':100}
                try:yield Predictor()
                finally:events.append('dp_closed')
            async def native(args,payload,limit,**kwargs):
                self.assertIn('dp_closed',events)
                self.assertNotIn('resume',events)
                self.assertTrue(engine._local_slot.locked())
                rendered.append((args,json.loads(payload)))
                return legacy.fixtures.fixture_wav()
            text=('Konteks kalimat ini tidak boleh dipotong pada setiap koma, karena semuanya masih satu kalimat. '*4).strip()
            text+='\n\n'+text
            selected=load_registry().resolve('sadness','low')
            with patch('sela_live_tts.duration.DurationSession',side_effect=duration), \
                 patch('voice_app.speech._bounded_process',side_effect=native):
                stream=engine.synthesize_chunks(text,delivery=selected)
                first=await anext(stream)
                with self.assertRaises(LiveTTSBusy),gate.claim():pass
                object.__setattr__(selected,'emotion','anger')
                pieces=[first]+[x async for x in stream]
            self.assertEqual(len(pieces),2)
            self.assertEqual(''.join(x[0] for x in pieces),text)
            self.assertEqual(events,['quiesce','dp_closed','resume'])
            self.assertEqual(len(rendered),2)
            self.assertTrue(all(x[0][-2:]==('conditioning_b_calm_low','12') for x in rendered))
            self.assertTrue(all(p=='conditioning_b_calm_low' for t,p in predicted))
            self.assertFalse(engine._local_slot.locked())
            with gate.claim():pass

    async def test_failed_dp_cleanup_retains_real_host_lease_asr_and_local_slot(self):
        from sela_live_tts.admission import AdmissionCoordinator,LiveTTSBusy
        with configured() as env:
            engine=legacy.speech(env);resumed=[];borrowed=[]
            engine.local_preview=True;engine.asr_engine='zipformer_id_int8'
            @asynccontextmanager
            async def quiesce(timeout):
                try:yield
                finally:resumed.append(True)
            setattr(engine,'_stream_asr',type('ASR',(),{'quiesce':staticmethod(quiesce)})())
            @asynccontextmanager
            async def duration(**kwargs):
                lease=kwargs['coordinator'].claim();lease.__enter__();borrowed.append(lease)
                class Predictor:
                    async def predict(self,text,*,profile_id=None):return {'seconds':2.,'tokens':10}
                try:yield Predictor()
                finally:raise RuntimeError('fixture cleanup failed before owned lease release')
            gate=AdmissionCoordinator(env['VOICE_SELA_LIVE_LOCK'],production_probe=lambda:False)
            stream=engine.synthesize_chunks('Jangan lepaskan slot sebelum worker selesai.')
            try:
                with patch('sela_live_tts.duration.DurationSession',side_effect=duration):
                    with self.assertRaisesRegex(RuntimeError,'fixture cleanup failed'):await anext(stream)
                self.assertEqual(resumed,[])
                self.assertTrue(engine._local_slot.locked())
                with self.assertRaises(LiveTTSBusy),gate.claim():pass
            finally:
                for lease in borrowed:lease.__exit__(None,None,None)
                retained=getattr(engine,'_natural_failed_lease',None)
                if retained is not None:await retained.aclose()
                if engine._local_slot.locked():engine._local_slot.release()
                await stream.aclose()

    async def test_repeated_dp_cancellation_reaps_real_child_before_asr_and_host_release(self):
        import os
        from sela_live_tts.duration import DurationSession
        from sela_live_tts.admission import AdmissionCoordinator,LiveTTSBusy
        with configured() as env:
            engine=legacy.speech(env);engine.local_preview=True;engine.asr_engine='zipformer_id_int8'
            resumed=[];children=[]
            ready=Path(env['VOICE_SELA_LIVE_LOCK']).with_name('query-started')
            script=("import sys,time,pathlib;print('{\\\"type\\\":\\\"ready\\\",\\\"protocol\\\":1,\\\"max_queries\\\":64,\\\"native_network_denied\\\":true}',flush=True);"
                    "sys.stdin.readline();"+f"pathlib.Path({str(ready)!r}).write_text('ready');time.sleep(30)")
            @asynccontextmanager
            async def quiesce(timeout):
                try:yield
                finally:
                    self.assertTrue(all(c.returncode is not None for c in children))
                    self.assertTrue(engine._local_slot.locked());resumed.append(True)
            setattr(engine,'_stream_asr',type('ASR',(),{'quiesce':staticmethod(quiesce)})())
            async def spawn(*args,**kwargs):
                child=await asyncio.create_subprocess_exec(sys.executable,'-I','-c',script,**kwargs)
                original=child.wait
                async def delayed_wait():
                    await asyncio.sleep(.15)
                    return await original()
                child.wait=delayed_wait;children.append(child)
                return child
            def factory(**kwargs):return DurationSession(**kwargs,process_factory=spawn)
            gate=AdmissionCoordinator(env['VOICE_SELA_LIVE_LOCK'],production_probe=lambda:False)
            stream=engine.synthesize_chunks('Pembatalan harus menunggu worker benar-benar selesai.')
            task=None
            try:
                with patch('sela_live_tts.duration.DurationSession',side_effect=factory):
                    task=asyncio.create_task(anext(stream))
                    async with asyncio.timeout(3):
                        while not ready.exists():await asyncio.sleep(.005)
                    task.cancel();await asyncio.sleep(.02);task.cancel();await asyncio.sleep(.02)
                    self.assertEqual(resumed,[])
                    with self.assertRaises(LiveTTSBusy),gate.claim():pass
                    with self.assertRaises(asyncio.CancelledError):await task
                self.assertEqual(resumed,[True])
                self.assertFalse(engine._local_slot.locked())
                with gate.claim():pass
                for child in children:
                    with self.assertRaises(ProcessLookupError):os.kill(child.pid,0)
            finally:
                if task is not None and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
                await stream.aclose()
                for child in children:
                    if child.returncode is None:child.kill();await child.wait()

    async def test_runtime_inventory_rejected_before_any_natural_import_or_probe(self):
        import builtins
        original_import = builtins.__import__
        for kind in ('missing_pin', 'relative_manifest', 'bad_digest', 'drift', 'missing_required',
                     'unknown', 'traversal', 'absolute', 'alias', 'duplicate', 'schema',
                     'symlink', 'symlink_parent', 'fifo', 'oversize', 'declared_drift',
                     'duration_path', 'worker_name', 'pin_disagreement'):
            with self.subTest(kind=kind), configured() as env:
                manifest = Path(env['VOICE_SELA_LIVE_RUNTIME_MANIFEST'])
                data = json.loads(manifest.read_bytes())
                if kind == 'missing_pin': env.pop('VOICE_SELA_LIVE_DURATION_WORKER_SHA256')
                elif kind == 'relative_manifest': env['VOICE_SELA_LIVE_RUNTIME_MANIFEST'] = 'relative.json'
                elif kind == 'bad_digest': env['VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256'] = 'z'*64
                elif kind == 'drift': manifest.write_text('{}')
                elif kind == 'missing_required': data['files'].pop('sela_live_tts/natural.py')
                elif kind == 'unknown': data['files']['surprise.py'] = '0'*64
                elif kind == 'traversal': data['files']['sela_live_tts/../surprise.py'] = '0'*64
                elif kind == 'absolute': data['files']['/root/surprise.py'] = '0'*64
                elif kind == 'alias': data['files']['sela_live_tts//natural.py'] = data['files']['sela_live_tts/natural.py']
                elif kind == 'schema': data['schema_version'] = True
                elif kind == 'declared_drift':
                    manifest.with_name('utterance.py').write_text('# unreviewed')
                elif kind == 'duration_path': env['VOICE_SELA_LIVE_DURATION_WORKER'] = str(manifest.with_name('worker.py'))
                elif kind == 'worker_name': env['VOICE_SELA_LIVE_WORKER'] = str(manifest.with_name('worker.py'))
                elif kind == 'pin_disagreement': env['VOICE_SELA_LIVE_DURATION_WORKER_SHA256'] = '0'*64
                elif kind == 'symlink':
                    saved = manifest.with_name('saved.json'); saved.write_bytes(manifest.read_bytes())
                    manifest.unlink(); manifest.symlink_to(saved)
                elif kind == 'symlink_parent':
                    alias = manifest.parents[1]/'alias'; alias.symlink_to(manifest.parent, target_is_directory=True)
                    env['VOICE_SELA_LIVE_RUNTIME_MANIFEST'] = str(alias/manifest.name)
                elif kind == 'fifo':
                    import os
                    manifest.unlink(); os.mkfifo(manifest, 0o600)
                elif kind == 'oversize':
                    manifest.write_bytes(b' '*1_000_001)
                    env['VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256'] = sha(manifest.read_bytes())
                if kind in ('missing_required','unknown','traversal','absolute','alias','schema'):
                    manifest.write_text(json.dumps(data))
                    env['VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256'] = sha(manifest.read_bytes())
                if kind == 'duplicate':
                    manifest.write_text('{"schema_version":1,"schema_version":1,"files":{}}')
                    env['VOICE_SELA_LIVE_RUNTIME_MANIFEST_SHA256'] = sha(manifest.read_bytes())
                imports = []
                def tracked(name, *args, **kwargs):
                    if name in ('sela_live_tts.natural', 'sela_live_tts.utterance', 'sela_live_tts.duration'):
                        imports.append(name)
                    return original_import(name, *args, **kwargs)
                with patch('builtins.__import__', side_effect=tracked), \
                     patch('voice_app.speech.subprocess.run') as probe:
                    with self.assertRaises(ValueError): Speech(environ=env)
                self.assertEqual(imports, [])
                probe.assert_not_called()

    async def test_policy_is_server_owned_strict_and_requires_noncontradictory_flags(self):
        with configured() as env:
            for changes in ({'VOICE_AUTOMATIC_CONDITIONING':'0'},
                            {'VOICE_EXPERIMENTAL_DELIVERY_PROSODY':'1'},
                            {'VOICE_SELA_LIVE_TTS':'0'},
                            *({'VOICE_SELA_LIVE_POLICY':bad} for bad in ('', 'natural_v2', True, None))):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    legacy.speech(env | changes)
            engine = legacy.speech(env)
            self.assertEqual(engine.tts_identity.get('policy'), 'natural_v1')
        with legacy.configured() as env:
            self.assertNotIn('policy', legacy.speech(env).tts_identity)
