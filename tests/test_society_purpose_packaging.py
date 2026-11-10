"""Public builds and invalid private inputs use synthetic data only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from tests.test_arena_legal import copy_build_context


class PurposePackagingTests(unittest.TestCase):
    def build(self, context, private_input=None):
        environ = dict(os.environ)
        environ.pop('KLADOS_SHARED_PURPOSE_BUILD_INPUT', None)
        if private_input is not None:
            environ['KLADOS_SHARED_PURPOSE_BUILD_INPUT'] = str(private_input)
        return subprocess.run([sys.executable, '-m', 'hatchling', 'build', '-t', 'wheel'],
                              cwd=context, env=environ, capture_output=True, text=True, timeout=60)

    def test_public_wheel_contains_empty_registry_and_no_private_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory) / 'context'
            copy_build_context(context)
            result = self.build(context)
            self.assertEqual(result.returncode, 0, result.stderr)
            with zipfile.ZipFile(next((context / 'dist').glob('*.whl'))) as wheel:
                registry = json.loads(wheel.read('config/shared-purposes-v1.json'))
                self.assertEqual(registry['purposes'], {})

    def test_invalid_private_build_input_stops_before_wheel_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = root / 'context'
            copy_build_context(context)
            source = root / 'synthetic.json'
            source.write_text(json.dumps({'schema_version': 1, 'purposes': {
                'building-a-society-v1': {'text': 'SYNTHETIC UNAPPROVED CANARY'}}}))
            for candidate in (source, root / 'absent.json', Path('relative.json')):
                with self.subTest(candidate=candidate.name):
                    result = self.build(context, candidate)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('SYNTHETIC UNAPPROVED CANARY', result.stderr)
                    self.assertFalse(list((context / 'dist').glob('*.whl')))
