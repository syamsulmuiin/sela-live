import io
import unittest
import wave

try:
    from voice_app import audio
except ImportError:
    audio = None


class AudioTests(unittest.TestCase):
    def test_invalid_pcm_is_rejected(self):
        for pcm in (b'', b'x', 'not bytes', None, b'\0\0' * 320001):
            with self.subTest(pcm_type=type(pcm).__name__), self.assertRaises(ValueError):
                audio.pcm16_wav(pcm, 16000)
        for rate in (True, '16000', 0, 48000):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                audio.pcm16_wav(b'\0\0', rate)

    def test_pcm_becomes_canonical_wav(self):
        self.assertIsNotNone(audio, 'PCM conversion is not implemented')
        pcm = b'\x00\x00' * 8000
        result = audio.pcm16_wav(pcm, 16000)
        with wave.open(io.BytesIO(result), 'rb') as f:
            self.assertEqual((f.getnchannels(), f.getsampwidth(), f.getframerate()), (1, 2, 16000))
            self.assertEqual(f.getnframes(), 8000)
            self.assertEqual(f.readframes(8000), pcm)


if __name__ == '__main__':
    unittest.main()
