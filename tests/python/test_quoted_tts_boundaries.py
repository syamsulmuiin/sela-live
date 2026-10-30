"""Generic delivery boundaries; never edit AI text or select AI emotions."""
import unittest
from voice_app.tts_chunks import source_parts, synthesis_source


class QuotedBoundaryTests(unittest.TestCase):
    def test_closed_dialogue_is_a_boundary_before_length_fallback(self):
        # Different content/closing delimiters exercise the general rule, not a
        # fixed phrase or a hand-authored partition of a production reply.
        for ending in ('.”', '."', '.’', ".'", '.)', '.]', '.»', '.”)'):
            with self.subTest(ending=ending):
                first = 'Narator menjelaskan bahwa semua peserta sudah siap mendengarkan. '
                dialogue = 'Lalu ia berkata, “Kita akan berangkat sekarang' + ending + ' '
                tail = 'Sesudah itu rombongan melanjutkan perjalanan menuju rumah nenek bersama-sama.'
                text = first + dialogue + tail
                parts = source_parts(text, lambda value: value)
                self.assertEqual(parts, [first + dialogue, tail])
                self.assertEqual(''.join(parts), text)
                self.assertTrue(all(len(synthesis_source(p)) <= 180 for p in parts))


    def test_overflow_prefers_existing_clause_boundary(self):
        for mark in (',', ':', ';'):
            with self.subTest(mark=mark):
                clause = ('Pembicara menjelaskan keadaan kepada semua peserta yang sedang menunggu kabar di ruang utama' + mark + ' ')
                tail = 'sesudah itu mereka melanjutkan perjalanan bersama keluarga menuju rumah nenek di desa sebelah.'
                text = clause + tail
                parts = source_parts(text, lambda value: value)
                self.assertEqual(parts, [clause, tail])
                self.assertEqual(''.join(parts), text)


    def test_fitting_utterance_is_not_split_inside_quote_attribution_or_title(self):
        prefix = 'Narator menyampaikan penjelasan panjang kepada peserta pertemuan sebelum memperkenalkan '
        for suffix in ('sebutan “Dr.” Smith tiba.', 'ucapan “Jangan pergi!” katanya pelan.'):
            with self.subTest(suffix=suffix):
                text = prefix + suffix
                self.assertLessEqual(len(text), 180)
                self.assertEqual(source_parts(text, lambda value: value), [text])


if __name__ == '__main__':
    unittest.main()
