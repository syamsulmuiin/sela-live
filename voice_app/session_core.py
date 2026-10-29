# Copied from /root/claude-voice-core/voice_core.py; original unchanged.
"""Provider-independent session control; no external integrations."""
from collections import deque
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(frozen=True)
class ResponseToken:
    conversation_id: str
    epoch: int
    generation: int
    provider: str
    session_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        for name in ('conversation_id', 'provider', 'session_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(name + ' must be a nonempty string')
        for name in ('epoch', 'generation'):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(name + ' must be a nonnegative integer')


@dataclass
class Turn:
    role: str
    text: str
    status: str
    source: str
    heard_text: str = ''


@dataclass(frozen=True)
class AudioChunk:
    chunk_id: int
    data: bytes
    text: str


@dataclass
class Session:
    conversation_id: str = 'conversation-1'
    provider: str = 'manual'
    max_audio_bytes: int = 65536
    _instance_id: str = field(default_factory=lambda: uuid4().hex, init=False, repr=False)
    _queue: deque[AudioChunk] = field(default_factory=deque, init=False, repr=False)
    _chunk_sequence: int = field(default=0, init=False)
    _inflight: AudioChunk | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.max_audio_bytes) is not int or self.max_audio_bytes <= 0:
            raise ValueError('max_audio_bytes must be a positive integer')
        for name in ('conversation_id', 'provider'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(name + ' must be a nonempty string')

    @property
    def queued_audio_bytes(self) -> int:
        return sum(len(chunk.data) for chunk in self._queue)

    @property
    def buffered_audio_bytes(self) -> int:
        return self.queued_audio_bytes + (len(self._inflight.data) if self._inflight else 0)

    @property
    def speaking(self) -> bool:
        return self._inflight is not None

    history: list[Turn] = field(default_factory=list, init=False)
    active: ResponseToken | None = field(default=None, init=False)
    epoch: int = field(default=0, init=False)
    generation: int = field(default=0, init=False)
    open: bool = field(default=False, init=False)

    @property
    def responding(self) -> bool:
        return self.active is not None and self.history[-1].status == 'streaming'

    mode: str = 'hands_free'
    muted: bool = False
    pressed: bool = False

    @property
    def mic_active(self) -> bool:
        return self.open and not self.muted and (self.mode == 'hands_free' or self.pressed)

    def _require_open(self) -> None:
        if not self.open:
            raise ValueError('session closed')

    def start(self, mode: str = 'hands_free') -> None:
        if self.open:
            raise ValueError('session already open')
        if mode not in ('hands_free', 'push_to_talk'):
            raise ValueError('invalid input mode')
        self.mode, self.muted, self.pressed = mode, False, False
        self.open = True
        self.epoch += 1

    def end(self) -> None:
        self._require_open()
        self.interrupt()
        self.open = False
        self.pressed = False

    def submit(self, text: str, source: str = 'text') -> ResponseToken:
        self._require_open()
        if not isinstance(text, str) or not text.strip():
            raise ValueError('user text must be a nonempty string')
        if source not in ('text', 'voice'):
            raise ValueError('source must be text or voice (final transcript)')
        self.interrupt()
        self.generation += 1
        self.active = ResponseToken(self.conversation_id, self.epoch, self.generation, self.provider, self._instance_id)
        self.history.append(Turn('user', text, 'complete', source))
        self.history.append(Turn('assistant', '', 'streaming', 'generated'))
        return self.active

    def interrupt(self) -> None:
        self._require_open()
        if self.active is not None and (self.responding or self.buffered_audio_bytes):
            self.history[-1].status = 'interrupted'
        self.active = None
        self._queue.clear()
        self._inflight = None

    def _matches(self, token: ResponseToken | None) -> bool:
        return self.open and token is not None and type(token) is ResponseToken and token == self.active

    def response_text(self, token: ResponseToken | None, text: str) -> bool:
        if not self._matches(token) or not self.responding:
            return False
        if not isinstance(text, str) or not text:
            raise ValueError('response text must be a nonempty string')
        self.history[-1].text += text
        return True

    def response_audio(self, token: ResponseToken | None, data: bytes, text: str = '') -> bool:
        if not self._matches(token) or not self.responding:
            return False
        if type(data) is not bytes or not data:
            raise ValueError('audio must be nonempty bytes')
        if not isinstance(text, str):
            raise ValueError('audio transcript must be a string')
        if self.buffered_audio_bytes + len(data) > self.max_audio_bytes:
            return False
        self._chunk_sequence += 1
        self._queue.append(AudioChunk(self._chunk_sequence, data, text))
        return True

    def finish(self, token: ResponseToken | None) -> bool:
        if not self._matches(token) or not self.responding:
            return False
        self.history[-1].status = 'complete'
        return True

    def drain_audio(self, token: ResponseToken | None) -> AudioChunk | None:
        if not self._matches(token) or self._inflight is not None or not self._queue:
            return None
        self._inflight = self._queue.popleft()
        return self._inflight

    def ack_played(self, token: ResponseToken | None, chunk_id: int) -> bool:
        if (not self._matches(token) or type(chunk_id) is not int
                or self._inflight is None or chunk_id != self._inflight.chunk_id):
            return False
        self.history[-1].heard_text += self._inflight.text
        self._inflight = None
        return True

    def select_provider(self, provider: str) -> None:
        self._require_open()
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError('provider must be a nonempty string')
        self.provider = provider

    def press(self) -> None:
        self._require_open()
        if not self.muted:
            self.pressed = True

    def release(self) -> None:
        self._require_open()
        self.pressed = False

    def set_mute(self, muted: bool) -> None:
        self._require_open()
        if type(muted) is not bool:
            raise ValueError('muted must be boolean')
        self.muted = muted
        self.pressed = False
