"""Speech-copy normalization only; no native speech runtime is needed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from voice_app.speech import normalize_pronunciation


class ArabicPronunciationTests(unittest.TestCase):
    def test_quran_ornaments_follow_logical_input_order(self):
        self.assertEqual(normalize_pronunciation('\ufd3f١\ufd3e'), '(١)')

    def test_arabic_letters_vowel_marks_and_numerals_are_preserved(self):
        text = 'بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ ﴿١٢٣٤٥٦٧٨٩٠﴾'
        expected = 'بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ (١٢٣٤٥٦٧٨٩٠)'
        self.assertEqual(normalize_pronunciation(text), expected)
        plain = 'اَ بُ تِ ثْ جّ حً خٌ دٍ ٠١٢٣٤٥٦٧٨٩ ۰۱۲۳۴۵۶۷۸۹'
        self.assertEqual(normalize_pronunciation(plain), plain)

    def test_ornaments_are_generic_and_do_not_require_arabic_content(self):
        cases = (
            ('\ufd3f\ufd3e', '()'),
            ('\ufd3fhello\ufd3e', '(hello)'),
            ('x\ufd3fy\ufd3ez\ufd3f\ufd3e', 'x(y)z()'),
            ('\ufd3ehello\ufd3f', ')hello('),
            ('\ufd3f', '('),
            ('\ufd3e', ')'),
            ('(عَرَبِيّ) [١] ۞ ۩', '(عَرَبِيّ) [١] ۞ ۩'),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(normalize_pronunciation(text), expected)

    def test_callers_original_input_is_unchanged(self):
        original = '\ufd3fبِسْمِ اللَّهِ ١\ufd3e'
        before = original.encode('utf-8')
        spoken = normalize_pronunciation(original)
        self.assertEqual(original.encode('utf-8'), before)
        self.assertEqual(original, '\ufd3fبِسْمِ اللَّهِ ١\ufd3e')
        self.assertEqual(spoken, '(بِسْمِ اللَّهِ ١)')

    def test_existing_indonesian_normalization_is_unchanged(self):
        cases = (
            ('API Codex 12', 'a pe i kodeks dua belas'),
            ('Rp1.250,50', 'seribu dua ratus lima puluh rupiah koma lima nol'),
            ('2026-11-01', 'satu November dua ribu dua puluh enam'),
            ('007 /path/12 v1.2 name_12', '007 /path/12 v1.2 name_12'),
            ('', ''),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(normalize_pronunciation(text), expected)

    def test_ornaments_are_mapped_before_existing_normalization(self):
        self.assertEqual(
            normalize_pronunciation('\ufd3fAPI Rp12 2026-11-01\ufd3e'),
            '(a pe i dua belas rupiah satu November dua ribu dua puluh enam)',
        )


if __name__ == '__main__':
    unittest.main()
