"""Optional real multilingual recognizer using a locally installed faster-whisper model.

No downloads, network, provider fallback or production changes. The caller
must supply a verified local CTranslate2 model directory and 16 kHz mono PCM.
"""
from array import array
from pathlib import Path
import sys
from sela_live.audio.language_contract import normalize_language


class WhisperMultilingualSTT:
    def __init__(self, model_dir, *, device="cpu", compute_type="int8"):
        path = Path(model_dir)
        if not path.is_dir() or not (path / "model.bin").is_file():
            raise ValueError("Local faster-whisper model is required")
        if device != "cpu" or compute_type != "int8":
            raise ValueError("Only bounded CPU int8 configuration is supported")
        from faster_whisper import WhisperModel
        self.model = WhisperModel(str(path), device=device,
                                  compute_type=compute_type, local_files_only=True)

    def transcribe(self, pcm16, *, language="auto"):
        if (type(pcm16) is not bytes or not 3200 <= len(pcm16) <= 640000
                or len(pcm16) % 2):
            raise ValueError("Expected 16 kHz mono PCM16, 0.1 to 20 seconds")
        language = normalize_language(language)
        samples = array("h")
        samples.frombytes(pcm16)
        if sys.byteorder != "little":
            samples.byteswap()
        import numpy as np
        audio = np.asarray(samples, dtype=np.float32) / 32768.0
        segments, info = self.model.transcribe(
            audio, language=None if language == "auto" else language,
            beam_size=3, vad_filter=True, condition_on_previous_text=False)
        text = "".join(segment.text for segment in segments).strip()
        detected = normalize_language(info.language)
        confidence = float(info.language_probability)
        if not 0 <= confidence <= 1:
            raise ValueError("Invalid language confidence")
        if len(text) > 4000:
            raise ValueError("Transcript limit")
        return {"text": text, "language": detected, "confidence": confidence}
