"""Offline language admission for the existing local Supertonic-3 model.

This is a development-only contract. It does not modify natural_v1, enable a
production language, infer spoken language, or bypass the pinned TTS guard.
"""
from dataclasses import dataclass

from sela_live.audio.language_contract import normalize_language

# Native language tags declared by the installed Supertonic-3 SDK/model.
# A declaration is not a pronunciation or listening-quality acceptance.
NATIVE_LANGUAGES = frozenset(
    "en ko ja ar bg cs da de el es et fi fr hi hr hu id it lt lv nl pl pt ro ru sk sl sv tr uk vi".split()
)
# Audited local SDK synthesis produced nonempty, unclipped WAVs for these tags.
# This is NOT a subjective naturalness or production approval.
ACOUSTICALLY_SYNTHESIZED = frozenset(("id", "en", "ja"))
PRODUCTION_APPROVED = frozenset(("id",))


@dataclass(frozen=True)
class LanguageAdmission:
    language: str
    native_declared: bool
    synthesized_in_audit: bool
    production_approved: bool


def inspect_language(language: str) -> LanguageAdmission:
    language = normalize_language(language)
    if language == "auto":
        raise ValueError("TTS language must be explicit")
    return LanguageAdmission(
        language=language,
        native_declared=language in NATIVE_LANGUAGES,
        synthesized_in_audit=language in ACOUSTICALLY_SYNTHESIZED,
        production_approved=language in PRODUCTION_APPROVED,
    )


def require_audited_synthesis(language: str) -> str:
    """Allow isolated candidate testing, never authorize live synthesis."""
    result = inspect_language(language)
    if not result.native_declared or not result.synthesized_in_audit:
        raise ValueError("Language has no successful local synthesis audit")
    return result.language
