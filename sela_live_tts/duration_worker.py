"""Isolated duration-graph predictions only. No speech or PCM is produced.

CLI: serve ASSETS MODEL_MANIFEST FIXED_PROFILE EXPECTED_SOURCE_MANIFEST_SHA
The reviewed source manifest lives beside this script, including after relocation.
"""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
PIN_HELPER_SHA = '9436ec03105e5a7a787cdfc8cb87e4e77d10670a58656b1561fa3218a59dccc0'
PROFILES = ('conditioning_b',) + tuple(
    f'conditioning_b_{a}_{i}' for a in ('calm', 'active') for i in ('low', 'medium', 'high'))
MAX_QUERIES = 64
LINE_BYTES = 4096
INPUT_BYTES = MAX_QUERIES * LINE_BYTES
SPEED = 1.05


def validate_profile(profile):
    if type(profile) is not str or profile not in PROFILES:
        raise ValueError('invalid_request')
    return profile


def validate_text(text):
    if (type(text) is not str or not text.strip() or len(text) > 800
            or len(text.encode('utf8')) > 3200):
        raise ValueError('text_limit')
    if any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise ValueError('text_control')
    tags = re.findall(r'<(?:laugh|breath|sigh)>', text)
    stripped = re.sub(r'<(?:laugh|breath|sigh)>', '', text)
    if '<' in stripped or '>' in stripped or len(tags) > 3:
        raise ValueError('unsupported_tag')
    return text


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('invalid_request')
        result[key] = value
    return result


def nonfinite(_):
    raise ValueError('invalid_request')


def parse_request(raw, expected_id):
    if not raw or len(raw) > LINE_BYTES or not raw.endswith(b'\n'):
        raise ValueError('input_limit')
    value = json.loads(raw.decode('utf8'), object_pairs_hook=unique_object, parse_constant=nonfinite)
    if (type(value) is not dict or set(value) != {'id', 'text'}
            or type(value['id']) is not int or value['id'] != expected_id
            or not 1 <= expected_id <= MAX_QUERIES):
        raise ValueError('invalid_request')
    return expected_id, validate_text(value['text'])


def safe_path(value, *, directory=False):
    path = Path(value)
    if (not path.is_absolute() or '..' in path.parts
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError('asset_error')
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError('asset_error')
    return path


def load_predictor(root, profile_path, *, ort=None, np=None, processor_class=None):
    """Import only after isolation; injectable harmless runtime in unit tests."""
    if ort is None:
        import onnxruntime as ort
    if np is None:
        import numpy as np
    if processor_class is None:
        from supertonic.core import UnicodeProcessor
        processor_class = UnicodeProcessor
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    # The guard validates the complete immutable profile before this read.
    data = json.loads(safe_path(profile_path).read_text())['style_dp']
    if data['dims'] != [1, 8, 16]:
        raise ValueError('asset_error')
    style_dp = np.asarray(data['data'], dtype=np.float32).reshape(*data['dims'])
    processor = processor_class(str(root / 'onnx/unicode_indexer.json'))
    dp = ort.InferenceSession(str(root / 'onnx/duration_predictor.onnx'),
                             sess_options=opts, providers=['CPUExecutionProvider'])
    return Predictor(processor, dp, style_dp)


class Predictor:
    """Identical single-item core preprocessing and DP call, not PCM duration."""
    def __init__(self, processor, dp_session, style_dp):
        self.processor, self.dp_session, self.style_dp = processor, dp_session, style_dp

    def __call__(self, text):
        validate_text(text)
        ids, mask = self.processor([text], 'id')
        tokens = int(ids.shape[-1])
        if not 1 <= tokens <= 32768:
            raise ValueError('text_limit')
        if tokens > 512:
            return {'seconds': None, 'tokens': tokens, 'refusal': 'model_token_limit'}
        duration, *_ = self.dp_session.run(None, {
            'text_ids': ids, 'style_dp': self.style_dp, 'text_mask': mask})
        # Preserve the SDK's array division (including its dtype rounding).
        duration = duration / SPEED
        if duration.size != 1:
            raise ValueError('duration_limit')
        seconds = float(duration.reshape(-1)[0])
        if not math.isfinite(seconds) or not 0 < seconds <= 3600:
            raise ValueError('duration_limit')
        return {'seconds': seconds, 'tokens': tokens}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError('asset_error')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_native(expected_sha):
    if type(expected_sha) is not str or re.fullmatch('[0-9a-f]{64}', expected_sha) is None:
        raise ValueError('asset_error')
    helper = safe_path(HERE / 'source_pins.py')
    if helper.stat().st_size > 1_000_000 or hashlib.sha256(helper.read_bytes()).hexdigest() != PIN_HELPER_SHA:
        raise ValueError('asset_error')
    pins = load_module('_duration_source_pins', helper)
    data = pins.verify_sources(HERE / 'upstream-manifest.json', expected_sha)
    # Neither guard nor SDK is imported before all pinned sources are checked.
    guard = load_module('_duration_native_guard', Path(data['guard_root']) / 'supertonic3_worker.py')
    return data, guard


def offline_environment():
    return dict(PATH='/usr/bin:/bin', HOME='/nonexistent', LANG='C.UTF-8',
                HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS='2',
                OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')


def install_limits():
    import resource
    import signal
    resource.setrlimit(resource.RLIMIT_CPU, (60, 65))
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4_000_000, 4_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    signal.alarm(75)


def arm_deadline(seconds):
    import signal
    def expired(*_):
        os.write(2, b'supertonic3_failure=resource_limit\n')
        os._exit(75)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(.001, seconds))


def check_versions():
    import importlib.metadata
    for package, version in (('supertonic','1.3.1'), ('numpy','2.2.6'), ('onnxruntime','1.23.2')):
        if importlib.metadata.version(package) != version:
            raise ValueError('runtime_version')


def start_resource_guard(guard):
    import threading
    import time
    def memory():
        return int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                        if line.startswith('MemAvailable:')))
    guard.require_headroom(memory())
    def monitor():
        try:
            while True:
                rss = int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                               if line.startswith('VmRSS:')))
                guard.guard_resources(memory(), rss)
                time.sleep(.05)
        except Exception:
            os.write(2, b'supertonic3_failure=resource_limit\n')
            os._exit(75)
    threading.Thread(target=monitor, daemon=True).start()


def serve(predictor, stdin, stdout, deadline):
    import time
    def emit(value):
        line = json.dumps(value, allow_nan=False, separators=(',', ':')) + '\n'
        if len(line.encode('utf8')) > LINE_BYTES:
            raise ValueError('output_limit')
        stdout.write(line)
        stdout.flush()
    emit({'type':'ready', 'protocol':1, 'max_queries':MAX_QUERIES, 'native_network_denied':True})
    total = 0
    for expected_id in range(1, MAX_QUERIES + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('resource_limit')
        arm_deadline(remaining)
        raw = stdin.readline(LINE_BYTES + 1)
        if not raw:
            return
        total += len(raw)
        if total > INPUT_BYTES:
            raise ValueError('input_limit')
        query_id, text = parse_request(raw, expected_id)
        arm_deadline(min(10., deadline - time.monotonic()))
        result = predictor(text)
        if time.monotonic() > deadline:
            raise ValueError('resource_limit')
        emit({'id':query_id, **result})
    # No 65th query accepted, no idle worker retained after budget exhaustion.


def main(argv=None, *, stdin=None, stdout=None):
    import time
    started = time.monotonic()
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 5 or argv[0] != 'serve':
        raise ValueError('invalid_request')
    _, assets, manifest, profile, expected_sha = argv
    validate_profile(profile)
    root = safe_path(assets, directory=True)
    model_manifest = safe_path(manifest)
    data, guard = verify_native(expected_sha)
    install_limits()
    os.environ.clear()
    os.environ.update(offline_environment())
    if guard.isolate() is not True:
        raise ValueError('network_sandbox')
    # Reject every symlinked asset path before the unchanged full asset guard.
    inventory = json.loads(model_manifest.read_text())
    for row in inventory['files']:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('asset_error')
        safe_path(root / relative)
    guard.check_assets(root, model_manifest)
    profile_path = guard.selected_profile_path(root, model_manifest, profile)
    check_versions()
    start_resource_guard(guard)
    sys.path.insert(0, str(Path(data['sdk_root']).parent))
    predictor = load_predictor(root, profile_path)
    serve(predictor, sys.stdin.buffer if stdin is None else stdin,
          sys.stdout if stdout is None else stdout, started + 70.)


SAFE_REASONS = frozenset({'asset_error','invalid_request','invalid_text','resource_limit',
                          'runtime_version','network_sandbox','duration_limit','output_limit',
                          'memory_headroom','memory_allocation'})


def safe_reason(exc):
    if isinstance(exc, MemoryError):
        return 'memory_allocation'
    if len(exc.args) == 1 and type(exc.args[0]) is str:
        if exc.args[0] in SAFE_REASONS:
            return exc.args[0]
        if exc.args[0] in {'text_limit','text_control','unsupported_tag','input_limit'}:
            return 'invalid_text'
    return 'asset_error'


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        os.write(2, b'supertonic3_failure=' + safe_reason(exc).encode('ascii') + b'\n')
        raise SystemExit(1)
