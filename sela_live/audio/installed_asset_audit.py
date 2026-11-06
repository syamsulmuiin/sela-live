"""Read-only installed voice asset inventory and WAV quality measurements.

Never runs models, downloads files, imports native SDKs, or reads credentials.
Does not claim recognition accuracy or subjective voice naturalness.
"""
import hashlib
import json
from pathlib import Path
import wave
import math
from array import array
import sys


def sha256_file(path, *, max_bytes=2_500_000_000):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
        raise ValueError("Missing, symlinked or oversized model file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def inventory(model_root, *, names):
    root = Path(model_root).resolve(strict=True)
    if not root.is_dir() or len(names) > 24:
        raise ValueError("Invalid model inventory")
    files = {}
    for name in names:
        if type(name) is not str or not name or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError("Only direct model asset filenames are permitted")
        target = root / name
        files[name] = sha256_file(target)
    return {"model_root": str(root), "files": files, "languages_verified": False}


def wav_metrics(path, *, max_bytes=12_000_000):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
        raise ValueError("Invalid WAV file")
    with wave.open(str(path), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        frames = source.getnframes()
        if channels != 1 or width != 2 or rate not in (16000, 22050, 24000, 44100, 48000) or not 0 < frames <= rate * 120:
            raise ValueError("Unsupported WAV format")
        pcm = source.readframes(frames)
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    peak = max(abs(x) for x in samples)
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    return {"duration_seconds": round(frames / rate, 4), "sample_rate": rate,
            "peak_fraction": round(peak / 32768, 6),
            "rms_dbfs": round(20 * math.log10(max(rms, 1) / 32768), 3),
            "clipped_samples": sum(1 for x in samples if abs(x) >= 32767),
            "acoustic_quality_verified": False}


def save_report(path, result):
    target = Path(path)
    if target.exists():
        raise FileExistsError("Never overwrite prior audit reports")
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
