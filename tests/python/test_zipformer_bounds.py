"""Additional real worker malformed-input regressions; no mock recognizer."""
import io,struct,subprocess,unittest,wave
from tests.python.test_zipformer import PY,WORKER,ROOT
from voice_app.speech import _offline_env
class WorkerBoundaryTests(unittest.TestCase):
    def test_rejects_unknown_action_model_and_malformed_audio_without_text_logs(self):
        for args in (('unknown',str(ROOT/'model')),('check','/missing/model')):
            with self.subTest(args=args):
                p=subprocess.run((PY,'-I',str(WORKER),*args),capture_output=True,env=_offline_env(),timeout=15)
                self.assertNotEqual(p.returncode,0);self.assertEqual(p.stdout,b'');self.assertEqual(p.stderr,b'')
        bad_wav=io.BytesIO()
        with wave.open(bad_wav,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*800)
        raw=bad_wav.getvalue()
        for payload in (struct.pack('!I',640045),b'X',struct.pack('!I',100)+b'RIFF',struct.pack('!I',len(raw))+raw):
            with self.subTest(payload_bytes=len(payload)):
                p=subprocess.run((PY,'-I',str(WORKER),'serve',str(ROOT/'model')),input=payload,capture_output=True,env=_offline_env(),timeout=20)
                self.assertNotEqual(p.returncode,0);self.assertEqual(p.stdout,b'READY\n');self.assertEqual(p.stderr,b'')
if __name__=='__main__':unittest.main()
