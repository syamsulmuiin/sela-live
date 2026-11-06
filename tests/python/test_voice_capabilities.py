import unittest
from sela_live.audio.language_contract import SpokenSegment
from sela_live.audio.voice_capabilities import (
    VoiceCapabilities, require_speech_support, ZIPFORMER_ID, SUPERTONIC_NATURAL_ID
)


class VoiceCapabilityTests(unittest.TestCase):
    def test_baseline_id_only(self):
        self.assertTrue(ZIPFORMER_ID.accepts("id"))
        self.assertFalse(ZIPFORMER_ID.accepts("en"))
        self.assertFalse(SUPERTONIC_NATURAL_ID.accepts("fr", "warm"))

    def test_opt_in_multilingual_style(self):
        caps = VoiceCapabilities(("id", "en", "fr"), ("default", "warm"))
        self.assertEqual(require_speech_support(
            SpokenSegment("turn", "Bonjour", "fr", "warm"), caps).text, "Bonjour")
        with self.assertRaises(ValueError):
            require_speech_support(SpokenSegment("turn", "Hallo", "de"), caps)

    def test_reject_invalid_capabilities(self):
        with self.assertRaises(ValueError):
            VoiceCapabilities(("auto",))
        with self.assertRaises(ValueError):
            VoiceCapabilities(("id", "id"))


if __name__ == "__main__":
    unittest.main()
