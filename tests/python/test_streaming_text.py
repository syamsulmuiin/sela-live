import unittest
from sela_live.audio.streaming_text import SpeechSegmentBuffer


class StreamingTextTests(unittest.TestCase):
    def test_partial_sentence_waits(self):
        b = SpeechSegmentBuffer(min_segment=8)
        self.assertEqual(b.feed("Halo, apa"), [])
        self.assertEqual(b.feed(" kabar?"), ["Halo, apa kabar?"])
        self.assertEqual(b.finish(), [])

    def test_preserves_source_exactly(self):
        b = SpeechSegmentBuffer(min_segment=8)
        original = "Hello there.  Apa kabar? Next fragment"
        output = b.feed(original[:14]) + b.feed(original[14:]) + b.finish()
        self.assertEqual("".join(output), original)

    def test_cancel_discards_unspoken_tail(self):
        b = SpeechSegmentBuffer(min_segment=8)
        b.feed("Incomplete")
        b.cancel()
        self.assertEqual(b.finish(), [])
        with self.assertRaises(ValueError):
            b.feed("new")

    def test_bounded_and_control_rejection(self):
        b = SpeechSegmentBuffer(max_buffer=30, min_segment=5, max_segment=20)
        with self.assertRaises(ValueError):
            b.feed("x" * 31)
        with self.assertRaises(ValueError):
            b.feed("bad\x00")

    def test_unicode_boundaries(self):
        b = SpeechSegmentBuffer(min_segment=3)
        self.assertEqual(b.feed("你好。"), ["你好。"])


if __name__ == "__main__":
    unittest.main()
