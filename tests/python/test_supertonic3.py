import importlib.util
import pathlib
import unittest

ROOT=pathlib.Path('/root/voice-lab/autonomous/expressive-tts')

class RuntimeValidation(unittest.TestCase):
    def test_pinned_local_runtime_and_identity(self):
        path=pathlib.Path(__file__).resolve().parents[2]/'voice_app/supertonic3_worker.py'
        self.assertTrue(path.is_file(), 'explicit local Supertonic3 worker missing')
        spec=importlib.util.spec_from_file_location('st3',path)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        result=m.check_assets(ROOT/'assets', ROOT/'assets-manifest.json')
        self.assertEqual(result, {'backend':'supertonic3','voice':'M1','language':'id','sample_rate':44100,'channels':1,'sample_width':2})
        with self.assertRaises(ValueError):m.check_assets(pathlib.Path('assets'), ROOT/'assets-manifest.json')
        with self.assertRaises((ValueError,FileNotFoundError)):m.check_assets(ROOT/'missing',ROOT/'assets-manifest.json')

class InputValidation(unittest.TestCase):
    def test_bounded_speech_only_documented_tags(self):
        from voice_app import supertonic3_worker as m
        self.assertTrue(hasattr(m,'validate_request'), 'bounded request validator missing')
        text='Baik, saya bantu. <laugh> Mau mulai dari mana?'
        request={'text':text}
        self.assertEqual(m.validate_request(request),text)
        self.assertEqual(request['text'],text)
        for bad in ['', 'x'*801, '<happy> Halo', '<script>alert(1)</script>', '<laugh', 'Halo\x00', 123]:
            with self.subTest(bad=str(bad)[:20]),self.assertRaises(ValueError):m.validate_request({'text':bad})
        for key,value in [('voice','F1'),('lang','en'),('speed',2),('emotion','sad')]:
            with self.assertRaises(ValueError):m.validate_request({'text':'Halo',key:value})

class RealSynthesis(unittest.TestCase):
    @unittest.skipUnless(__import__('os').environ.get('SUPERTONIC_REAL')=='1','opt-in heavy synthesis')
    def test_actual_local_m1_indonesian_wav(self):
        from voice_app import supertonic3_worker as m
        self.assertTrue(hasattr(m,'Runtime'),'real runtime missing')
        runtime=m.Runtime(ROOT/'assets',ROOT/'assets-manifest.json')
        data,metrics=runtime.synthesize({'text':'Baik, saya siap membantu.'})
        import io,wave
        with wave.open(io.BytesIO(data),'rb') as w:
            self.assertEqual((w.getframerate(),w.getnchannels(),w.getsampwidth()),(44100,1,2))
            self.assertGreater(w.getnframes(),4410)
        self.assertGreater(metrics['inference_seconds'],0)
        self.assertLess(metrics['peak_rss_kib'],700*1024)

class WorkerCLI(unittest.TestCase):
    def test_check_and_bounded_invalid_input_without_model_allocation(self):
        import subprocess
        worker=pathlib.Path(__file__).resolve().parents[2]/'voice_app/supertonic3_worker.py'
        args=[str(ROOT/'venv/bin/python'),'-I',str(worker)]
        check=subprocess.run(args+['check',str(ROOT/'assets'),str(ROOT/'assets-manifest.json')],capture_output=True,timeout=15)
        self.assertEqual(check.returncode,0,check.stderr.decode())
        self.assertIn(b'44100',check.stdout)
        self.assertTrue(__import__('json').loads(check.stdout)['native_network_denied'])
        for bad in [b'{"text":"<happy> hi"}',b' '*4097,b'{}']:
            r=subprocess.run(args+['tts',str(ROOT/'assets'),str(ROOT/'assets-manifest.json')],input=bad,capture_output=True,timeout=15)
            self.assertNotEqual(r.returncode,0)
            self.assertEqual(r.stdout,b'')
            self.assertLess(len(r.stderr),100)

if __name__=='__main__':unittest.main()
