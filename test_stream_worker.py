import asyncio, io, json, struct, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from voice_app.audio import pcm16_wav

class PersistentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_one_process_and_holds_capacity_after_cancel(self):
        from voice_app.stream_asr import PersistentASR
        # Real subprocess fixture tests framing/lifetime, not real ASR quality.
        script = '''import sys,struct,json,time
r=sys.stdin.buffer; w=sys.stdout.buffer
w.write(b'READY\\n');w.flush()
while True:
 h=r.read(4)
 if not h: break
 n=struct.unpack('!I',h)[0]; p=r.read(n)
 time.sleep(.12)
 b=json.dumps({'text':'fixture'}).encode();w.write(struct.pack('!I',len(b))+b);w.flush()
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.py';path.write_text(script)
            worker=PersistentASR((sys.executable,'-I',str(path)),{})
            await worker.ready()
            pid=worker.pid
            first=asyncio.create_task(worker.transcribe(b'a'))
            await asyncio.sleep(.03)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError): await first
            self.assertTrue(worker.busy)
            self.assertIsNone(await worker.preview(b'b'))
            self.assertEqual(await worker.transcribe(b'c'),'fixture')
            self.assertEqual(worker.pid,pid)
            self.assertEqual(worker.inference_count,2)
            await worker.close()
            self.assertIsNone(worker.pid)

    async def test_request_and_response_caps(self):
        from voice_app.stream_asr import PersistentASR
        worker=PersistentASR(('/missing',),{})
        with self.assertRaises(ValueError): await worker.transcribe(b'x'*640045)
        with self.assertRaises(ValueError): await worker.transcribe(b'')
        with self.assertRaises(ValueError): await worker.preview(b'x'*640045)

class ModelReuseTests(unittest.TestCase):
    def test_framed_worker_multiple_requests_and_oversize_header(self):
        from voice_app import local_audio_worker as w
        import types
        payload=pcm16_wav(b'\x01\x01'*160,16000)
        output=io.BytesIO()
        with patch.object(w,'load_stt_model',return_value=object()), patch.object(w,'transcribe',return_value={'text':'fixture'}) as decode:
            w.serve_stt('/fixture', io.BytesIO((struct.pack('!I',len(payload))+payload)*2),output)
        self.assertEqual(decode.call_count,2)
        self.assertTrue(output.getvalue().startswith(b'READY\n'))
        with patch.object(w,'load_stt_model',return_value=object()):
            with self.assertRaises(ValueError):
                w.serve_stt('/fixture',io.BytesIO(struct.pack('!I',640045)),io.BytesIO())

    def test_repeated_prefix_and_final_share_loaded_model_without_changing_decoding(self):
        import types
        from voice_app import local_audio_worker as w
        calls=[]; loads=[]
        class Model:
            def __init__(self,*args,**kwargs): loads.append(kwargs)
            def transcribe(self,*args,**kwargs):
                calls.append(kwargs)
                return iter([types.SimpleNamespace(text='fixture',no_speech_prob=.1,avg_logprob=-.1)]),None
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            for name in ('model.bin','config.json','tokenizer.json'): (path/name).write_text('{}')
            with patch.dict(sys.modules,{'faster_whisper':types.SimpleNamespace(WhisperModel=Model)}):
                model=w.load_stt_model(path)
                for _ in range(2):
                    self.assertEqual(w.transcribe(pcm16_wav(b'\x01\x01'*16000,16000),path,model=model),{'text':'fixture'})
        self.assertEqual(len(loads),1)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(calls[0]['beam_size'],5)
        self.assertNotIn('initial_prompt',calls[0])
