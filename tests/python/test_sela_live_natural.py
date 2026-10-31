"""Unified natural request/render contract; fixtures, never native inference."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import hashlib
import io
import json
import sys
import unittest
import wave


def fixture_wav():
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100)
        w.writeframes(b'\x10\x00'*2000)
    return out.getvalue()


def runtime_pins(root):
    from sela_live_tts.natural import RuntimePins
    for name in ('natural_worker.py','duration_worker.py','upstream-manifest.json','assets.json'):
        (root/name).write_text('{}' if name.endswith('.json') else '# harmless fixture, never executed\n')
    return RuntimePins(python=sys.executable,assets=str(root),asset_manifest=str(root/'assets.json'),
        worker=str(root/'natural_worker.py'),duration_worker=str(root/'duration_worker.py'),
        worker_sha256=hashlib.sha256((root/'natural_worker.py').read_bytes()).hexdigest(),
        duration_worker_sha256=hashlib.sha256((root/'duration_worker.py').read_bytes()).hexdigest(),
        source_manifest_sha256=hashlib.sha256((root/'upstream-manifest.json').read_bytes()).hexdigest())


class NaturalTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_immutable_request_drives_plan_and_native_context(self):
        try:
            from sela_live_tts.natural import NaturalTTS, RuntimePins
        except ImportError:
            self.fail('Unified natural TTS renderer is missing')
        from sela_live_tts.utterance import SpeechRequest
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('natural_worker.py','duration_worker.py','upstream-manifest.json','assets.json'):
                (root/name).write_text('{}' if name.endswith('.json') else '# harmless fixture, never executed\n')
            pins=RuntimePins(python=sys.executable,assets=str(root),asset_manifest=str(root/'assets.json'),
                worker=str(root/'natural_worker.py'),duration_worker=str(root/'duration_worker.py'),
                worker_sha256=hashlib.sha256((root/'natural_worker.py').read_bytes()).hexdigest(),
                duration_worker_sha256=hashlib.sha256((root/'duration_worker.py').read_bytes()).hexdigest(),
                source_manifest_sha256=hashlib.sha256((root/'upstream-manifest.json').read_bytes()).hexdigest())
            state={'duration_active':False,'predictions':[],'renders':[],'closed':False}
            @asynccontextmanager
            async def duration_factory(**kwargs):
                state['duration_active']=True
                class Predictor:
                    async def predict(self,text,*,profile_id=None):
                        state['predictions'].append((text,profile_id))
                        return {'seconds':8.,'tokens':len(text)+9}
                try:yield Predictor()
                finally:state.update(duration_active=False,closed=True)
            async def native(args,payload,limit,**kwargs):
                self.assertFalse(state['duration_active'],'DP must be reaped before full audio inference')
                self.assertTrue(state['closed'])
                state['renders'].append((args,json.loads(payload)))
                return fixture_wav()
            class Admission:
                poll_interval=.001
                def claim(self):
                    from contextlib import nullcontext
                    return nullcontext()
                def check_production(self):pass
            engine=NaturalTTS(pins,duration_factory=duration_factory,native_call=native,coordinator=Admission())
            text='Arga berjalan pulang, lalu ia mendengar piano. '
            result=[item async for item in engine.synthesize_chunks(SpeechRequest(text))]
            self.assertEqual(''.join(part for part,audio,last in result),text)
            self.assertEqual(len(result),1)
            self.assertTrue(result[-1][2])
            self.assertEqual(state['renders'][0][0][2],str(root/'natural_worker.py'))
            self.assertEqual(state['renders'][0][0][-1],'12')
            self.assertEqual(state['renders'][0][1],{'text':text.strip()})
            self.assertEqual(engine.identity['policy'],'natural_v1')
            self.assertFalse(engine.identity['independent_emotion_rendering_proven'])
            self.assertEqual(engine.last_plan.source_text,text)

    async def test_delivery_profile_is_bound_before_duration_session_starts(self):
        from sela_live_tts.natural import NaturalTTS
        from sela_live_tts.utterance import SpeechRequest
        from voice_app.delivery import load_registry
        with tempfile.TemporaryDirectory() as directory:
            pins=runtime_pins(Path(directory));profiles=[];renders=[]
            @asynccontextmanager
            async def factory(**kwargs):
                profiles.append(kwargs.get('profile_id', 'conditioning_b'))
                class Predictor:
                    async def predict(self,text,*,profile_id=None):
                        if profile_id != profiles[-1]:
                            raise ValueError('Duration profile mismatch')
                        return {'seconds':2.,'tokens':20}
                yield Predictor()
            async def native(args,*rest,**kwargs):renders.append(args[-2]);return fixture_wav()
            class Admission:
                poll_interval=.001
                def claim(self):
                    from contextlib import nullcontext
                    return nullcontext()
                def check_production(self):pass
            engine=NaturalTTS(pins,duration_factory=factory,native_call=native,coordinator=Admission())
            selected=load_registry().resolve('anger','high')
            result=[x async for x in engine.synthesize_chunks(SpeechRequest('Batas ini tegas.', selected))]
            self.assertEqual(profiles,['conditioning_b_active_high'])
            self.assertEqual(renders,profiles)
            self.assertEqual(len(result),1)

    async def test_whitespace_only_spans_attach_to_spoken_ack_not_native_requests(self):
        from sela_live_tts.natural import NaturalTTS
        from sela_live_tts.utterance import SpeechRequest
        with tempfile.TemporaryDirectory() as directory:
            pins=runtime_pins(Path(directory));payloads=[]
            @asynccontextmanager
            async def factory(**kwargs):
                class Predictor:
                    async def predict(self,text,*,profile_id=None):return {'seconds':2.,'tokens':20}
                yield Predictor()
            async def native(args,payload,*rest,**kwargs):
                value=json.loads(payload)['text']
                if not value.strip():raise ValueError('Empty native request')
                payloads.append(value);return fixture_wav()
            class Admission:
                poll_interval=.001
                def claim(self):
                    from contextlib import nullcontext
                    return nullcontext()
                def check_production(self):pass
            engine=NaturalTTS(pins,duration_factory=factory,native_call=native,coordinator=Admission())
            text=' '*1200+'Halo.'+'\t'*1000
            result=[x async for x in engine.synthesize_chunks(SpeechRequest(text))]
            self.assertEqual(''.join(x[0] for x in result),text)
            self.assertEqual(payloads,['Halo.'])
            self.assertEqual(len(result),1)
            self.assertTrue(result[-1][2])

    async def test_worker_drift_after_planning_rejects_before_audio_spawn(self):
        from sela_live_tts.natural import NaturalTTS
        from sela_live_tts.utterance import SpeechRequest
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pins=runtime_pins(root);renders=[]
            @asynccontextmanager
            async def factory(**kwargs):
                class Predictor:
                    async def predict(self,text,*,profile_id=None):return {'seconds':2.,'tokens':20}
                try:yield Predictor()
                finally:(root/'natural_worker.py').write_text('# drifted after planning\n')
            async def native(*args,**kwargs):renders.append(1);return fixture_wav()
            class Admission:
                poll_interval=.001
                def claim(self):
                    from contextlib import nullcontext
                    return nullcontext()
                def check_production(self):pass
            engine=NaturalTTS(pins,duration_factory=factory,native_call=native,coordinator=Admission())
            with self.assertRaises(ValueError):
                await anext(engine.synthesize_chunks(SpeechRequest('Suara asli.')))
            self.assertEqual(renders,[])

    async def test_second_request_is_rejected_before_duration_admission(self):
        from sela_live_tts.natural import NaturalTTS
        from sela_live_tts.utterance import SpeechRequest
        from sela_live_tts.admission import LiveTTSBusy
        with tempfile.TemporaryDirectory() as directory:
            pins=runtime_pins(Path(directory));entered=asyncio.Event();release=asyncio.Event();starts=[]
            @asynccontextmanager
            async def factory(**kwargs):
                starts.append(1);entered.set();await release.wait()
                class Predictor:
                    async def predict(self,text,*,profile_id=None):return {'seconds':2.,'tokens':20}
                yield Predictor()
            async def native(*args,**kwargs):return fixture_wav()
            class Admission:
                poll_interval=.001
                def claim(self):
                    from contextlib import nullcontext
                    return nullcontext()
                def check_production(self):pass
            engine=NaturalTTS(pins,duration_factory=factory,native_call=native,coordinator=Admission())
            a=engine.synthesize_chunks(SpeechRequest('Pertama.'));b=engine.synthesize_chunks(SpeechRequest('Kedua.'))
            first=asyncio.create_task(anext(a));second=None
            try:
                await entered.wait();second=asyncio.create_task(anext(b))
                await asyncio.sleep(0);await asyncio.sleep(0)
                self.assertTrue(second.done(),'Second request must fail before another DP session starts')
                with self.assertRaises(LiveTTSBusy):await second
                self.assertEqual(len(starts),1)
            finally:
                release.set()
                for task in (first,second):
                    if task is not None and not task.done():task.cancel()
                await asyncio.gather(*[x for x in (first,second) if x is not None],return_exceptions=True)
                await a.aclose();await b.aclose()
