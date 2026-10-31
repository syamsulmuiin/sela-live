"""Harmless WAV fixtures for the finite private comparison driver."""
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import wave

ROOT=Path(__file__).resolve().parents[2]


def wav():
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100)
        w.writeframes(b'\x10\x00'*1000)
    return out.getvalue()


class NaturalProbeTests(unittest.IsolatedAsyncioTestCase):
    def module(self):
        path=ROOT/'tools/tts_natural_same_text_probe.py'
        self.assertTrue(path.is_file(),'Natural comparison driver missing')
        spec=importlib.util.spec_from_file_location('_natural_probe',path)
        if spec is None or spec.loader is None:self.fail('Probe import unavailable')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    async def main_fixture(self, mode):
        import asyncio,json
        from unittest.mock import patch
        from contextlib import nullcontext
        from sela_live_tts.utterance import UtterancePlan,ResolvedDelivery
        module=self.module()
        review=ROOT/'docs/reviews/NATURAL-PRIVATE-PROBE-REPAIR-REVIEW.json'
        if not review.exists():self.skipTest('Private approved source inventory unavailable')
        # This is a harmless main-path fixture, not a fresh authorization of
        # the evolving runtime. Historical independent review stays immutable.
        # The separate gate test exercises the real rejection path.
        fixture_approval=json.loads(review.read_text())
        for row in fixture_approval['sha256_inventory']:
            path=Path(row['path'])
            # Rebind only the harmless in-memory fixture inventory when tests
            # run from a clean source export; real approvals stay immutable.
            if path.is_relative_to('/root/sela-live'):
                path=ROOT/path.relative_to('/root/sela-live')
                row['path']=str(path)
            row['sha256']=module.digest(path);row['bytes']=path.stat().st_size
        calls=[];cleaned=asyncio.Event();entered=asyncio.Event();deadlines=[]
        original_timeout=asyncio.timeout
        class Admission:
            poll_interval=.001
            def claim(self):return nullcontext()
            def check_production(self):pass
        class Candidate:
            def __init__(self,runtime,**kwargs):self.runtime=runtime;self.native=kwargs['native_call'];self.last_plan=None
            async def synthesize_chunks(self,request):
                self.last_plan=UtterancePlan(request.text,(),ResolvedDelivery(),0)
                parts=[request.text] if mode!='budget' else [request.text[i:i+70] for i in range(0,len(request.text),70)]
                for index,source in enumerate(parts):
                    args=(self.runtime.python,'-I',self.runtime.worker,'tts',self.runtime.assets,
                          self.runtime.asset_manifest,'conditioning_b','12')
                    audio=await self.native(args,json.dumps({'text':source}).encode(),2646044)
                    yield source,audio,index==len(parts)-1
        async def native(args,payload,limit,**kwargs):
            calls.append(args);entered.set()
            if mode in ('cancel','deadline'):
                try:await asyncio.sleep(30)
                finally:await asyncio.sleep(.03);cleaned.set()
            return wav()
        def timeout(seconds):
            deadlines.append(seconds)
            return original_timeout(.03 if seconds==900 and mode=='deadline' else seconds)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module,'validate_review',return_value=fixture_approval), \
                 patch('sela_live_tts.admission.AdmissionCoordinator',Admission), \
                 patch('sela_live_tts.natural.NaturalTTS',Candidate), \
                 patch('voice_app.speech._bounded_process',native), \
                 patch('tempfile.mkdtemp',return_value=directory), \
                 patch('asyncio.timeout',timeout):
                if mode=='cancel':
                    task=asyncio.create_task(module.main(review=review))
                    try:
                        async with original_timeout(3):
                            while not entered.is_set():
                                if task.done():await task
                                await asyncio.sleep(.005)
                        task.cancel();await asyncio.sleep(.005);task.cancel()
                        with self.assertRaises(asyncio.CancelledError):await task
                        self.assertTrue(cleaned.is_set())
                    finally:
                        if not task.done():task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                elif mode=='deadline':
                    with self.assertRaises(TimeoutError):await module.main(review=review)
                    self.assertTrue(cleaned.is_set())
                elif mode=='budget':
                    with self.assertRaisesRegex(ValueError,'native_call_budget'):await module.main(review=review)
                else:await module.main(review=review)
            receipt=json.loads((Path(directory)/'receipt.json').read_text())
            self.assertIn(900,deadlines)
            self.assertTrue(receipt['cleanup_complete'])
            self.assertTrue(receipt['production_app_unchanged'])
            self.assertTrue(all(args[-2:]==('conditioning_b','12') for args in calls))
            self.assertLessEqual(len(calls),24)
            self.assertEqual(receipt['native_calls'],len(calls))
            if mode=='budget':self.assertEqual(len(calls),24)
            expected='cancelled' if mode=='cancel' else ('complete' if mode=='complete' else 'failed')
            self.assertEqual(receipt['status'],expected)

    async def test_main_happy_fixture_has_terminal_receipt(self):await self.main_fixture('complete')
    async def test_main_refuses_native_call_25_without_retry(self):await self.main_fixture('budget')
    async def test_main_outer_deadline_reaps_before_terminal_receipt(self):await self.main_fixture('deadline')
    async def test_main_repeated_cancellation_finishes_owned_cleanup(self):await self.main_fixture('cancel')

    async def test_unapproved_or_incomplete_review_fails_before_inference(self):
        import json
        module=self.module()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'review.json'
            for data in ({},{'private_native_probe_authorized':False},
                         {'private_native_probe_authorized':True,'deployment_authorized':False,
                          'baseline_commit':'fa0f9b0f1f952e192cc9844ca73b3d453d08704c','sha256_inventory':[]}):
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):module.validate_review(path)
                with self.assertRaises(ValueError):await module.main(review=path)

    async def test_same_full_source_two_streams_write_verified_joined_wavs(self):
        module=self.module();closed=[]
        async def stream(parts):
            try:
                for i,part in enumerate(parts):yield part,wav(),i==len(parts)-1
            finally:closed.append(True)
        with tempfile.TemporaryDirectory() as directory:
            result=await module.collect_pair('Halo. Dunia!',stream(['Halo. ','Dunia!']),
                                             stream(['Halo. Dunia!']),Path(directory))
            self.assertEqual(len(result['baseline']['parts']),2)
            self.assertEqual(len(result['natural']['parts']),1)
            self.assertEqual(len(closed),2)
            for side in result.values():
                self.assertEqual(''.join(p['source'] for p in side['parts']),'Halo. Dunia!')
                with wave.open(side['wav'],'rb') as w:
                    self.assertEqual(w.getnframes(),sum(p['frames'] for p in side['parts']))
