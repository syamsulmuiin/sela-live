"""Private staged Supertonic3 evaluation; no downloads or provider fallback."""
import hashlib
import json
from pathlib import Path

import re

# Fixed protocol vocabulary only. Never send native exception text to the parent.
SAFE_FAILURE_REASONS = frozenset({
    'memory_headroom', 'memory_allocation', 'resource_limit', 'invalid_text',
    'invalid_audio', 'duration_limit', 'clipping', 'runtime_version',
    'asset_error', 'network_sandbox', 'output_limit', 'native_error', 'empty_output', 'invalid_request',
})
FAILURE_PREFIX = b'supertonic3_failure='


class WorkerFailure(RuntimeError):
    """Explicit worker guard, not an inference exception with arbitrary text."""


class AssetFailure(ValueError):
    """Asset validation failed, without exposing paths or parser messages."""


def failure_reason(exc):
    if type(exc) is AssetFailure:
        return 'asset_error'
    if type(exc) is WorkerFailure and len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in SAFE_FAILURE_REASONS:
        return exc.args[0]
    if isinstance(exc, MemoryError):
        return 'memory_allocation'
    if isinstance(exc, (json.JSONDecodeError, UnicodeError)):
        return 'invalid_text'
    if type(exc) is ValueError and len(exc.args) == 1:
        code = exc.args[0]
        if type(code) is not str:
            return 'native_error'
        if code in {'request_fields', 'delivery_controls', 'text_limit', 'text_control', 'unsupported_tag', 'tag_limit', 'input_limit'}:
            return 'invalid_text'
        if code in {'invalid_audio', 'empty_audio'}:
            return 'invalid_audio'
        if code in {'duration_limit', 'clipping', 'runtime_version', 'output_limit'}:
            return code
        if code in {'absolute_assets_required', 'model_revision', 'asset_manifest', 'asset_size', 'asset_hash', 'sample_rate'}:
            return 'asset_error'
    return 'native_error'


def emit_failure(reason):
    import os
    # Revalidate even internally; never stringify arbitrary objects/exceptions.
    safe = reason if type(reason) is str and reason in SAFE_FAILURE_REASONS else 'native_error'
    os.write(2, FAILURE_PREFIX + safe.encode('ascii') + b'\n')


def require_headroom(available_kib):
    if available_kib < 1100 * 1024:
        raise WorkerFailure('memory_headroom')


def guard_resources(available_kib, rss_kib):
    if available_kib < 500 * 1024 or rss_kib > 720 * 1024:
        import os
        emit_failure('resource_limit')
        os._exit(75)

def validate_delivery(controls):
    """Copy only the two documented numeric SDK controls; no style labels."""
    import math
    if type(controls) is not dict or set(controls) != {'speed', 'silence_duration'}:
        raise ValueError('delivery_controls')
    result = {}
    for key, low, high in (('speed', 0.9, 1.1), ('silence_duration', 0.1, 0.5)):
        value = controls[key]
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError('delivery_controls')
        result[key] = float(value)
    return result


def validate_request(request):
    if not isinstance(request,dict) or set(request) not in ({'text'}, {'text', 'delivery'}):
        raise ValueError('request_fields')
    if 'delivery' in request:
        validate_delivery(request['delivery'])
    text=request['text']
    if not isinstance(text,str) or not text.strip() or len(text)>800 or len(text.encode('utf8'))>3200:raise ValueError('text_limit')
    if any(ord(c)<32 and c not in '\n\t' for c in text):raise ValueError('text_control')
    stripped=re.sub(r'<(?:laugh|breath|sigh)>','',text)
    if '<' in stripped or '>' in stripped:raise ValueError('unsupported_tag')
    if len(re.findall(r'<(?:laugh|breath|sigh)>',text))>3:raise ValueError('tag_limit')
    return text

IDENTITY={'backend':'supertonic3','voice':'M1','language':'id','sample_rate':44100,'channels':1,'sample_width':2}
REVISION='3cadd1ee6394adea1bd021217a0e650ede09a323'

def check_assets(root, manifest):
    try:
        return _check_assets(root, manifest)
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        raise AssetFailure('asset_error') from None


def _check_assets(root, manifest):
    root,manifest=Path(root),Path(manifest)
    if not root.is_absolute() or not manifest.is_absolute():raise ValueError('absolute_assets_required')
    data=json.loads(manifest.read_text())
    if data['revision']!=REVISION:raise ValueError('model_revision')
    required={'onnx/duration_predictor.onnx','onnx/text_encoder.onnx','onnx/vector_estimator.onnx','onnx/vocoder.onnx','onnx/tts.json','onnx/unicode_indexer.json','config.json','voice_styles/M1.json','README.md','LICENSE'}
    if {r['path'] for r in data['files']}!=required:raise ValueError('asset_manifest')
    for row in data['files']:
        f=root/row['path']
        if f.stat().st_size!=row['bytes']:raise ValueError('asset_size')
        h=hashlib.sha256()
        with f.open('rb') as stream:
            while b:=stream.read(1024*1024):h.update(b)
        if h.hexdigest()!=row['sha256']:raise ValueError('asset_hash')
    if json.loads((root/'onnx/tts.json').read_text())['ae']['sample_rate']!=44100:raise ValueError('sample_rate')
    return dict(IDENTITY)

AUTOMATIC_PROFILE_IDS = tuple(f'conditioning_b_{a}_{i}' for a in ('calm', 'active') for i in ('low', 'medium', 'high'))
PROFILE_IDS = ('original', 'conditioning_b') + AUTOMATIC_PROFILE_IDS
AUTOMATIC_HELPER_SHA256 = '0058579c56aefd4cc3c02ba87b646493518c2af82e9dd020a90d74cde533e44c'


def selected_profile_path(root, manifest, selected_profile='original'):
    """Only a fixed enum selects the app-owned profile. Safe with python -I."""
    if type(selected_profile) is not str or selected_profile not in PROFILE_IDS:
        raise WorkerFailure('invalid_request')
    if selected_profile == 'original':
        return None
    if selected_profile in AUTOMATIC_PROFILE_IDS:
        try:
            import importlib.util, os, stat
            path = Path(__file__).absolute().with_name('automatic_conditioning.py')
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError
                content = stream.read(1000001)
            if hashlib.sha256(content).hexdigest() != AUTOMATIC_HELPER_SHA256:
                raise ValueError
            spec = importlib.util.spec_from_file_location('_automatic_conditioning_fixed', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.profile_path(root, manifest, selected_profile)
        except (OSError, ValueError, ImportError, AttributeError, TypeError, KeyError, RecursionError):
            raise AssetFailure('asset_error') from None
    try:
        import importlib.util
        path = Path(__file__).absolute().with_name('approved_conditioning.py')
        if path.is_symlink():
            raise ValueError
        spec = importlib.util.spec_from_file_location('_approved_conditioning_fixed', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.approved_profile(root, manifest)
    except (OSError, ValueError, ImportError, AttributeError, TypeError):
        raise AssetFailure('asset_error') from None


class Runtime:
    def __init__(self,root,manifest,selected_profile='original'):
        import time
        self.start=time.monotonic()
        check_assets(root,manifest)
        profile_path = selected_profile_path(root, manifest, selected_profile)
        from supertonic import TTS
        self.engine=TTS(model='supertonic-3',model_dir=str(root),auto_download=False,intra_op_num_threads=2,inter_op_num_threads=1)
        if self.engine.sample_rate!=44100:raise ValueError('sample_rate')
        if profile_path is None:
            self.style=self.engine.get_voice_style('M1')
        else:
            try:
                self.style=self.engine.get_voice_style_from_path(str(profile_path))
            except (OSError, ValueError, KeyError, TypeError):
                raise AssetFailure('asset_error') from None
        self.cold_load_seconds=time.monotonic()-self.start
        self.calls=0

    def synthesize(self,request):
        import io,time,wave,resource
        import numpy as np
        text=validate_request(request)
        start=time.monotonic()
        np.random.seed(20261026) # SDK uses numpy.random.randn; no invented API seed argument.
        # The no-delivery branch deliberately retains the original SDK call:
        # speed=1.05 and the pinned SDK's silence_duration default (0.3).
        controls = dict(request['delivery']) if 'delivery' in request else {'speed': 1.05}
        samples,duration=self.engine.synthesize(text,voice_style=self.style,lang='id',total_steps=8,verbose=False,**controls)
        elapsed=time.monotonic()-start
        samples=np.asarray(samples).reshape(-1)
        if not samples.size or not np.isfinite(samples).all():raise ValueError('invalid_audio')
        if samples.size>44100*30:raise ValueError('duration_limit')
        peak=float(np.max(np.abs(samples)))
        if peak>=1:raise ValueError('clipping')
        pcm=np.rint(samples*32767).astype('<i2')
        if not np.any(pcm):raise ValueError('empty_audio')
        out=io.BytesIO()
        with wave.open(out,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes(pcm.tobytes())
        data=out.getvalue();seconds=len(pcm)/44100
        self.calls+=1
        metrics={'inference_seconds':elapsed,'cold_load_seconds':self.cold_load_seconds,'first_audio_seconds':elapsed+(self.cold_load_seconds if self.calls==1 else 0),'duration_seconds':seconds,'rtf':elapsed/seconds,'bytes':len(data),'sample_rate':44100,'peak':peak,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'call':self.calls,'streaming':False}
        return data,metrics

def isolate():
    """Fail-closed native syscall denial, compatible with an existing service sandbox."""
    import ctypes,errno,socket
    lib=ctypes.CDLL('libseccomp.so.2',use_errno=True)
    lib.seccomp_init.argtypes=[ctypes.c_uint32];lib.seccomp_init.restype=ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p];lib.seccomp_syscall_resolve_name.restype=ctypes.c_int
    lib.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
    lib.seccomp_load.argtypes=[ctypes.c_void_p];lib.seccomp_release.argtypes=[ctypes.c_void_p]
    ctx=lib.seccomp_init(0x7fff0000)
    if not ctx:raise WorkerFailure('network_sandbox')
    try:
        for name in (b'socket',b'connect',b'sendto',b'sendmsg',b'sendmmsg'):
            number=lib.seccomp_syscall_resolve_name(name)
            if number<0 or lib.seccomp_rule_add(ctx,0x00050000|errno.EPERM,number,0):raise WorkerFailure('network_sandbox')
        if lib.seccomp_load(ctx):raise WorkerFailure('network_sandbox')
    finally:lib.seccomp_release(ctx)
    native=ctypes.CDLL(None,use_errno=True)
    if native.socket(2,1,0)!=-1 or ctypes.get_errno()!=errno.EPERM:raise WorkerFailure('network_sandbox')
    try:socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    except PermissionError:return True
    raise WorkerFailure('network_sandbox')

def main():
    import sys,os,resource,signal,importlib.metadata
    if len(sys.argv) not in (4,5) or sys.argv[1] not in ('check','tts'):raise WorkerFailure('invalid_request')
    selected_profile = sys.argv[4] if len(sys.argv) == 5 else 'original'
    if selected_profile not in PROFILE_IDS:raise WorkerFailure('invalid_request')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='2'
    os.environ['HF_HUB_OFFLINE']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    resource.setrlimit(resource.RLIMIT_CPU,(60,65));resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3));resource.setrlimit(resource.RLIMIT_FSIZE,(4_000_000,4_000_000));resource.setrlimit(resource.RLIMIT_NOFILE,(128,128));signal.alarm(75)
    for package,version in [('supertonic','1.3.1'),('numpy','2.2.6'),('onnxruntime','1.23.2')]:
        if importlib.metadata.version(package)!=version:raise ValueError('runtime_version')
    denied=isolate()
    if sys.argv[1]=='check':
        result=check_assets(sys.argv[2],sys.argv[3]);result['native_network_denied']=denied
        if selected_profile_path(sys.argv[2], sys.argv[3], selected_profile) is not None:
            result.update(voice_style_profile=selected_profile, style_label='Conditioning B (+0.4)' if selected_profile == 'conditioning_b' else 'Experimental B-anchored activation')
        sys.stdout.write(json.dumps(result));return
    payload=sys.stdin.buffer.read(4097)
    if len(payload)>4096:raise ValueError('input_limit')
    request=json.loads(payload);validate_request(request)
    def memory():
        return int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
    require_headroom(memory())
    import threading,time
    def guard():
        while True:
            rows=Path('/proc/self/status').read_text().splitlines();rss=int(next(l.split()[1] for l in rows if l.startswith('VmRSS:')))
            guard_resources(memory(),rss)
            time.sleep(.05)
    threading.Thread(target=guard,daemon=True).start()
    runtime=(Runtime(Path(sys.argv[2]),Path(sys.argv[3])) if selected_profile == 'original'
             else Runtime(Path(sys.argv[2]),Path(sys.argv[3]),selected_profile))
    data,metrics=runtime.synthesize(request)
    if len(data)>2_646_044:raise ValueError('output_limit')
    sys.stdout.buffer.write(data)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        import sys
        emit_failure(failure_reason(exc));sys.exit(1)
