"""Ephemeral acoustic aggregates only; no speech or emotion interpretation.

Standard-library implementation: no model, recording, transcript, or dependency.
"""
import math
import struct


def diagnose_pcm16(pcm, sample_rate):
    """Summarize bounded original mono little-endian PCM16, without retaining it.

    Silence is a fixed -45 dBFS, 20 ms energy heuristic, NOT a speech VAD.
    Digital silence has null dBFS rather than non-JSON negative infinity.
    """
    if (type(sample_rate) is not int or sample_rate != 16000
            or type(pcm) is not bytes or not pcm or len(pcm) % 2
            or len(pcm) > 640000):
        raise ValueError('Expected bounded mono PCM16 16000 Hz')
    samples = [item[0] for item in struct.iter_unpack('<h', pcm)]
    count = len(samples)
    energy = sum(value * value for value in samples)
    peak = max(abs(value) for value in samples)
    rms = math.sqrt(energy / count) / 32768
    rms_dbfs = round(20 * math.log10(rms), 2) if rms else None
    peak_dbfs = round(20 * math.log10(peak / 32768), 2) if peak else None
    silent_samples = 0
    for start in range(0, count, 320):
        frame = samples[start:start + 320]
        if sum(value * value for value in frame) / len(frame) < (32768 * 10 ** (-45 / 20)) ** 2:
            silent_samples += len(frame)
    silence = silent_samples / count
    warnings = []
    if rms_dbfs is None or rms_dbfs < -40:
        warnings.append('low_level')
    if silence > .8:
        warnings.append('mostly_silent')
    clipping = sum(value in (-32768, 32767) for value in samples) / count
    if clipping >= .01:
        warnings.append('clipping')
    if count / sample_rate < .25:
        warnings.append('too_short')
    return {
        'source': 'original_pcm', 'duration_seconds': round(count / sample_rate, 4),
        'rms_dbfs': rms_dbfs, 'peak_dbfs': peak_dbfs,
        'clipping_fraction': round(clipping, 6),
        'silence_fraction': round(silence, 4), 'non_silent_fraction': round(1 - silence, 4),
        'f0_hz': None, 'f0_status': 'not_evaluated',
        'emotion': None, 'emotion_status': 'not_evaluated', 'warnings': warnings,
    }
