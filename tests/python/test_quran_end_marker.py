"""Portable speech-copy regression tests; no SDK, models or inference needed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from voice_app.speech import normalize_pronunciation


class QuranEndMarkerTests(unittest.TestCase):
    def test_end_of_ayah_becomes_a_supported_sentence_boundary(self):
        self.assertEqual(normalize_pronunciation('رَحِيمٌ ۝١'), 'رَحِيمٌ .١')

    def test_verse_digits_keep_their_script_value_and_order(self):
        for digits in ('0', '1', '12', '123', '001', '٠', '١', '١٢', '١٢٣', '۰', '۱', '۱۲', '۱۲۳'):
            with self.subTest(digits=digits):
                self.assertEqual(normalize_pronunciation('۝' + digits), '.' + digits)

    def test_arabic_letters_and_vowel_marks_are_unchanged(self):
        source = 'اَ بُ تِ ثْ جّ حً خٌ دٍ اللَّهِ الرَّحْمَٰنِ ۝١\nرَحِيمٌ ۝٢'
        self.assertEqual(normalize_pronunciation(source), source.replace('۝', '.'))

    def test_normalization_is_idempotent(self):
        for source in ('رَحِيمٌ ۝١', '۝12 ۝۱۲ ۝١٢', '﴿رَحِيمٌ ۝١﴾', 'API Rp12 2026-11-01 ۝١'):
            with self.subTest(source=source):
                speech = normalize_pronunciation(source)
                self.assertEqual(normalize_pronunciation(speech), speech)

    def test_source_bytes_are_not_mutated(self):
        source = '﴿رَحِيمٌ ۝١﴾'
        before = source.encode('utf-8')
        self.assertEqual(normalize_pronunciation(source), '(رَحِيمٌ .١)')
        self.assertEqual(source.encode('utf-8'), before)
        self.assertEqual(source, '﴿رَحِيمٌ ۝١﴾')

    def test_marker_mapping_is_generic_not_phrase_specific(self):
        for source, expected in (('۝', '.'), ('x۝y', 'x.y'), ('۝۝', '..'),
                                 ('۝١۝٢', '.١.٢'), ('﴾۝﴿', ').('),
                                 ('عَرَبِيّ ۞ ۩', 'عَرَبِيّ ۞ ۩')):
            with self.subTest(source=source):
                self.assertEqual(normalize_pronunciation(source), expected)

    def test_existing_normalization_and_ornaments_are_retained(self):
        cases = (
            ('﴿١﴾', '(١)'),
            ('API Codex 12', 'a pe i kodeks dua belas'),
            ('Rp1.250,50', 'seribu dua ratus lima puluh rupiah koma lima nol'),
            ('2026-11-01', 'satu November dua ribu dua puluh enam'),
            ('007 /path/12 v1.2 name_12', '007 /path/12 v1.2 name_12'),
            ('API 12 ۝12', 'a pe i dua belas .12'),
            ('', ''),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(normalize_pronunciation(source), expected)

    def test_representative_speech_copy_has_no_incompatible_markers(self):
        source = '﴿رَحِيمٌ ۝١﴾\nعَرَبِيّ ۝٢'
        speech = normalize_pronunciation(source)
        self.assertEqual(speech, '(رَحِيمٌ .١)\nعَرَبِيّ .٢')
        for marker in ('\u06dd', '\ufd3f', '\ufd3e'):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, speech)


if __name__ == '__main__':
    unittest.main()
