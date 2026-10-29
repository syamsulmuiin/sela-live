"""Automatic activation conditioning contracts; native probes run separately."""
import unittest
from pathlib import Path
from dataclasses import replace
from voice_app.delivery import load_registry

class RouterTests(unittest.TestCase):
    def test_bounded_profiles_and_pure_neutral(self):
        from voice_app.automatic_conditioning import load_router
        registry = load_registry()
        router = load_router(registry=registry)
        self.assertEqual(set(router.emotions), set(registry.ids))
        for emotion in registry.ids:
            for intensity in ('low', 'medium', 'high'):
                result = router.select(registry.resolve(emotion, intensity))
                activation = 'base' if emotion == 'neutral' else ('calm' if emotion in ('sadness','trust','disgust') else 'active')
                profile = 'conditioning_b' if activation == 'base' else f'conditioning_b_{activation}_{intensity}'
                self.assertEqual(result, dict(profile_id=profile, activation=activation, intended_emotion=emotion, intensity=intensity, supported=True))
                result['profile_id'] = 'tampered'
                self.assertEqual(router.select(registry.resolve(emotion,intensity))['profile_id'], profile)
        for bad in (None, {}, replace(registry.resolve(), intensity='max'), replace(registry.resolve(), speed=float('nan')), replace(registry.resolve(), emotion='unknown')):
            with self.assertRaises(ValueError): router.select(bad)

class StrictRouterTests(unittest.TestCase):
    def test_extension_fallback_and_strict_rules(self):
        import json, tempfile
        from voice_app.delivery import DeliveryRegistry
        from voice_app.automatic_conditioning import load_router
        from types import MappingProxyType
        registry = load_registry()
        extended = DeliveryRegistry(2, MappingProxyType(dict(registry.entries) | {'curiosity': registry.entries['neutral']}))
        router = load_router(registry=extended)
        result = router.select(extended.resolve('curiosity', 'high'))
        self.assertEqual(result, dict(profile_id='conditioning_b', activation='base', intended_emotion='curiosity', intensity='high', supported=False, reason_code='unmapped_registered_emotion'))
        original = Path(__file__).parent / 'voice_app/automatic_conditioning_rules.json'
        data = json.loads(original.read_text())
        mutations = [dict(data, version=True), dict(data, version=float('nan')), dict(data, extra=1), dict(data, routes=dict(data['routes'], neutral='active')), dict(data, routes=dict(data['routes'], curiosity='active')), dict(data, routes=dict(data['routes'], joy='unknown'))]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'rules.json'
            for item in mutations:
                p.write_text(json.dumps(item))
                with self.assertRaises(ValueError): load_router(p, registry=registry)
            for raw in ('{"version":1,"version":2,"routes":{"neutral":"base"}}', ' ' * 32769):
                p.write_text(raw)
                with self.assertRaises(ValueError): load_router(p)

class SpeechTests(unittest.IsolatedAsyncioTestCase):
    def speech(self, **overrides):
        import subprocess
        import test_supertonic3_adapter as fixtures
        from unittest.mock import patch
        from voice_app.speech import Speech
        with fixtures.configured(VOICE_SUPERTONIC3_STYLE_PROFILE='conditioning_b', **overrides) as env, patch('voice_app.speech.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            return Speech(environ=env)

    async def test_flags_and_auto_without_pacing(self):
        from unittest.mock import patch
        from voice_app.speech import Speech
        disabled = self.speech()
        self.assertFalse(disabled.automatic_conditioning_enabled)
        self.assertFalse(disabled.delivery_intent_supported)
        self.assertIsNone(disabled.conditioning_for(None))
        for value in ('true', '', '01', 1, True, None):
            with self.assertRaises(ValueError): self.speech(VOICE_AUTOMATIC_CONDITIONING=value)
        for env in ({'VOICE_TTS_BACKEND':'edge','VOICE_AUTOMATIC_CONDITIONING':'1'}, {'VOICE_TTS_BACKEND':'supertonic3','VOICE_AUTOMATIC_CONDITIONING':'1'}):
            with self.assertRaises(ValueError): Speech(environ=env)
        speech = self.speech(VOICE_AUTOMATIC_CONDITIONING='1')
        self.assertTrue(speech.delivery_intent_supported)
        self.assertFalse(speech.delivery_controls_supported)
        self.assertIsInstance(speech.automatic_conditioning_emotions, tuple)
        import test_supertonic3_adapter as fixtures
        with patch('voice_app.speech._bounded_process', return_value=fixtures.fixture_wav()) as process:
            await speech.synthesize('Kita coba sekarang.', delivery=load_registry().resolve('joy','high'))
            self.assertEqual(process.call_args.args[0][-1], 'conditioning_b_active_high')
            self.assertEqual(__import__('json').loads(process.call_args.args[1]), {'text':'Kita coba sekarang.'})
            await speech.synthesize('Kita coba sekarang.', delivery=load_registry().resolve())
            self.assertEqual(process.call_args.args[0][-1], 'conditioning_b')
            await speech.synthesize('Tanpa metadata.')
            self.assertEqual(process.call_args.args[0][-1], 'conditioning_b')
        self.assertEqual(speech.tts_identity['voice_style_profile'],'conditioning_b')

    async def test_chunk_snapshot_and_concurrent_response_isolation(self):
        from unittest.mock import patch
        import test_supertonic3_adapter as fixtures
        import asyncio
        speech = self.speech(VOICE_AUTOMATIC_CONDITIONING='1')
        registry = load_registry()
        delivery = registry.resolve('sadness','low')
        calls = []
        async def process(args, payload, limit, **kwargs):
            calls.append((args[-1], __import__('json').loads(payload)))
            await asyncio.sleep(0)
            return fixtures.fixture_wav()
        text = ('Ini kalimat panjang yang tetap harus dibacakan lengkap tanpa mengubah sumber. ' * 24).strip()
        with patch('voice_app.speech._bounded_process', side_effect=process):
            iterator = speech.synthesize_chunks(text, delivery=delivery)
            first = await anext(iterator)
            object.__setattr__(delivery, 'emotion', 'anger') # after admitted snapshot
            object.__setattr__(delivery, 'intensity', 'high')
            await speech.synthesize('Respons lain.', delivery=registry.resolve('joy','high'))
            parts = [first]
            async for part in iterator: parts.append(part)
        self.assertGreater(len(parts),1)
        self.assertEqual(calls[1][0],'conditioning_b_active_high')
        self.assertTrue(all(profile=='conditioning_b_calm_low' for i,(profile,_) in enumerate(calls) if i!=1))
        self.assertEqual(''.join(part[0] for part in parts), text)

    async def test_selection_once_pacing_separate_and_new_failure_no_retry(self):
        from unittest.mock import patch
        import test_supertonic3_adapter as fixtures
        from voice_app.speech import NativeTTSFailure
        import json
        registry=load_registry()
        speech=self.speech(VOICE_AUTOMATIC_CONDITIONING='1', VOICE_EXPERIMENTAL_DELIVERY_PROSODY='1')
        delivery=registry.resolve('joy','medium')
        with patch.object(speech._conditioning_router,'select', wraps=speech._conditioning_router.select) as selection, patch('voice_app.speech._bounded_process',return_value=fixtures.fixture_wav()) as process:
            await speech.synthesize('Satu respons.',delivery=delivery)
            self.assertEqual(selection.call_count,1)
            self.assertEqual(json.loads(process.call_args.args[1])['delivery'],delivery.native_controls())
            self.assertEqual(process.call_args.args[0][-1], 'conditioning_b_active_medium')
        with patch('voice_app.speech._bounded_process',side_effect=NativeTTSFailure('asset_error',1)) as process:
            with self.assertRaises(NativeTTSFailure): await speech.synthesize('Tidak diulang.',delivery=delivery)
            self.assertEqual(process.call_count,1)
            self.assertEqual(process.call_args.args[0][-1],'conditioning_b_active_medium')
        with self.assertRaises(AttributeError): speech.automatic_conditioning_enabled=False
        with self.assertRaises(AttributeError): speech.delivery_intent_supported=False
        fixed=self.speech()
        with self.assertRaises(ValueError): await fixed.synthesize('Eksperimen mati.',delivery=delivery)

    async def test_neutral_all_intensities_and_plain_input_exact_fixed_b(self):
        from unittest.mock import patch
        import test_supertonic3_adapter as fixtures
        registry=load_registry()
        from voice_app.speech import Speech
        import subprocess
        with fixtures.configured(VOICE_SUPERTONIC3_STYLE_PROFILE='conditioning_b') as env, patch('voice_app.speech.subprocess.run',return_value=subprocess.CompletedProcess([],0)):
            fixed=Speech(environ=env)
            auto=Speech(environ=env | {'VOICE_AUTOMATIC_CONDITIONING':'1'})
        calls=[]
        async def process(args,payload,limit,**kwargs):
            calls.append((args,payload,limit,kwargs))
            return fixtures.fixture_wav()
        with patch('voice_app.speech._bounded_process',side_effect=process):
            anchor=await fixed.synthesize('Teks yang sama, tetap utuh.')
            for delivery in (None,*(registry.resolve('neutral',x) for x in ('low','medium','high'))):
                self.assertEqual(await auto.synthesize('Teks yang sama, tetap utuh.',delivery=delivery),anchor)
                self.assertEqual(calls[-1],calls[0])

    async def test_enabled_readiness_fails_closed_for_any_new_level(self):
        import subprocess
        import test_supertonic3_adapter as fixtures
        from unittest.mock import patch
        from voice_app.speech import Speech
        with fixtures.configured(VOICE_SUPERTONIC3_STYLE_PROFILE='conditioning_b',VOICE_AUTOMATIC_CONDITIONING='1') as env:
            with patch('voice_app.speech.subprocess.run',side_effect=[subprocess.CompletedProcess([],0),subprocess.CompletedProcess([],1)]) as process:
                with self.assertRaisesRegex(ValueError,'no fallback'): Speech(environ=env)
                self.assertEqual(process.call_count,2)
                self.assertEqual(process.call_args.args[0][-1],'conditioning_b_calm_low')

class AutomaticAssetsTests(unittest.TestCase):
    def test_all_profiles_sdk_native_json_and_neutral_exact(self):
        import hashlib, json
        from voice_app import automatic_conditioning as automatic
        from voice_app import supertonic3_worker as worker
        model = Path('/root/voice-lab/autonomous/expressive-tts')
        base = json.loads((model/'assets/voice_styles/M1.json').read_bytes())
        self.assertTrue(hasattr(automatic,'profile_path'), 'pinned profile resolver missing')
        hashes = []
        for identifier in automatic.PROFILE_IDS:
            path = worker.selected_profile_path(model/'assets', model/'assets-manifest.json', identifier)
            profile = json.loads(path.read_bytes())
            self.assertEqual(profile['style_dp'], base['style_dp'])
            self.assertEqual(profile['metadata'], base['metadata'])
            hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(set(hashes)), 7)
        self.assertEqual(hashes[0], '4f52698e957ae4d15d23c7208c26b298d56f70647083d61c9ff518c4d374d828')
        for bad in ('conditioning_b_active_extreme', '/root/profile.json', {}, 1):
            with self.assertRaises(worker.WorkerFailure): worker.selected_profile_path(model/'assets',model/'assets-manifest.json',bad)

    def test_relocated_isolated_checks_and_asset_failure_no_fallback(self):
        import shutil, subprocess, tempfile
        from voice_app.automatic_conditioning import PROFILE_IDS
        stage = Path(__file__).parent
        model = Path('/root/voice-lab/autonomous/expressive-tts')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'voice_app').mkdir()
            for file in ('automatic_conditioning.py','approved_conditioning.py','supertonic3_worker.py'):
                shutil.copy(stage/'voice_app'/file,root/'voice_app'/file)
            shutil.copytree(stage/'voice_assets',root/'voice_assets')
            args = [str(model/'venv/bin/python'),'-I',str(root/'voice_app/supertonic3_worker.py'),'check',str(model/'assets'),str(model/'assets-manifest.json')]
            for identifier in PROFILE_IDS:
                result = subprocess.run(args+[identifier], capture_output=True, timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(__import__('json').loads(result.stdout)['voice_style_profile'], identifier)
            target = root/'voice_assets/automatic-conditioning/conditioning_b_active_high.json'
            original = target.read_bytes()
            for mode in ('missing','corrupt','symlink'):
                target.unlink()
                if mode=='corrupt': target.write_bytes(original+b' ')
                if mode=='symlink': target.symlink_to(stage/'voice_assets/automatic-conditioning/conditioning_b_active_high.json')
                result = subprocess.run(args+['conditioning_b_active_high'], capture_output=True, timeout=15)
                self.assertEqual(result.returncode,1)
                self.assertEqual(result.stdout,b'')
                self.assertEqual(result.stderr,b'supertonic3_failure=asset_error\n')
                if target.exists() or target.is_symlink(): target.unlink()
                target.write_bytes(original)
            for target in (root/'voice_app/automatic_conditioning.py', root/'voice_assets/automatic-conditioning/manifest.json'):
                original = target.read_bytes(); target.write_bytes(original+b' ')
                result = subprocess.run(args+['conditioning_b_calm_low'], capture_output=True, timeout=15)
                self.assertEqual(result.stderr,b'supertonic3_failure=asset_error\n')
                target.write_bytes(original)
            shutil.rmtree(root/'voice_assets/automatic-conditioning')
            for identifier in ('original','conditioning_b'):
                result = subprocess.run(args+[identifier],capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)

if __name__ == '__main__': unittest.main()
