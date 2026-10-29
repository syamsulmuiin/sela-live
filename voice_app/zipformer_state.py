"""Single native Zipformer stream; no PCM retained between bounded calls."""
from array import array
import hashlib
import sys
import re


def validate_key(key):
    if not isinstance(key, str) or re.fullmatch(r'[0-9a-f]{32}', key) is None:
        raise ValueError('Invalid ASR stream key')


class ZipformerState:
    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.stream = None
        self.key = None
        self.count = 0
        self.digest = None
        self.stream_resets = 0

    def _feed(self, pcm):
        # Small stdlib buffers also work with sherpa's numpy forcecast binding.
        # Avoid requiring the native numpy dependency in the application runtime.
        for offset in range(0, len(pcm), 2560):
            samples = array('h', pcm[offset:offset + 2560])
            if sys.byteorder != 'little':
                samples.byteswap()
            self.stream.accept_waveform(16000, array('f', (n / 32768.0 for n in samples)))
            self._drain()

    def _drain(self):
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)

    def forget(self, key):
        validate_key(key)
        if self.stream is None or self.key != key:
            return False
        self.recognizer.reset(self.stream)
        self.stream_resets += 1
        self.stream = self.key = self.digest = None
        self.count = 0
        return True

    def update(self, key, pcm, *, final=False):
        validate_key(key)
        if type(pcm) is not bytes or not 0 < len(pcm) <= 640000 or len(pcm) % 2:
            raise ValueError('Invalid ASR PCM')
        if self.key == key:
            if len(pcm) < self.count * 2 or hashlib.sha256(pcm[:self.count * 2]).digest() != self.digest:
                raise ValueError('ASR prefix must grow without mutation')
        elif self.stream is not None:
            self.forget(self.key)
        if self.stream is None:
            self.stream = self.recognizer.create_stream()
            self.key = key
        new_samples = len(pcm) // 2 - self.count
        self._feed(pcm[self.count * 2:])
        self.count = len(pcm) // 2
        self.digest = hashlib.sha256(pcm).digest()
        source_samples = self.count
        if final:
            self._feed(b'\0\0' * 25600)
            self.stream.input_finished()
            self._drain()
        text = self.recognizer.get_result(self.stream)
        if final:
            self.recognizer.reset(self.stream)
            self.stream_resets += 1
            self.stream = self.key = self.digest = None
            self.count = 0
        return {'text': text, 'metrics': {'prefix_redecode': False,
                'new_samples_fed': new_samples, 'source_samples_fed': source_samples,
                'stream_resets': self.stream_resets}}
