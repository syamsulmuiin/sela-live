"""Protocol capacity and registry boundary regression tests; no inference."""
import json
import tempfile
import unittest
from pathlib import Path


class DeliveryBoundaryTests(unittest.TestCase):
    def test_full_length_astral_text_survives_json_escaping(self):
        from voice_app.delivery import parse_reply
        literal = '\U0001f642' * 4000
        raw = json.dumps({'text': literal, 'delivery': {'emotion': 'neutral', 'intensity': 'low'}})
        self.assertEqual(parse_reply(raw).text, literal)

    def test_registry_numeric_boundaries_at_all_intensities(self):
        from voice_app.delivery import load_registry
        base = json.loads((Path(__file__).parent / 'voice_app/delivery_registry.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'registry.json'
            for speed in (0.95, 1.08):
                for silence in (0.24, 0.36):
                    base['entries']['boundary'] = {'speed': speed, 'silence_duration': silence,
                                                  'support_level': 'prosody_only_experimental'}
                    path.write_text(json.dumps(base))
                    registry = load_registry(path)
                    for intensity in ('low', 'medium', 'high'):
                        controls = registry.resolve('boundary', intensity).native_controls()
                        self.assertTrue(0.9 <= controls['speed'] <= 1.1)
                        self.assertTrue(0.1 <= controls['silence_duration'] <= 0.5)


if __name__ == '__main__':
    unittest.main()
