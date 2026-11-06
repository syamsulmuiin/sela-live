"""Inference-free safety regression for the isolated multilingual runtime wrapper."""
import unittest
from sela_live.audio.multilingual_candidate import controlled_multilingual_runtime


class FakeGuard:
    class Runtime:
        def __init__(self, root, manifest, selected_profile):
            self.style = object()
            self.engine = type("Engine", (), {})()
            self.engine.synthesize = lambda *a, **k: None

        def synthesize(self, request):
            return self.engine.synthesize(
                request["text"], voice_style=self.style, lang="id",
                total_steps=8, verbose=False, speed=1.05
            )


class CandidateSafetyTests(unittest.TestCase):
    def test_profile_rejected_before_loading(self):
        candidate = controlled_multilingual_runtime(FakeGuard, lambda text, limit: [text])
        with self.assertRaises(ValueError):
            candidate(None, None, "original")

    def test_rejected_request_restores_engine_method(self):
        candidate = controlled_multilingual_runtime(FakeGuard, lambda text, limit: [text])
        worker = candidate(None, None)
        previous = worker.engine.synthesize
        with self.assertRaises(ValueError):
            worker.synthesize({"text": "hello", "language": "de", "profile": "conditioning_b"})
        self.assertIs(worker.engine.synthesize, previous)

    def test_unexpected_field_rejected(self):
        candidate = controlled_multilingual_runtime(FakeGuard, lambda text, limit: [text])
        worker = candidate(None, None)
        with self.assertRaises(ValueError):
            worker.synthesize({"text": "hello", "language": "en",
                               "profile": "conditioning_b", "debug": True})


if __name__ == "__main__":
    unittest.main()
