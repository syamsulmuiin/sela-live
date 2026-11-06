"""Provider-neutral language and voice-turn contracts for future Sela Live adapters.

This module does not select models, synthesize audio, or change the installed
Indonesian STT/TTS path. A language must be confirmed by an adapter before use.
"""
from dataclasses import dataclass
import re

_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,8}){0,2}$")
_MAX_TEXT = 4000


def normalize_language(value: str) -> str:
    """Normalize explicit BCP-47-like language tags; 'auto' means undetermined."""
    if type(value) is not str or not value or len(value) > 35:
        raise ValueError("Invalid language tag")
    if value == "auto":
        return value
    if not _LANGUAGE.fullmatch(value):
        raise ValueError("Invalid language tag")
    parts = value.split("-")
    return "-".join([parts[0].lower(), *[part.lower() for part in parts[1:]]])


@dataclass(frozen=True)
class SpeechTurn:
    """Final STT text accepted for one LLM turn.

    Language is descriptive metadata, not proof that an ASR/TTS model supports
    it. Partial transcripts must not create independent LLM turns.
    """
    session_id: str
    turn_id: str
    text: str
    language: str = "auto"

    def __post_init__(self):
        if any(type(v) is not str or not v or len(v) > 128
               for v in (self.session_id, self.turn_id)):
            raise ValueError("Invalid turn identity")
        if (type(self.text) is not str or not self.text.strip()
                or len(self.text) > _MAX_TEXT
                or any(ord(c) < 32 and c not in "\n\t" for c in self.text)):
            raise ValueError("Invalid final transcript")
        object.__setattr__(self, "language", normalize_language(self.language))


@dataclass(frozen=True)
class SpokenSegment:
    """Text-to-speech request metadata; no generated or inferred emotions."""
    turn_id: str
    text: str
    language: str
    style: str = "default"

    def __post_init__(self):
        if type(self.turn_id) is not str or not self.turn_id or len(self.turn_id) > 128:
            raise ValueError("Invalid turn identity")
        if (type(self.text) is not str or not self.text.strip()
                or len(self.text) > 800
                or len(self.text.encode("utf-8")) > 3200
                or any(ord(c) < 32 and c not in "\n\t" for c in self.text)):
            raise ValueError("Invalid speech text")
        object.__setattr__(self, "language", normalize_language(self.language))
        if type(self.style) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", self.style):
            raise ValueError("Invalid style")
