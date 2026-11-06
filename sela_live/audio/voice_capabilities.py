"""Opt-in STT/TTS capability negotiation, independent of Hermes and models.

A model adapter must explicitly advertise supported languages; this module
does not claim the installed Indonesian models are multilingual.
"""
from dataclasses import dataclass
from sela_live.audio.language_contract import normalize_language, SpokenSegment


@dataclass(frozen=True)
class VoiceCapabilities:
    languages: tuple[str, ...]
    styles: tuple[str, ...] = ("default",)
    streaming: bool = False

    def __post_init__(self):
        if (type(self.languages) is not tuple or not self.languages
                or len(self.languages) > 64 or any(normalize_language(x) == "auto" for x in self.languages)
                or len(set(self.languages)) != len(self.languages)):
            raise ValueError("Invalid language capabilities")
        for language in self.languages:
            if normalize_language(language) != language:
                raise ValueError("Capabilities must use normalized tags")
        if (type(self.styles) is not tuple or not self.styles
                or len(self.styles) > 32 or len(set(self.styles)) != len(self.styles)
                or any(type(x) is not str or not x or len(x) > 32
                       or not x.replace("_", "").replace("-", "").isalnum() for x in self.styles)):
            raise ValueError("Invalid style capabilities")
        if type(self.streaming) is not bool:
            raise ValueError("Invalid streaming flag")

    def accepts(self, language: str, style: str = "default") -> bool:
        return normalize_language(language) in self.languages and style in self.styles


def require_speech_support(segment: SpokenSegment, capabilities: VoiceCapabilities):
    if not isinstance(segment, SpokenSegment) or not isinstance(capabilities, VoiceCapabilities):
        raise TypeError("Expected speech segment and capabilities")
    if not capabilities.accepts(segment.language, segment.style):
        raise ValueError("Speech language/style unsupported by selected adapter")
    return segment


# Actual installed baseline; no multilingual claim.
ZIPFORMER_ID = VoiceCapabilities(("id",))
SUPERTONIC_NATURAL_ID = VoiceCapabilities(("id",))
