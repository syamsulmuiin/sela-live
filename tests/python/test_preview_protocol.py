"""Deterministic transport fixtures, separate from real worker/browser gate."""
import asyncio, base64, tempfile, unittest
from fastapi.testclient import TestClient
from voice_app.server import create_app, validate
from tests.python.test_server import Provider, Speech

class DraftSpeech(Speech):
    stt_available=True
    stt_mode='local'
    local_preview=True
    browser_stt_allowed=True
    def __init__(self): self.previews=0;self.finals=0
    async def prepare_preview(self): pass
    async def close_preview(self): pass
    async def preview(self,wav):
        self.previews+=1
        return 'draft' if self.previews==1 else 'revised draft'
    async def transcribe(self,wav):
        self.finals+=1
        return 'final utterance'

class CountProvider(Provider):
    def __init__(self): self.calls=0
    async def stream(self,messages):
        self.calls+=1
        assert messages[-1]['content']=='final utterance'
        yield 'reply'

class PreviewProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.speech=DraftSpeech();self.provider=CountProvider()
    def app(self):
        return create_app(provider=self.provider,speech=self.speech,data_dir=self.directory.name,local_test_auth=True,public_origin='')
    def connect(self,c): return c.websocket_connect('/ws',headers={'host':'localhost','origin':'http://localhost'})
    def until(self,ws,kind):
        for _ in range(30):
            e=ws.receive_json()
            self.assertNotEqual(e['type'],'error',e)
            if e['type']==kind: return e
        self.fail('missing '+kind)
    def preview(self,seconds=1.5,id=1):
        return {'type':'audio_preview','utterance_id':id,'sample_rate':16000,
                'pcm_b64':base64.b64encode(b'\x01\x01'*int(16000*seconds)).decode()}
    def final(self,id=1):
        return {**self.preview(3,id),'type':'audio'}
    def test_provisional_revised_final_enters_history_once_and_acks(self):
        with TestClient(self.app()) as c,self.connect(c) as ws:
            cfg=c.get('/api/config',headers={'host':'localhost'}).json()
            self.assertTrue(cfg.get('local_preview_supported'))
            ws.receive_json();ws.send_json({'type':'start'});self.until(ws,'state')
            ws.send_json(self.preview()); e=self.until(ws,'transcript_partial')
            self.assertEqual(e['text'],'draft');self.assertTrue(e['provisional'])
            self.assertEqual(self.provider.calls,0)
            # Advancing admission clock is deterministic, not sleeping in tests.
            from unittest.mock import patch
            with patch('voice_app.server.PREVIEW_MIN_INTERVAL',0):
                ws.send_json(self.preview(3));e=self.until(ws,'transcript_partial')
            self.assertEqual(e['text'],'revised draft')
            ws.send_json(self.final());self.until(ws,'transcript')
            audio=self.until(ws,'audio');self.until(ws,'response_done');state=self.until(ws,'state')['state']
            self.assertEqual(len(state['history']),2)
            self.assertEqual(state['history'][0]['text'],'final utterance')
            ws.send_json({'type':'ack','token':audio['token'],'chunk_id':audio['chunk_id']})
            self.assertEqual(self.until(ws,'state')['state']['history'][-1]['heard_text'],'reply')
            ws.send_json(self.final());self.until(ws,'state')
            ws.send_json(self.preview());self.until(ws,'state')
            self.assertEqual(self.provider.calls,1);self.assertEqual(self.speech.finals,1)
    def test_prepare_ack_is_correlated_and_stop_during_load_discards_it(self):
        import threading
        entered=threading.Event();release=threading.Event()
        async def prepare():
            entered.set()
            while not release.is_set(): await asyncio.sleep(.005)
        self.speech.prepare_preview=prepare
        with TestClient(self.app()) as c,self.connect(c) as ws:
            ws.receive_json()
            ws.send_json({'type':'start','start_id':'load1','local_preview':True})
            self.until(ws,'state');self.assertTrue(entered.wait(2))
            ws.send_json({'type':'stop'});self.assertFalse(self.until(ws,'state')['state']['open'])
            release.set()
            ws.send_json({'type':'start','start_id':'fresh'})
            self.assertEqual(self.until(ws,'session_ready')['start_id'],'fresh')

    def test_running_and_pending_preview_suppressed_at_lifecycle_boundaries(self):
        import threading
        from unittest.mock import patch
        for boundary in ('mute','stop','interrupt','start','disconnect','final'):
            with self.subTest(boundary=boundary):
                entered=threading.Event();release=threading.Event();completed=threading.Event()
                self.speech=DraftSpeech();self.provider=CountProvider()
                async def preview(wav):
                    self.speech.previews+=1;entered.set()
                    while not release.is_set(): await asyncio.sleep(.002)
                    completed.set();return 'stale private draft'
                self.speech.preview=preview
                with TestClient(self.app()) as c:
                    with self.connect(c) as ws:
                        ws.receive_json();ws.send_json({'type':'start'});self.until(ws,'state')
                        ws.send_json(self.preview());self.until(ws,'state');self.assertTrue(entered.wait(2))
                        with patch('voice_app.server.PREVIEW_MIN_INTERVAL',0):
                            for seconds in (2,2.5,3,3.5,4):
                                ws.send_json(self.preview(seconds));self.until(ws,'state')
                        if boundary=='final':
                            ws.send_json(self.final());self.until(ws,'response_done');self.until(ws,'state')
                        elif boundary!='disconnect':
                            cmd={'type':boundary}
                            if boundary=='mute':cmd['muted']=True
                            ws.send_json(cmd)
                            if boundary=='interrupt':self.until(ws,'interrupted')
                            self.until(ws,'state')
                        if boundary=='disconnect':ws.close()
                        release.set();self.assertTrue(completed.wait(2))
                        if boundary!='disconnect':
                            ws.send_json({'type':'provider','provider':'default'})
                            self.assertEqual(ws.receive_json()['type'],'state','no late partial before barrier')
                    self.assertEqual(self.speech.previews,1,'pending revisions must coalesce and expire')
                    self.assertEqual(self.provider.calls,1 if boundary=='final' else 0)

    def test_disabled_closed_muted_and_playback_reject_preview(self):
        with TestClient(self.app()) as c,self.connect(c) as ws:
            ws.receive_json();ws.send_json(self.preview())
            self.assertEqual(ws.receive_json()['code'],'session_not_ready')
            ws.send_json({'type':'start'});self.until(ws,'state')
            ws.send_json({'type':'mute','muted':True});self.until(ws,'state')
            ws.send_json(self.preview());self.assertEqual(ws.receive_json()['type'],'error')
            ws.send_json({'type':'mute','muted':False});self.until(ws,'state')
            ws.send_json(self.final());self.until(ws,'response_done');self.until(ws,'state')
            ws.send_json(self.preview(3,id=2));self.assertEqual(ws.receive_json()['type'],'error')
            self.assertEqual(self.speech.previews,0)

    def test_muted_generation_cannot_be_resubmitted_after_unmute(self):
        with TestClient(self.app()) as c,self.connect(c) as ws:
            ws.receive_json();ws.send_json({'type':'start'});self.until(ws,'state')
            ws.send_json(self.preview());self.until(ws,'transcript_partial')
            for muted in (True,False):
                ws.send_json({'type':'mute','muted':muted});self.until(ws,'state')
            ws.send_json(self.final());self.until(ws,'state')
            ws.send_json({'type':'provider','provider':'default'})
            state=self.until(ws,'state')['state']
            self.assertEqual(state['history'],[])
            self.assertEqual(self.provider.calls,0)

    def test_preview_size_schema_bounds_before_decode(self):
        for body in (self.preview(.1),self.preview(20.01),{**self.preview(),'utterance_id':True},
                     {**self.preview(),'utterance_id':0},{**self.preview(),'sample_rate':48000}):
            with self.subTest(keys=tuple(body)):
                with self.assertRaises(ValueError): validate(body)
        validate(self.preview())
