"""Static, offline capability audit for existing Sela Zipformer/Supertonic code.

This audit deliberately does not claim that uninspected model weights can
recognize or synthesize any language. No native imports or model downloads.
"""
from dataclasses import dataclass
from pathlib import Path
import ast


@dataclass(frozen=True)
class LocalVoiceAudit:
    stt_engine: str
    tts_engine: str
    configured_stt_language: str
    configured_tts_language: str
    multilingual_verified: bool
    expressive_native_verified: bool
    details: tuple[str, ...]


def audit_source(root: Path) -> LocalVoiceAudit:
    root = Path(root)
    stt_path = root / "sela_live/audio/stt.py"
    tts_path = root / "sela_live/audio/tts.py"
    worker_path = root / "voice_app/supertonic3_worker.py"
    for path in (stt_path, tts_path, worker_path):
        if not path.is_file() or path.stat().st_size > 200000:
            raise ValueError("Missing or oversized local engine source")
    stt = stt_path.read_text(encoding="utf-8")
    tts = tts_path.read_text(encoding="utf-8")
    worker = worker_path.read_text(encoding="utf-8")
    for source in (stt, tts, worker):
        ast.parse(source)
    if ("class ZipformerState" not in stt
            or "self.recognizer.decode_stream" not in stt
            or "kwargs['lang'] != 'id'" not in tts
            or "text_processor([chunks[0]], 'id')" not in tts
            or "'language':'id'" not in worker
            or "voice_style=self.style" not in worker):
        raise ValueError("Unexpected local engine source; manual review required")
    return LocalVoiceAudit(
        stt_engine="sherpa-onnx Zipformer (existing)",
        tts_engine="Supertonic-3 natural_v1 (existing)",
        configured_stt_language="id (deployment/model provenance must be verified)",
        configured_tts_language="id (enforced by natural_v1)",
        multilingual_verified=False,
        expressive_native_verified=False,
        details=(
            "Source inspection is not model-weight or microphone validation.",
            "ZipformerState already incrementally decodes growing PCM prefixes.",
            "natural_v1 validates the Indonesian tokenizer and pinned style.",
            "Existing conditioning profiles are not proof of emotional voice control.",
            "Inspect installed model manifests and run actual language/audio benchmarks before enabling new languages.",
        ),
    )
