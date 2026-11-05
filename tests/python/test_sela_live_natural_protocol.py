"""Real planner/session/worker framing with pinned preprocessing, never ONNX inference."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path('/root/voice-lab/autonomous/expressive-tts/venv/bin/python')
UNICODE = Path('/root/voice-lab/autonomous/expressive-tts/assets/onnx/unicode_indexer.json')


class NaturalProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_framing_token_refusal_reaches_planner_and_completes_coverage(self):
        if not PYTHON.exists() or not UNICODE.exists():
            self.skipTest('Private pinned preprocessing runtime unavailable')
        from sela_live_tts.duration import DurationSession
        from sela_live_tts.natural import NaturalTTS, RuntimePins
        from sela_live_tts.utterance import SpeechRequest
        from sela_live_tts.source_pins import verify_sources
        manifest = ROOT/'sela_live_tts/upstream-manifest.json'
        from sela_live_tts.natural_worker import MANIFEST_SHA as manifest_sha
        sdk_root = verify_sources(manifest, manifest_sha)['sdk_root']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root/'duration_worker.py'
            worker.write_text(f'''import sys,time
sys.dont_write_bytecode=True
sys.path.insert(0,{str(ROOT)!r})
from sela_live_tts import duration_worker as w
sys.path.insert(0,{str(Path(sdk_root).parent)!r})
import numpy as np
from supertonic.core import UnicodeProcessor
class NoNativeDP:
    def run(self, outputs, inputs):
        if inputs['text_ids'].shape[-1]>512:
            raise RuntimeError('Oversized DP execution forbidden')
        return [np.array([2.1],dtype=np.float32)]
p=w.Predictor(UnicodeProcessor({str(UNICODE)!r}),NoNativeDP(),np.zeros((1,8,16),dtype=np.float32))
w.serve(p,sys.stdin.buffer,sys.stdout,time.monotonic()+20)
''')
            native_worker = root/'natural_worker.py'
            native_worker.write_text('# Never executed: fixture audio seam only.\n')
            (root/'upstream-manifest.json').write_bytes(manifest.read_bytes())
            (root/'assets.json').write_text('{}')
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            pins = RuntimePins(str(PYTHON),str(root),str(root/'assets.json'),str(native_worker),
                               str(worker),sha(native_worker),sha(worker),manifest_sha)
            class Admission:
                poll_interval=.005
                def claim(self):
                    from contextlib import nullcontext
                    return nullcontext()
                def check_production(self):pass
            observations=[]; sessions=[]; rendered=[]
            @asynccontextmanager
            async def factory(**kwargs):
                session=DurationSession(**kwargs);sessions.append(session)
                async with session:
                    original=session.predict
                    async def observe(normalized_text,*,profile_id=None):
                        result=await original(normalized_text,profile_id=profile_id)
                        observations.append(result)
                        return result
                    session.predict=observe
                    yield session
            async def audio(args,payload,*rest,**kwargs):
                self.assertTrue(all(s.returncode is not None for s in sessions))
                rendered.append(json.loads(payload)['text'])
                out=io.BytesIO()
                with wave.open(out,'wb') as w:
                    w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100)
                    w.writeframes(b'\x10\x00'*2000)
                return out.getvalue()
            engine=NaturalTTS(pins,duration_factory=factory,native_call=audio,coordinator=Admission())
            text='kata '*119+'fin.'
            result=[x async for x in engine.synthesize_chunks(SpeechRequest(text))]
            self.assertEqual(''.join(x[0] for x in result),text)
            self.assertIsNone(observations[0]['seconds'])
            self.assertEqual(observations[0]['tokens'],608)
            self.assertEqual(observations[0]['refusal'],'model_token_limit')
            self.assertGreater(len(rendered),1)
            plan=engine.last_plan
            if plan is None:self.fail('Completed speech has no plan')
            self.assertTrue(all(g.resource_envelope_verified for g in plan.groups))
            self.assertTrue(result[-1][2])
            self.assertTrue(all(s.process.returncode is not None and s._lease is None for s in sessions))


if __name__ == '__main__':
    unittest.main()
