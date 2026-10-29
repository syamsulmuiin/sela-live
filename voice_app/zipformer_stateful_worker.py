"""Offline stateful Indonesian Zipformer worker (private, bounded protocol).

stdin: uint32 big-endian body size, then b'ST1', opcode byte (0 preview,
1 final, 2 forget), 16 UUID bytes, and complete growing WAV (none for forget).
Body cap 640064 bytes, WAV cap 640044, PCM mono signed LE16/16000Hz <=20s.
stdout: READY newline, then uint32 big-endian length + JSON {text, metrics}.
No other stdout/stderr, logs, keys in metrics, or audio retained across calls.
Invalid/truncated/mutated/reordered frames fail closed (worker exits).
An equal duplicate feeds nothing; endpoint detection never owns turn lifetime.
"""
import ctypes
import io
import json
import os
from pathlib import Path
import resource
import socket
import struct
import sys
import time
import wave

ROOT = Path('/root/voice-lab/autonomous/zipformer')


def _handle(state, payload):
    if len(payload) < 20 or payload[:3] != b'ST1' or payload[3] not in (0, 1, 2):
        raise ValueError('Invalid ASR frame')
    op, key = payload[3], payload[4:20].hex()
    if op == 2:
        if len(payload) != 20:
            raise ValueError('Invalid ASR forget frame')
        forgotten = state.forget(key)
        return {'text': '', 'metrics': {'prefix_redecode': False,
            'new_samples_fed': 0, 'source_samples_fed': state.count,
            'stream_resets': state.stream_resets, 'forgotten': forgotten}}
    if not 44 < len(payload) - 20 <= 640044:
        raise ValueError('Invalid ASR WAV size')
    try:
        with wave.open(io.BytesIO(payload[20:]), 'rb') as reader:
            if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getcomptype()) != (1, 2, 16000, 'NONE'):
                raise ValueError('Mono PCM16 16000Hz required')
            count = reader.getnframes()
            if not 0 < count <= 320000:
                raise ValueError('Invalid ASR sample count')
            pcm = reader.readframes(count + 1)
            if len(pcm) != count * 2:
                raise ValueError('Invalid ASR PCM size')
    except (wave.Error, EOFError) as exc:
        raise ValueError('Invalid ASR WAV') from exc
    return state.update(key, pcm, final=op == 1)


def serve(recognizer, source, output, *, enforce_limits=True):
    # Import works as package in tests and as -I standalone script in production.
    if __package__:
        from .zipformer_state import ZipformerState
    else:
        from zipformer_state import ZipformerState
    state = ZipformerState(recognizer)
    output.write(b'READY\n')
    output.flush()
    while True:
        header = source.read(4)
        if not header:
            return
        if len(header) != 4:
            raise ValueError('Truncated ASR header')
        size = struct.unpack('!I', header)[0]
        if not 20 <= size <= 640064:
            raise ValueError('Invalid ASR frame size')
        payload = source.read(size)
        if len(payload) != size:
            raise ValueError('Truncated ASR body')
        used = resource.getrusage(resource.RUSAGE_SELF)
        if enforce_limits:
            resource.setrlimit(resource.RLIMIT_CPU,
                (min(180, int(used.ru_utime + used.ru_stime) + 86), 185))
        begin = time.monotonic()
        result = _handle(state, payload)
        del payload
        text = result['text']
        if not isinstance(text, str) or len(text) > 4000:
            raise ValueError('Invalid ASR result')
        used = resource.getrusage(resource.RUSAGE_SELF)
        result['metrics'].update(inference_seconds=time.monotonic() - begin,
            cpu_seconds=used.ru_utime + used.ru_stime, maxrss_kib=used.ru_maxrss,
            network_namespace_isolated=enforce_limits)
        body = json.dumps(result, ensure_ascii=False).encode()
        if not 0 < len(body) <= 65536:
            raise ValueError('ASR output cap')
        output.write(struct.pack('!I', len(body)) + body)
        output.flush()
        del result, body, text


def main():
    output_fd = os.dup(1)
    try:
        with open(os.devnull, 'wb') as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        if len(sys.argv) != 3 or sys.argv[1] not in ('check', 'serve') or Path(sys.argv[2]) != ROOT / 'model':
            raise ValueError('Explicit pinned model path and action required')
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024**2, 1536 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (180, 185))
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.unshare(0x40000000) != 0:
            raise RuntimeError('Isolated network namespace required; no fallback')
        for host in ('127.0.0.1', '1.1.1.1'):
            try:
                with socket.create_connection((host, 443), timeout=.1):
                    raise ValueError('Network unexpectedly reachable')
            except OSError:
                pass
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
            OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')
        sys.path.insert(0, str(ROOT))
        from helpers import verify_artifact, load_recognizer
        provenance = json.loads((ROOT / 'artifact-verification.json').read_text())
        for artifact in provenance['artifacts']:
            if artifact['path'].startswith('model/'):
                verify_artifact((ROOT / artifact['path']).read_bytes(), artifact['upstream'])
        import sherpa_onnx  # check also verifies native ABI import
        if sys.argv[1] == 'serve':
            # -I intentionally excludes the script directory; only trust this
            # installed worker's sibling state module, never model-supplied code.
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            with os.fdopen(os.dup(output_fd), 'wb') as output:
                serve(load_recognizer(), sys.stdin.buffer, output)
        return 0
    except Exception:
        return 1
    finally:
        os.close(output_fd)


if __name__ == '__main__':
    sys.exit(main())
