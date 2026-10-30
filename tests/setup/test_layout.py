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
        self.assertEqual(additions, [
            {'path': 'tests/python/test_audio_continuity.py',
             'sha256': '462c5d5c6db496a672bed5be280fd8ad8f01ff6cec7bf680d09c54092b848bf2'},
            {'path': 'tests/python/test_quoted_tts_boundaries.py',
             'sha256': '9b69a725e65fa5dbbadaa0b2e1eed85775f618d28c36a4bf2d79dffe7013de20'},
            {'path': 'tests/python/test_sela_live_tts.py',
             'sha256': '16590f006f791d08554021f337f31d9d5b4c8659c9328e3d1acb38db69e4afaa'},
            {'path': 'tests/python/test_sela_live_admission.py',
             'sha256': '63c8e33847f97db1b21d8ed8c95e5038d785702af32110b06ce7b66f7a54a715'},
            {'path': 'tests/python/test_sela_live_probe.py',
             'sha256': '824890e1b393e46b044487b4435e01c0e8e6839639191b055bba2773dbe94f70'},
            {'path': 'tests/python/test_sela_live_integration.py',
             'sha256': '8a5782239ec29fdac3025bd4d8c0704f4325099ac0161d87eda5a6980b7b8724'},
            {'path': 'tests/setup/test_live_runtime_package.py',
             'sha256': '892b471a65f284e2bdd9090764c5f972eeba89f7b8e2039b97b4c02fd6713bcf'},
            {'path': 'tests/setup/test_live_tts_install.py',
             'sha256': '4f7927efc04124229cee5ea29c177b446f31dcbaf96bf39b0a9f7d8cd5c33779'},
        ])
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
