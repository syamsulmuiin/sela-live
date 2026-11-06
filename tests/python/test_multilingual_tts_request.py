import unittest

from sela_live.audio.multilingual_tts_request import parse_multilingual_request


class MultilingualTTSRequestTests(unittest.TestCase):
    def test_audited_languages(self):
        for lang in ("id", "en", "ja"):
            result = parse_multilingual_request({
                "text": "Hello Sela.", "language": lang, "profile": "conditioning_b"
            })
            self.assertEqual(result.language, lang)

    def test_unverified_languages_rejected(self):
        for lang in ("de", "auto", "xx"):
            with self.assertRaises(ValueError):
                parse_multilingual_request({
                    "text": "Hello", "language": lang, "profile": "conditioning_b"
                })

    def test_extra_fields_and_missing_language_rejected(self):
        for payload in (
            {"text": "Hello", "profile": "conditioning_b"},
            {"text": "Hello", "language": "en", "profile": "conditioning_b", "speed": 2},
        ):
            with self.assertRaises(ValueError):
                parse_multilingual_request(payload)

    def test_tags_and_unreviewed_profiles_rejected(self):
        for text, profile in (("<laugh>Hello", "conditioning_b"), ("Hello", "original")):
            with self.assertRaises(ValueError):
                parse_multilingual_request({
                    "text": text, "language": "en", "profile": profile
                })


if __name__ == "__main__":
    unittest.main()
