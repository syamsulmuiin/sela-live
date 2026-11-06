"""Inference-free contract tests; no production model or service required."""
import unittest
from sela_live.audio.language_contract import SpeechTurn, SpokenSegment, normalize_language


class LanguageContractTests(unittest.TestCase):
    def test_tags(self):
        self.assertEqual(normalize_language("ID-id"), "id-id")
        self.assertEqual(normalize_language("en-US"), "en-us")
        self.assertEqual(normalize_language("auto"), "auto")
        for value in ("", "../id", "id;rm", " id", "a", "id_" ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_language(value)

    def test_final_turn(self):
        turn = SpeechTurn("session", "turn", "Halo, hello!", "id")
        self.assertEqual(turn.language, "id")
        with self.assertRaises(ValueError):
            SpeechTurn("session", "turn", " ")
        with self.assertRaises(ValueError):
            SpeechTurn("session", "turn", "x\x00")

    def test_spoken_segment(self):
        segment = SpokenSegment("turn", "Bonjour", "fr", "warm")
        self.assertEqual(segment.style, "warm")
        with self.assertRaises(ValueError):
            SpokenSegment("turn", "x" * 801, "fr")
        with self.assertRaises(ValueError):
            SpokenSegment("turn", "Bonjour", "fr", "<emotion>")


if __name__ == "__main__":
    unittest.main()
