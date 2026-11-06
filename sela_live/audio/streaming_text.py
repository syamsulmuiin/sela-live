"""Bounded, provider-neutral streaming text segmentation.

Only committed text is eligible for speech. The caller retains the authoritative
LLM response, playback ACK, cancellation and generation-token checks.
"""
import re

_BOUNDARY = re.compile(r'[.!?。！？]+(?:["\u201d\u2019])?(?=\s|$)')


class SpeechSegmentBuffer:
    def __init__(self, *, max_buffer=4000, min_segment=24, max_segment=180):
        if (type(max_buffer) is not int or type(min_segment) is not int
                or type(max_segment) is not int or not 1 <= min_segment <= max_segment <= 800
                or max_segment > max_buffer or max_buffer > 4000):
            raise ValueError("Invalid segmentation bounds")
        self.max_buffer = max_buffer
        self.min_segment = min_segment
        self.max_segment = max_segment
        self._buffer = ""
        self._closed = False

    def feed(self, delta: str) -> list[str]:
        if self._closed or type(delta) is not str:
            raise ValueError("Invalid stream delta")
        if not delta:
            return []
        if len(self._buffer) + len(delta) > self.max_buffer:
            raise ValueError("Stream buffer limit")
        if any(ord(c) < 32 and c not in "\n\t" for c in delta):
            raise ValueError("Invalid text control")
        self._buffer += delta
        return self._take_ready()

    def _take_ready(self) -> list[str]:
        parts = []
        while len(self._buffer) >= self.min_segment:
            boundaries = [m.end() for m in _BOUNDARY.finditer(self._buffer)
                          if self.min_segment <= m.end() <= self.max_segment]
            if not boundaries:
                break
            end = boundaries[-1]
            parts.append(self._buffer[:end])
            self._buffer = self._buffer[end:]
        return parts

    def finish(self) -> list[str]:
        if self._closed:
            return []
        parts = self._take_ready()
        if self._buffer:
            parts.append(self._buffer)
        self._buffer = ""
        self._closed = True
        return parts

    def cancel(self) -> None:
        self._buffer = ""
        self._closed = True
