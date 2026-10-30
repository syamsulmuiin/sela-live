"""Execute cloned Python control flow with synthetic arrays; no ONNX inference."""
import sys,json,inspect,hashlib
from pathlib import Path
sys.dont_write_bytecode=True;sys.path.insert(0,'/root/supertonic-live-fix')
import numpy as np
from supertonic.pipeline import TTS
from supertonic.core import Style,Supertonic
from supertonic.utils import chunk_text
from supertonic import config

class TextProcessor:
    def validate_text(self,text):return True,[]
    def __call__(self,texts,lang):return np.ones((len(texts),3),dtype=np.int64),np.ones((len(texts),1,3),dtype=np.float32)
class PipelineFixture:
    text_processor=TextProcessor()
    def __call__(self,texts,style,steps,speed,lang):return np.ones((1,100),dtype=np.float32),np.array([100/44100])
style=Style(np.zeros((1,1,1),dtype=np.float32),np.zeros((1,1,1),dtype=np.float32))
tts=TTS.__new__(TTS);tts.model=PipelineFixture();tts.is_multilingual=True;tts.model_name='supertonic-3';tts.sample_rate=44100
text='Kalimat pertama. Kalimat kedua.'
a,_=tts.synthesize(text,style,lang='id',max_chunk_length=18,silence_duration=.3)
b,_=tts.synthesize(text,style,lang='id',max_chunk_length=18,silence_duration=0)
count=len(chunk_text(text,18));assert count==2
assert a.shape[1]-b.shape[1]==13230
single_a,_=tts.synthesize('bangku depan warung, pucuk jambu.',style,lang='id',silence_duration=.3)
single_b,_=tts.synthesize('bangku depan warung, pucuk jambu.',style,lang='id',silence_duration=0)
assert np.array_equal(single_a,single_b)
invalid_steps=[]
for steps in (0,101):
    try:tts.synthesize('Teks.',style,total_steps=steps,lang='id')
    except ValueError:invalid_steps.append(steps)
assert invalid_steps==[0,101]
class Session:
    def __init__(self,role):self.role=role;self.calls=0
    def run(self,output,feed):
        self.calls+=1
        if self.role=='duration':return [np.array([2.1],dtype=np.float32)]
        if self.role=='encoder':return [np.zeros((1,1,3),dtype=np.float32)]
        if self.role=='vector':return [feed['noisy_latent']]
        return [np.zeros((1,feed['latent'].shape[-1]*20),dtype=np.float32)]
core=Supertonic.__new__(Supertonic);core.sample_rate=100;core.base_chunk_size=10;core.chunk_compress_factor=2;core.ldim=2;core.text_processor=TextProcessor()
core.dp_ort=Session('duration');core.text_enc_ort=Session('encoder');core.vector_est_ort=Session('vector');core.vocoder_ort=Session('vocoder')
np.random.seed(20261026);wave8,dur8=core(['Teks.'],style,total_step=8,speed=1.05,lang='id')
loops8=core.vector_est_ort.calls
np.random.seed(20261026);wave12,dur12=core(['Teks.'],style,total_step=12,speed=1.05,lang='id')
loops12=core.vector_est_ort.calls-loops8
assert loops8==8 and loops12==12 and np.array_equal(dur8,dur12) and wave8.shape==wave12.shape
bad_speeds=[]
for speed in (.6,2.1):
    try:core(['Teks.'],style,total_step=8,speed=speed,lang='id')
    except ValueError:bad_speeds.append(speed)
assert bad_speeds==[.6,2.1]
r={'scope':'cloned Python source behavior with explicitly synthetic ORT/session fixtures; no native model inference or listening claim','sdk_commit':'df0f9686dac7fbbde391b759e2ee5286a3737622','sdk_defaults':{'speed':config.DEFAULT_SPEED,'steps':config.DEFAULT_TOTAL_STEPS,'max_chunk_length':config.DEFAULT_MAX_CHUNK_LENGTH,'silence_duration':config.DEFAULT_SILENCE_DURATION,'max_text_length':config.MAX_TEXT_LENGTH},'silence_two_sdk_chunks':{'inserted_samples_at44100':a.shape[1]-b.shape[1],'seconds':.3},'silence_control_changes_single_sdk_chunk':not np.array_equal(single_a,single_b),'steps_loop_counts':[loops8,loops12],'same_predictor_duration_when_only_steps_change':np.array_equal(dur8,dur12),'predictor_duration_after_speed_division':dur8.tolist(),'invalid_steps_rejected':invalid_steps,'invalid_speeds_rejected':bad_speeds,'tts_signature':str(inspect.signature(TTS.synthesize)),'source_sha256':{name:hashlib.sha256((Path('/root/supertonic-live-fix/supertonic')/name).read_bytes()).hexdigest() for name in ('pipeline.py','core.py','config.py','utils.py')}}
p=Path('/root/sela-live/docs/NATIVE-CONTROL-FLOW-PROBE.json');assert not p.exists();p.write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
