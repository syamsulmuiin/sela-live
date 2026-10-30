"""Phase-one TTS contract; synthetic fixtures, never native inference."""
import unittest


class LiveTTSSettingsTests(unittest.TestCase):
    def test_only_bounded_quality_controls_are_enabled(self):
        try:
            from sela_live_tts.backend import NativeSettings
        except ImportError:
            self.fail('Sela Live TTS native settings are not implemented')
        self.assertEqual(NativeSettings().total_steps,8)
        for value in (8,10,12):self.assertEqual(NativeSettings(total_steps=value).total_steps,value)
        for value in (True,1,16,100,8.0,'8'):
            with self.subTest(value=value),self.assertRaises(ValueError):NativeSettings(total_steps=value)


class NativeStepOverrideTests(unittest.TestCase):
    def test_steps_only_forwarded_and_method_restored_after_success(self):
        try:
            from sela_live_tts.controls import native_step_override
        except ImportError:self.fail('Native quality override not implemented')
        class Engine:
            def synthesize(self,text,**kwargs):return text,kwargs
        engine=Engine()
        with native_step_override(engine,12):
            text,kwargs=engine.synthesize('teks tetap',total_steps=8,speed=1.05,silence_duration=.3)
        self.assertEqual(text,'teks tetap')
        self.assertEqual(kwargs,{'total_steps':12,'speed':1.05,'silence_duration':.3})
        self.assertNotIn('synthesize',vars(engine))

    def test_method_restored_after_native_error(self):
        try:
            from sela_live_tts.controls import native_step_override
        except ImportError:self.fail('Native quality override not implemented')
        class Engine:
            def synthesize(self,*args,**kwargs):raise RuntimeError('fixture native failure')
        engine=Engine()
        with self.assertRaises(RuntimeError):
            with native_step_override(engine,10):engine.synthesize('tetap',total_steps=8)
        self.assertNotIn('synthesize',vars(engine))


class SourcePinTests(unittest.TestCase):
    def test_changed_sdk_source_is_rejected_without_importing_it(self):
        try:
            from sela_live_tts.source_pins import verify_sources
        except ImportError:self.fail('Cloned SDK source verification not implemented')
        import tempfile,json,hashlib
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sdk=root/'sdk';sdk.mkdir();guard=root/'guard';guard.mkdir();live=root/'live';live.mkdir()
            (sdk/'core.py').write_text('SAFE=1\n');(guard/'worker.py').write_text('SAFE=1\n');(live/'controls.py').write_text('SAFE=1\n')
            digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            data={'sdk_root':str(sdk),'sdk_python_files':{'core.py':digest(sdk/'core.py')},'guard_root':str(guard),'guard_python_files':{'worker.py':digest(guard/'worker.py')},'live_root':str(live),'live_python_files':{'controls.py':digest(live/'controls.py')}}
            path=root/'pins.json';path.write_text(json.dumps(data));expected=digest(path)
            self.assertEqual(verify_sources(path,expected)['sdk_root'],str(sdk))
            (sdk/'core.py').write_text('CHANGED=1\n')
            with self.assertRaises(ValueError):verify_sources(path,expected)


class LiveTTSBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from sela_live_tts.admission import AdmissionCoordinator
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.coordinator=AdmissionCoordinator(Path(self.directory.name)/'evaluation.lock',production_probe=lambda:False)

    async def test_chunk_sources_and_native_quality_are_forwarded_without_rewriting(self):
        try:
            from sela_live_tts.backend import SelaLiveTTS,NativeSettings
        except ImportError:self.fail('Sela Live TTS adapter not implemented')
        import io,wave,json
        from voice_app.speech import normalize_pronunciation
        output=io.BytesIO()
        with wave.open(output,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes(b'\x01\x00'*100)
        calls=[]
        async def native(args,payload,limit,**kwargs):
            calls.append((args,json.loads(payload),kwargs));return output.getvalue()
        client=SelaLiveTTS(settings=NativeSettings(12),native_call=native,coordinator=self.coordinator)
        text=('Teks asli tentang API dan kebun, tetap utuh tanpa mengganti tanda baca. '*5)
        chunks=[part async for part in client.synthesize_chunks(text)]
        self.assertEqual(''.join(p[0] for p in chunks),text)
        self.assertEqual([p[2] for p in chunks],[False]*(len(chunks)-1)+[True])
        self.assertEqual([c[1]['text'] for c in calls],[normalize_pronunciation(p[0].strip()) for p in chunks])
        self.assertTrue(all(c[0][-1]=='12' and c[0][-2]=='conditioning_b' for c in calls))
        self.assertTrue(all('VOICE_API_KEY' not in c[2]['env'] for c in calls))

    async def test_cancellation_retains_slot_until_native_cleanup_finishes(self):
        try:
            from sela_live_tts.backend import SelaLiveTTS
        except ImportError:self.fail('Sela Live TTS adapter not implemented')
        import asyncio
        entered=asyncio.Event();cleaning=asyncio.Event();release=asyncio.Event()
        async def native(*args,**kwargs):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cleaning.set();await release.wait()
        client=SelaLiveTTS(native_call=native,coordinator=self.coordinator)
        task=asyncio.create_task(client.synthesize('Ucapan tetap.'))
        await entered.wait();task.cancel();await cleaning.wait();task.cancel();await asyncio.sleep(0)
        self.assertTrue(client._slot.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(client._slot.locked())


if __name__=='__main__':unittest.main()
