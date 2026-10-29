"""Offline layout and immutable migration inventory contract (no app imports)."""
import ast
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestLayout(unittest.TestCase):
    def test_only_structured_test_locations(self):
        self.assertEqual(list(ROOT.glob('test_*.py')), [])
        self.assertEqual(list((ROOT / 'web').glob('*.test.cjs')), [])
        self.assertFalse((ROOT / 'tests_setup').exists())
        self.assertFalse((ROOT / 'tests/unit').exists())
        self.assertFalse((ROOT / 'tests/layout').exists())
        for directory in ('python', 'web', 'setup'):
            self.assertTrue((ROOT / 'tests' / directory).is_dir(), directory)
        self.assertTrue((ROOT / 'tests/setup/check_install_wheels.py').is_file())
        self.assertFalse((ROOT / 'tests/setup/test_install_wheels.py').exists())

    def test_exact_inventory_and_preserved_test_source(self):
        inventory = json.loads((ROOT / 'tests/move-inventory.json').read_text())
        # The original migration receipt is immutable; additions are not migrations.
        additions = json.loads((ROOT / 'tests/additions-inventory.json').read_text())
        self.assertEqual(additions, [{
            'path': 'tests/python/test_audio_continuity.py',
            'sha256': '462c5d5c6db496a672bed5be280fd8ad8f01ff6cec7bf680d09c54092b848bf2',
        }])
        for row in additions:
            path = ROOT / row['path']
            self.assertFalse(path.is_symlink())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row['sha256'])
        expected = {row['destination'] for row in inventory} | {row['path'] for row in additions}
        actual = {str(p.relative_to(ROOT)) for directory, pattern in
                  (('python', 'test_*.py'), ('web', '*.test.cjs'), ('setup', '*.py'))
                  for p in (ROOT / 'tests' / directory).glob(pattern)
                  if p.name not in ('__init__.py', 'test_layout.py')}
        self.assertEqual(actual, expected)
        for row in inventory:
            with self.subTest(source=row['source']):
                self.assertFalse((ROOT / row['source']).exists())
                source = (ROOT / row['destination']).read_bytes()
                self.assertEqual(hashlib.sha256(source).hexdigest(), row['destination_sha256'])
                if row['destination'].endswith('.py'):
                    tree = ast.parse(source)
                    functions = sorted(node.name for node in ast.walk(tree)
                                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                       and node.name.startswith('test_'))
                    self.assertEqual(functions, row['test_functions'])

    def test_discovery_packages_and_shipping_targets(self):
        for path in ('tests', 'tests/python', 'tests/setup'):
            self.assertTrue((ROOT / path / '__init__.py').is_file(), path)
        self.assertTrue((ROOT / 'voice_app/server.py').is_file())
        self.assertTrue((ROOT / 'web/app.js').is_file())
        self.assertFalse((ROOT / 'tests/python/voice_app').exists())
        self.assertFalse((ROOT / 'tests/web/app.js').exists())


if __name__ == '__main__':
    unittest.main()
