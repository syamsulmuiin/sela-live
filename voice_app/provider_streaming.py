"""Optional streaming adapter for provider output.

The existing delivery JSON envelope must be validated in full before any text
can be spoken. Raw plain-text providers may emit sentence segments early.
"""
from sela_live.audio.streaming_text import SpeechSegmentBuffer


async def stream_plain_speech(provider, messages, *, min_segment=24):
    """Yield source-preserving segments from a plain-text LLM provider.

    Do not use with structured delivery envelopes: incomplete JSON is not
    trusted speech text. Existing production collection remains unchanged.
    """
    if getattr(provider, "delivery_protocol_version", None):
        raise ValueError("Structured delivery requires complete envelope validation")
    buffer = SpeechSegmentBuffer(min_segment=min_segment)
    try:
        async for delta in provider.stream(messages):
            for segment in buffer.feed(delta):
                yield segment
        for segment in buffer.finish():
            yield segment
    finally:
        buffer.cancel()
