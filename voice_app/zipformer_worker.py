"""Experiment-owned explicit Indonesian Zipformer adapter. Not a Whisper fallback.

Transport currently re-decodes bounded complete growing PCM prefixes in a fresh
native online stream per request. It does NOT claim capture-continuous state.
The Stage1 helper separately benchmarks true time-paced native streaming.
No model downloads, credential inheritance, transcript logs or network routes.
"""
import os,sys,io,json,struct,wave,time,resource,ctypes,socket,hashlib
from pathlib import Path
# Fixed app-owned dependency root, never derived from installation or model input.
# Helpers and pinned provenance stay outside caller-selected model directories.
ROOT=Path('/root/voice-lab/autonomous/zipformer')
output_fd=os.dup(1)
code=1
try:
    with open(os.devnull,'wb') as sink:
        os.dup2(sink.fileno(),1);os.dup2(sink.fileno(),2)
    if len(sys.argv)!=3 or sys.argv[1] not in ('check','serve') or Path(sys.argv[2])!=ROOT/'model':
        raise ValueError('explicit pinned model path and action required')
    resource.setrlimit(resource.RLIMIT_AS,(1536*1024**2,1536*1024**2))
    resource.setrlimit(resource.RLIMIT_CPU,(180,185))
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.unshare(0x40000000)!=0:
        raise RuntimeError('isolated network namespace required; no fallback')
    for host in ('127.0.0.1','1.1.1.1'):
        try:
            with socket.create_connection((host,443),timeout=.1):
                raise ValueError('network unexpectedly reachable')
        except OSError:
            pass
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2')
    sys.path.insert(0,str(ROOT))
    from helpers import verify_artifact,load_recognizer,stream_pcm
    provenance=json.loads((ROOT/'artifact-verification.json').read_text())
    for artifact in provenance['artifacts']:
        if artifact['path'].startswith('model/'):
            verify_artifact((ROOT/artifact['path']).read_bytes(),artifact['upstream'])
    import sherpa_onnx
    if sys.argv[1]=='serve':
        recognizer=load_recognizer()
        with os.fdopen(os.dup(output_fd),'wb') as output:
            output.write(b'READY\n');output.flush()
            while True:
                header=sys.stdin.buffer.read(4)
                if not header:break
                if len(header)!=4:raise ValueError('truncated frame')
                size=struct.unpack('!I',header)[0]
                if not 44<size<=640044:raise ValueError('invalid frame size')
                payload=sys.stdin.buffer.read(size)
                if len(payload)!=size:raise ValueError('truncated WAV')
                with wave.open(io.BytesIO(payload)) as reader:
                    if (reader.getnchannels(),reader.getsampwidth(),reader.getframerate())!=(1,2,16000):
                        raise ValueError('mono PCM16 16000Hz required')
                    count=reader.getnframes();pcm=reader.readframes(count+1)
                    if not 0<count<=320000 or len(pcm)!=count*2:raise ValueError('invalid PCM')
                used=resource.getrusage(resource.RUSAGE_SELF)
                resource.setrlimit(resource.RLIMIT_CPU,(min(180,int(used.ru_utime+used.ru_stime)+86),185))
                begin=time.monotonic();decoded=stream_pcm(recognizer,pcm,paced=False)
                text=decoded['text']
                if not isinstance(text,str) or len(text)>4000:raise ValueError('invalid output')
                used=resource.getrusage(resource.RUSAGE_SELF)
                body=json.dumps({'text':text,'metrics':{'inference_seconds':time.monotonic()-begin,
                    'cpu_seconds':used.ru_utime+used.ru_stime,'maxrss_kib':used.ru_maxrss,
                    'reset_empty':decoded['reset_empty'],'network_namespace_isolated':True,
                    'prefix_redecode':True}},ensure_ascii=False).encode()
                if len(body)>65536:raise ValueError('output cap')
                output.write(struct.pack('!I',len(body))+body);output.flush()
    code=0
except Exception:
    code=1
finally:
    os.close(output_fd)
sys.exit(code)
