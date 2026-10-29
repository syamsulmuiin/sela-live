import asyncio,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from voice_app.stream_asr import PersistentASR
from voice_app.speech import Speech

SCRIPT='''import sys,struct,json,time,resource
r=sys.stdin.buffer;w=sys.stdout.buffer
w.write(b'READY\\n');w.flush()
while h:=r.read(4):
 n=struct.unpack('!I',h)[0];r.read(n);time.sleep(.05)
 b=json.dumps({'text':sys.argv[1],'metrics':{'cpu_seconds':float(sys.argv[2])}}).encode()
 w.write(struct.pack('!I',len(b))+b);w.flush()
'''
class FastRouting(unittest.IsolatedAsyncioTestCase):
 async def test_malformed_metrics_reaped_and_next_request_restarts(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'w.py';p.write_text(SCRIPT)
   w=PersistentASR((sys.executable,str(p),'draft','nan'),{})
   try:
    with self.assertRaises(ValueError):await w.preview(b'a')
    self.assertIsNone(w.pid)
    w.args=(sys.executable,str(p),'draft','0')
    self.assertEqual(await w.preview(b'a'),'draft')
   finally:await w.close()

 async def test_idle_settings_reject_unbounded_or_invalid(self):
  for value in (0,-1,float('inf'),float('nan'),True,'60',601):
   with self.assertRaises(ValueError):PersistentASR(('/fixture',),{},idle_seconds=value)

 async def test_cumulative_cpu_recycles_before_next_request(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'w.py';p.write_text(SCRIPT)
   w=PersistentASR((sys.executable,str(p),'draft','75'),{})
   try:
    await w.ready();pid=w.pid
    self.assertEqual(await w.preview(b'a'),'draft');self.assertIsNone(w.pid)
    with self.assertRaises(ProcessLookupError):__import__('os').kill(pid,0)
    self.assertEqual(await w.preview(b'b'),'draft');self.assertEqual(w.inference_count,2)
   finally:await w.close()

 async def test_shutdown_drains_native_work_blocks_new_preview_and_restarts(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'w.py';p.write_text(SCRIPT)
   w=PersistentASR((sys.executable,str(p),'draft','0'),{})
   await w.ready();pid=w.pid
   task=asyncio.create_task(w.preview(b'a'));await asyncio.sleep(.01)
   closing=asyncio.create_task(w.close());await asyncio.sleep(.005)
   self.assertTrue(w._closing)
   self.assertIsNone(await w.preview(b'b'))
   await task
   await closing;self.assertIsNone(w.pid)
   with self.assertRaises(ProcessLookupError):__import__('os').kill(pid,0)
   self.assertEqual(await w.preview(b'c'),'draft');await w.close()

 async def test_final_priority_survives_cancelled_draft_consumer(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'w.py';p.write_text(SCRIPT)
   w=PersistentASR((sys.executable,str(p),'draft','0'),{},final_args=(sys.executable,str(p),'final','0'))
   try:
    await w.ready();t=asyncio.create_task(w.preview(b'a'));await asyncio.sleep(.01);t.cancel()
    with self.assertRaises(asyncio.CancelledError):await t
    final=asyncio.create_task(w.transcribe(b'b'));await asyncio.sleep(.005)
    self.assertIsNone(await w.preview(b'c'))
    self.assertEqual(await final,'final');self.assertEqual(w.inference_count,2)
   finally:await w.close()

 async def test_idle_reaps_worker_and_next_preview_restarts(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'w.py';p.write_text(SCRIPT)
   w=PersistentASR((sys.executable,str(p),'draft','0'),{},idle_seconds=.1)
   try:
    await w.ready();pid=w.pid
    await asyncio.sleep(.2);self.assertIsNone(w.pid)
    with self.assertRaises(ProcessLookupError):__import__('os').kill(pid,0)
    self.assertEqual(await w.preview(b'a'),'draft');self.assertNotEqual(w.pid,pid)
   finally:await w.close()

 async def test_fast_draft_final_switch_evicts_previous_process(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'w.py';p.write_text(SCRIPT)
   draft=(sys.executable,str(p),'draft','0');final=(sys.executable,str(p),'final','0')
   w=PersistentASR(draft,{},final_args=final)
   try:
    await w.ready();pid=w.pid
    self.assertEqual(await w.preview(b'a'),'draft')
    self.assertEqual(await w.transcribe(b'b'),'final');self.assertNotEqual(w.pid,pid)
    with self.assertRaises(ProcessLookupError):__import__('os').kill(pid,0)
    self.assertEqual(await w.preview(b'c'),'draft')
   finally:await w.close()

class DraftDecode(unittest.TestCase):
 def test_worker_reports_cpu_and_enforces_each_request_budget(self):
  import struct,resource
  from voice_app import local_audio_worker as w
  from voice_app.audio import pcm16_wav
  wav=pcm16_wav(b'\x01\x01'*16000,16000);out=io.BytesIO()
  with patch.object(w,'load_stt_model',return_value=object()),patch.object(w,'transcribe',return_value={'text':'fixture'}),patch.object(resource,'setrlimit') as limit:
   w.serve_stt('/fixture',io.BytesIO(struct.pack('!I',len(wav))+wav),out,enforce_limits=True)
  result=json.loads(out.getvalue()[10:])
  self.assertGreaterEqual(result['metrics']['cpu_seconds'],0)
  self.assertEqual(limit.call_args.args[0],resource.RLIMIT_CPU)
  self.assertLessEqual(limit.call_args.args[1][0],180)
  self.assertEqual(limit.call_args.args[1][1],185)

 def test_explicit_draft_config_and_final_identity(self):
  with patch('voice_app.speech._runtime_ready',return_value=True):
   s=Speech(environ={'VOICE_LOCAL_PREVIEW':'1','VOICE_DRAFT_MODEL_PATH':'/draft','VOICE_WHISPER_MODEL_PATH':'/final'})
   w=s._preview_worker()
   self.assertEqual(w.args[-2:],('serve-draft','/draft'))
   self.assertEqual(w.final_args[-2:],('serve-stt','/final'))
   for config in ({'VOICE_DRAFT_MODEL_PATH':'relative'},{'VOICE_DRAFT_MODEL_PATH':'/draft'},{'VOICE_LOCAL_PREVIEW':'1','VOICE_DRAFT_MODEL_PATH':''}):
    with self.assertRaises(ValueError):Speech(environ=config)
  with patch('voice_app.speech._runtime_ready',side_effect=lambda p,m,a:str(m)!='/draft'):
   with self.assertRaises(ValueError):Speech(environ={'VOICE_LOCAL_PREVIEW':'1','VOICE_DRAFT_MODEL_PATH':'/draft'})

 def test_draft_beam_one_final_settings_identical(self):
  import types
  from voice_app import local_audio_worker as w
  from voice_app.audio import pcm16_wav
  calls=[]
  class Model:
   def transcribe(self,*a,**kw):
    calls.append(kw);return iter([types.SimpleNamespace(text='draft',no_speech_prob=.1,avg_logprob=-.1)]),None
  with patch.object(w,'validate_model',return_value=Path('/fixture')):
   wav=pcm16_wav(b'\x01\x01'*16000,16000)
   w.transcribe(wav,'/fixture',model=Model(),draft=True)
   w.transcribe(wav,'/fixture',model=Model())
  self.assertEqual(calls[0].pop('beam_size'),1)
  self.assertEqual(calls[1].pop('beam_size'),5)
  self.assertEqual(calls[0],calls[1]);self.assertNotIn('initial_prompt',calls[0])
