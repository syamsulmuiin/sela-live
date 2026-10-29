"""PCM and WAV boundaries for local voice input/output."""
import io
import wave


def pcm16_wav(pcm: bytes, sample_rate: int) -> bytes:
    if type(sample_rate) is not int or sample_rate != 16000:
        raise ValueError('PCM input must use 16000 Hz')
    if type(pcm) is not bytes or not pcm or len(pcm) % 2 or len(pcm) > 16000 * 2 * 20:
        raise ValueError('PCM must be nonempty mono PCM16, at most 20 seconds')
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm)
    return out.getvalue()
