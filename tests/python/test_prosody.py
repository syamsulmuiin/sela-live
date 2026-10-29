"""Deterministic synthetic PCM fixtures, not human/emotion validation."""
import json
import unittest


class DiagnosticsTests(unittest.TestCase):
    def test_silence_is_finite_private_aggregate_with_unknown_emotion(self):
        from voice_app.prosody import diagnose_pcm16
        result = diagnose_pcm16(b'\0\0' * 16000, 16000)
        self.assertEqual(result, {
            'source': 'original_pcm', 'duration_seconds': 1.0,
            'rms_dbfs': None, 'peak_dbfs': None, 'clipping_fraction': 0.0,
            'silence_fraction': 1.0, 'non_silent_fraction': 0.0,
            'f0_hz': None, 'f0_status': 'not_evaluated',
            'emotion': None, 'emotion_status': 'not_evaluated',
            'warnings': ['low_level', 'mostly_silent'],
        })
        json.dumps(result, allow_nan=False)

    def test_clipping_and_short_capture_warnings_are_actionable(self):
        import struct
        from voice_app.prosody import diagnose_pcm16
        result = diagnose_pcm16(struct.pack('<hh', -32768, 32767) * 800, 16000)
        self.assertEqual(result['duration_seconds'], .1)
        self.assertEqual(result['clipping_fraction'], 1.0)
        self.assertEqual(result['peak_dbfs'], 0.0)
        self.assertEqual(result['warnings'], ['clipping', 'too_short'])

    def test_synthetic_periodic_pcm_energy_does_not_claim_pitch_or_emotion(self):
        import math
        import struct
        from voice_app.prosody import diagnose_pcm16
        pcm = b''.join(struct.pack('<h', round(8192 * math.sin(2 * math.pi * 200 * n / 16000)))
                       for n in range(16000))
        result = diagnose_pcm16(pcm, 16000)
        self.assertAlmostEqual(result['rms_dbfs'], -15.05, places=2)
        self.assertAlmostEqual(result['peak_dbfs'], -12.04, places=2)
        self.assertEqual(result['non_silent_fraction'], 1.0)
        self.assertEqual(result['warnings'], [])
        self.assertIsNone(result['emotion'])
        self.assertIsNone(result['f0_hz'])

    def test_partial_frames_are_sample_weighted_and_dc_is_not_speech(self):
        import struct
        from voice_app.prosody import diagnose_pcm16
        result = diagnose_pcm16(b'\0\0' * 320 + struct.pack('<h', 1000) * 80, 16000)
        self.assertEqual(result['silence_fraction'], .8)
        self.assertEqual(result['non_silent_fraction'], .2)
        self.assertNotIn('voiced_fraction', result)

    def test_input_bounds_do_not_expand_audio_protocol(self):
        from voice_app.prosody import diagnose_pcm16
        for pcm, rate in ((b'', 16000), (b'x', 16000), (b'\0\0' * 320001, 16000),
                          (b'\0\0', 48000), (bytearray(b'\0\0'), 16000), (b'\0\0', True)):
            with self.subTest(rate=rate, size=len(pcm)):
                with self.assertRaises(ValueError):
                    diagnose_pcm16(pcm, rate)
        self.assertEqual(diagnose_pcm16(b'\0\0' * 320000, 16000)['duration_seconds'], 20)


if __name__ == '__main__':
    unittest.main()
