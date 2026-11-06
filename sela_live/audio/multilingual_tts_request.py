"""Strict opt-in request contract for isolated multilingual Supertonic trials.

No worker or production wiring. The existing natural_v1 request remains text-only.
"""
from dataclasses import dataclass

from sela_live.audio.supertonic_language_admission import require_audited_synthesis
from sela_live.audio.tts import validate_text


@dataclass(frozen=True)
class MultilingualTTSRequest:
    text: str
    language: str
    profile: str = "conditioning_b"

    def __post_init__(self):
        validate_text(self.text)
        object.__setattr__(self, "language", require_audited_synthesis(self.language))
        if self.profile != "conditioning_b":
            raise ValueError("Only audited B profile is eligible for multilingual trials")


def parse_multilingual_request(payload):
    if type(payload) is not dict or set(payload) != {"text", "language", "profile"}:
        raise ValueError("Explicit text, language, and profile required")
    if any(type(payload[k]) is not str for k in payload):
        raise ValueError("String fields required")
    return MultilingualTTSRequest(**payload)
