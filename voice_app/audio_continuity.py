"""Conservative multipart Supertonic PCM boundary trimming, never a VAD.

Only contiguous abs(sample)<=1 edge samples are removable. First leading and
last trailing edges survive. Retained PCM is copied verbatim, including internal
pauses. Unsupported/unsafe input raises ValueError (fail closed, no fallback);
all-floor audio is returned unchanged. Canonical 44-byte PCM WAV only: the native
adapter already normalizes to this form. No ancillary chunks are discarded.
"""
from array import array
import struct
import sys

SAMPLE_RATE = 44100
MAX_FRAMES = SAMPLE_RATE * 30
MAX_WAV_BYTES = 4_000_000
PADDING_FRAMES = 5292  # 120 ms
MIN_QUIET_FRAMES = 11025  # 250 ms
MAX_REMOVE_FRAMES = 33075  # 750 ms per edge


def trim_boundary_silence(raw, *, first, last, max_output_bytes=MAX_WAV_BYTES):
    """Return a bounded canonical WAV with only eligible edge floor removed.

    Reject empty, truncated, extra-chunk, non-PCM16/mono/44100, >30s, over-limit,
    and malformed input explicitly. Do not forward it as a successful result.
    A valid single-piece input and any unchanged input retain every WAV byte.
    """
    if (type(raw) is not bytes or type(first) is not bool or type(last) is not bool
            or type(max_output_bytes) is not int or max_output_bytes <= 44
            or not 44 < len(raw) <= min(MAX_WAV_BYTES, max_output_bytes)):
        raise ValueError('Invalid continuity WAV input or limit')
    try:
        riff, size, kind, fmt, fmt_size, codec, channels, rate, byte_rate, align, width, data, count = struct.unpack('<4sI4s4sIHHIIHH4sI', raw[:44])
    except struct.error as exc:
        raise ValueError('Invalid continuity WAV header') from exc
    if ((riff, kind, fmt, fmt_size, codec, channels, rate, byte_rate, align, width, data)
            != (b'RIFF', b'WAVE', b'fmt ', 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE*2, 2, 16, b'data')
            or size != len(raw)-8 or count != len(raw)-44 or count % 2
            or not 0 < count//2 <= MAX_FRAMES):
        raise ValueError('Continuity requires bounded canonical mono PCM16 44100 WAV')
    if first and last:
        return raw
    samples = array('h')
    samples.frombytes(raw[44:])
    if sys.byteorder != 'little':
        samples.byteswap()
    leading = 0
    while leading < len(samples) and abs(samples[leading]) <= 1:
        leading += 1
    if leading == len(samples):
        return raw  # No active anchor: never reduce a silent waveform.
    trailing = 0
    while abs(samples[len(samples)-1-trailing]) <= 1:
        trailing += 1
    left = min(leading-PADDING_FRAMES, MAX_REMOVE_FRAMES) if not first and leading >= MIN_QUIET_FRAMES else 0
    right = min(trailing-PADDING_FRAMES, MAX_REMOVE_FRAMES) if not last and trailing >= MIN_QUIET_FRAMES else 0
    if not left and not right:
        return raw
    retained = raw[44+left*2:len(raw)-right*2]
    if not retained:
        raise ValueError('Continuity would produce empty PCM')
    result = raw[:4] + struct.pack('<I', len(retained)+36) + raw[8:40] + struct.pack('<I', len(retained)) + retained
    if len(result) > max_output_bytes:
        raise ValueError('Continuity output exceeds limit')
    return result
