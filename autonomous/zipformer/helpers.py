"""Isolated Zipformer experiment helpers; implemented by RED/GREEN slices."""
import hashlib

def score_text(text, reference=None, full=False):
    """Evaluation only; never return raw reference/hypothesis or score partial WER."""
    import re
    words = re.findall(r'\w+', text.casefold())
    result = {'nonempty': bool(words), 'word_count':len(words), 'hypothesis_sha256':hashlib.sha256(text.encode()).hexdigest()}
    if reference is not None:
        refs = re.findall(r'\w+', reference.casefold())
        result['first_word_matches'] = bool(words and refs and words[0] == refs[0])
        if full and refs:
            row = list(range(len(words)+1))
            for i, ref in enumerate(refs, 1):
                nxt = [i]
                for j, word in enumerate(words, 1):
                    nxt.append(min(nxt[-1]+1, row[j]+1, row[j-1]+(ref!=word)))
                row = nxt
            result.update(word_errors=row[-1], reference_words=len(refs), wer=row[-1]/len(refs))
    return result


def verify_artifact(data, metadata):
    if len(data) != metadata['size']:
        raise ValueError('artifact size mismatch')
    digest = hashlib.sha256(data).hexdigest()
    if 'lfs' in metadata:
        valid = digest == metadata['lfs']['sha256']
    else:
        valid = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == metadata['blobId']
    if not valid:
        raise ValueError('artifact upstream digest mismatch')
    return digest

def load_recognizer():
    import os
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')
    from pathlib import Path
    import sherpa_onnx
    model = Path(__file__).resolve().parent / 'model'
    files = {role: str(model / (role+'-iter-100000-avg-15-chunk-32-left-256.int8.onnx')) for role in ('encoder', 'decoder', 'joiner')}
    if not all(Path(p).is_file() for p in files.values()):
        raise FileNotFoundError('pinned local transducer assets unavailable')
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        **files, tokens=str(model / 'tokens.txt'), num_threads=2,
        provider='cpu', sample_rate=16000, feature_dim=80,
        decoding_method='greedy_search', enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20.0, debug=False,
    )

def stream_pcm(recognizer, pcm, paced=True):
    """One genuine persistent native online state; 80ms chunks, no prefix replay.

    Raw text stays in the caller's memory. Tail silence is explicit and measured.
    No endpoint-triggered reset until the final utterance is collected.
    """
    import numpy as np
    import time
    import resource
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 640000:
        raise ValueError('expected 0 < mono PCM16 duration <= 20s at 16000Hz')
    audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
    stream = recognizer.create_stream()
    start = time.monotonic()
    infer = 0.0
    events = []
    endpoints = []
    last_text = ''
    fed = 0
    source_end_wall = None
    # 1.6s tail supports ordinary endpoint rule2 (1.2s), not future speech.
    for data, is_source in ((audio, True), (np.zeros(25600, dtype=np.float32), False)):
        for pos in range(0, len(data), 1280):
            chunk = data[pos:pos+1280]
            fed += len(chunk)
            if paced:
                time.sleep(max(0.0, start + fed/16000 - time.monotonic()))
            tick = time.monotonic()
            stream.accept_waveform(16000, chunk)
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            infer += time.monotonic() - tick
            text = recognizer.get_result(stream)
            elapsed = time.monotonic() - start
            if text != last_text:
                events.append({'seconds': elapsed, 'samples_fed': fed, 'text': text, 'during_source': is_source})
                last_text = text
            if recognizer.is_endpoint(stream) and not endpoints:
                endpoints.append({'seconds':elapsed,'samples_fed':fed,'during_source':is_source})
        if is_source:
            source_end_wall = time.monotonic() - start
    tick = time.monotonic()
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    infer += time.monotonic() - tick
    text = recognizer.get_result(stream)
    elapsed = time.monotonic() - start
    recognizer.reset(stream)
    return {'text':text, 'events':events, 'endpoints':endpoints,
            'source_samples_fed':len(audio), 'source_seconds':len(audio)/16000,
            'source_end_wall_seconds':source_end_wall, 'wall_seconds':elapsed,
            'inference_seconds':infer, 'decode_rtf':infer/(len(audio)/16000),
            'finalization_after_source_seconds':elapsed-source_end_wall,
            'tail_seconds':1.6, 'input_finished':True,
            'reset_empty':recognizer.get_result(stream)=='',
            'maxrss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'paced':paced, 'chunk_samples':1280}

