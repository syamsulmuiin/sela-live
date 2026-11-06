import unittest
from pathlib import Path
from sela_live.audio.local_voice_audit import audit_source


class LocalVoiceAuditTests(unittest.TestCase):
    def test_existing_source_constraints(self):
        root = Path(__file__).resolve().parents[2]
        report = audit_source(root)
        self.assertFalse(report.multilingual_verified)
        self.assertFalse(report.expressive_native_verified)
        self.assertIn("Zipformer", report.stt_engine)
        self.assertIn("Supertonic", report.tts_engine)


if __name__ == "__main__":
    unittest.main()
