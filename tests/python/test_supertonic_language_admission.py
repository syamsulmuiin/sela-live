import unittest

from sela_live.audio.supertonic_language_admission import (
    inspect_language, require_audited_synthesis,
)


class SupertonicLanguageAdmissionTests(unittest.TestCase):
    def test_audited_languages_do_not_imply_production_approval(self):
        for language in ("en", "ja"):
            result = inspect_language(language)
            self.assertTrue(result.native_declared)
            self.assertTrue(result.synthesized_in_audit)
            self.assertFalse(result.production_approved)
            self.assertEqual(require_audited_synthesis(language), language)

    def test_indonesian_baseline_remains_approved(self):
        self.assertTrue(inspect_language("id").production_approved)

    def test_declared_language_is_not_automatically_audited(self):
        result = inspect_language("de")
        self.assertTrue(result.native_declared)
        self.assertFalse(result.synthesized_in_audit)
        with self.assertRaises(ValueError):
            require_audited_synthesis("de")

    def test_unknown_and_auto_are_rejected(self):
        for language in ("auto", "xx", "", "EN", "<id>"):
            with self.assertRaises(ValueError):
                require_audited_synthesis(language)


if __name__ == "__main__":
    unittest.main()
