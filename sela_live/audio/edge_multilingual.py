"""Opt-in multilingual neural TTS using installed edge-tts service.

Unlike local Supertonic this requires network access and sends text to the
speech service. It must never silently replace the offline production engine.
"""
import re
from sela_live.audio.language_contract import SpokenSegment
from sela_live.audio.voice_capabilities import VoiceCapabilities, require_speech_support

_VOICES = {
    "id": "id-ID-GadisNeural",
    "en": "en-US-AriaNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "ja": "ja-JP-NanamiNeural",
    "ar": "ar-SA-ZariyahNeural",
}
_STYLES = {"default": ("+0%", "+0%"), "warm": ("-5%", "+0%"),
           "energetic": ("+8%", "+5%"), "calm": ("-10%", "-5%")}


class EdgeMultilingualTTS:
    capabilities = VoiceCapabilities(tuple(_VOICES), tuple(_STYLES), streaming=True)

    async def synthesize_mp3(self, segment: SpokenSegment) -> bytes:
        require_speech_support(segment, self.capabilities)
        import edge_tts
        rate, pitch = _STYLES[segment.style]
        client = edge_tts.Communicate(
            segment.text, voice=_VOICES[segment.language], rate=rate, pitch=pitch)
        chunks = []
        size = 0
        async for item in client.stream():
            if item.get("type") != "audio":
                continue
            data = item.get("data")
            if type(data) is not bytes:
                raise ValueError("Invalid TTS audio frame")
            size += len(data)
            if size > 2_000_000:
                raise ValueError("TTS audio limit")
            chunks.append(data)
        if not chunks:
            raise ValueError("Empty TTS audio")
        return b"".join(chunks)
