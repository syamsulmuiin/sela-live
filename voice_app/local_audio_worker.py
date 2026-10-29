"""Offline audio worker. No web/Hermes imports or credentials.

Protocol: trusted action+absolute model path argv; bounded WAV (STT) or JSON
{text:...} (TTS) stdin; JSON text or complete PCM16 WAV stdout. One request per
process by default. Explicit serve-stt mode shares a single offline model across
bounded framed prefixes/finals; it is not a native incremental decoder.
"""
import io
import json
import os
from pathlib import Path
import sys
import wave

MAX_INPUT_WAV = 640044
MAX_OUTPUT_WAV = 4_000_000


def validate_model(path, action):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Absolute local model path required')
    required = ([path / n for n in ('model.bin', 'config.json', 'tokenizer.json')]
                if action == 'stt' else [path, Path(str(path) + '.json')])
    if not all(p.is_file() and p.stat().st_size for p in required):
        raise ValueError('Local model missing')
    if action == 'tts':
        config = json.loads(required[1].read_text(encoding='utf-8'))
        if (config.get('audio', {}).get('sample_rate') != 22050
                or config.get('phoneme_type', 'espeak') != 'espeak'
                or config.get('espeak', {}).get('voice') != 'id'):
            raise ValueError('Expected Indonesian espeak Piper 22050 Hz voice')
    return path


def load_stt_model(path):
    from faster_whisper import WhisperModel
    return WhisperModel(str(validate_model(path, 'stt')), device='cpu', compute_type='int8',
                        cpu_threads=2, num_workers=1, local_files_only=True)


def transcribe(payload, path, model=None, *, draft=False):
    if not 44 < len(payload) <= MAX_INPUT_WAV:
        raise ValueError('Invalid STT WAV size')
    with wave.open(io.BytesIO(payload)) as reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 16000):
            raise ValueError('STT requires mono PCM16 16000 Hz')
        frames = reader.getnframes()
        pcm = reader.readframes(frames + 1)
        if not 0 < frames <= 320000 or len(pcm) != frames * 2:
            raise ValueError('Invalid STT WAV duration')
    path = validate_model(path, 'stt')
    # Exact digital silence is not language. VAD handles less trivial silence.
    if not any(pcm):
        return {'text':''}
    if model is None:
        model = load_stt_model(path)
    segments, _ = model.transcribe(io.BytesIO(payload), language='id', beam_size=1 if draft else 5,
        vad_filter=True, vad_parameters={'min_silence_duration_ms':500},
        condition_on_previous_text=False, no_speech_threshold=.6, log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4)
    # Match upstream's joint silence rule, including its confidence override.
    # An extra independent confidence gate would drop speech already accepted
    # by the decoder; VAD and the upstream thresholds remain enabled above.
    text = ' '.join(segment.text.strip() for segment in segments
                    if segment.no_speech_prob <= .6 or segment.avg_logprob > -1.0).strip()
    if len(text) > 4000:
        raise ValueError('STT text exceeds limit')
    return {'text':text}


def synthesize(payload, path):
    if len(payload) > 96000:
        raise ValueError('TTS request exceeds limit')
    body = json.loads(payload)
    text = body.get('text')
    if set(body) != {'text'} or not isinstance(text, str) or not text.strip() or len(text) > 16000:
        raise ValueError('Invalid TTS request')
    path = validate_model(path, 'tts')
    from piper import PiperVoice
    from piper.config import PiperConfig
    import onnxruntime
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    config = json.loads(Path(str(path) + '.json').read_text(encoding='utf-8'))
    voice = PiperVoice(config=PiperConfig.from_dict(config), session=onnxruntime.InferenceSession(
        str(path), sess_options=options, providers=['CPUExecutionProvider']))
    class BoundedBuffer(io.BytesIO):
        def write(self, data):
            if self.tell() + len(data) > MAX_OUTPUT_WAV:
                raise ValueError('Piper WAV exceeds limit')
            return super().write(data)
    result = BoundedBuffer()
    with wave.open(result, 'wb') as output:
        voice.synthesize_wav(text, output)
    return result.getvalue()


def deny_network(event, args):
    if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.bind'):
        raise RuntimeError('Audio worker network disabled')


def serve_stt(path, source, output, *, draft=False, enforce_limits=False):
    """Serial bounded prefix re-decoding, same model/options as final decoding."""
    import struct
    import time
    import resource
    model = load_stt_model(path)
    output.write(b'READY\n'); output.flush()
    while True:
        header = source.read(4)
        if not header:
            return
        if len(header) != 4:
            raise ValueError('Truncated frame')
        size = struct.unpack('!I', header)[0]
        if not 44 < size <= MAX_INPUT_WAV:
            raise ValueError('Invalid frame size')
        payload = source.read(size)
        if len(payload) != size:
            raise ValueError('Truncated audio')
        start = time.monotonic()
        if enforce_limits:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            resource.setrlimit(resource.RLIMIT_CPU, (min(180, int(usage.ru_utime + usage.ru_stime) + 86), 185))
        result = transcribe(payload, path, model=model, **({'draft':True} if draft else {}))
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result['metrics'] = {'inference_seconds':time.monotonic()-start,
                             'cpu_seconds':usage.ru_utime + usage.ru_stime,
                             'maxrss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
        body = json.dumps(result, ensure_ascii=False).encode('utf-8')
        if len(body) > 65536:
            raise ValueError('Output exceeds cap')
        output.write(struct.pack('!I',len(body))+body); output.flush()


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('stt', 'tts', 'check-stt', 'check-tts', 'serve-stt', 'serve-draft'):
        return 2
    action = sys.argv[1]
    draft = action == 'serve-draft'
    if draft:
        action = 'serve-stt'
    path = Path(sys.argv[2])
    os.environ.update({'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1',
                       'OMP_NUM_THREADS':'2', 'OPENBLAS_NUM_THREADS':'2', 'MKL_NUM_THREADS':'2'})
    sys.addaudithook(deny_network)
    # CPU time is bounded independently from the parent's wall-clock deadline.
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (180, 185) if action == 'serve-stt' else (85, 90))
    output_fd = os.dup(1)
    try:
        # Native-library diagnostics must not corrupt protocol output or leak text.
        with open(os.devnull, 'wb') as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        validate_model(path, 'stt' if action == 'serve-stt' else action.removeprefix('check-'))
        if action == 'serve-stt':
            resource.setrlimit(resource.RLIMIT_CPU, (180, 185))
            with os.fdopen(os.dup(output_fd), 'wb') as output:
                serve_stt(path, sys.stdin.buffer, output, draft=draft, enforce_limits=True)
            return 0
        if action == 'check-stt':
            import faster_whisper
            result = b''
        elif action == 'check-tts':
            import piper
            import onnxruntime
            result = b''
        elif action == 'stt':
            payload = sys.stdin.buffer.read(MAX_INPUT_WAV + 1)
            result = json.dumps(transcribe(payload, path), ensure_ascii=False).encode('utf-8')
        else:
            result = synthesize(sys.stdin.buffer.read(96001), path)
        view = memoryview(result)
        while view:
            count = os.write(output_fd, view)
            view = view[count:]
        return 0
    except Exception:
        return 1
    finally:
        os.close(output_fd)


if __name__ == '__main__':
    sys.exit(main())
