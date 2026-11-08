"""Locked-resolution and isolated CI image checks; no registry push or deployment."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]


class BuildInputTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def capture(self, capfd):
        self.output_capture = capfd

    def test_reviewed_input_hashes_and_pinned_base(self):
        manifest = json.loads((ROOT / 'build-inputs.json').read_text())
        for name, expected in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_text(encoding='utf-8').encode()).hexdigest(), expected, name)
        docker = (ROOT / 'deployment/local/Dockerfile').read_text()
        self.assertEqual(docker.count('FROM ' + manifest['python_base'] + ' AS '), 2)
        self.assertTrue(docker.startswith('# syntax=' + manifest['dockerfile_frontend']))
        self.assertEqual(docker.count('--require-hashes'), 3)
        self.assertIn('--no-build-isolation', docker)
        self.assertIn('--no-deps /wheel/*.whl', docker)

    @unittest.skipUnless(os.getenv('GITHUB_ACTIONS') == 'true', 'public-index resolution check runs in CI')
    def test_two_hash_verified_resolution_plans_are_identical(self):
        plans = []
        with tempfile.TemporaryDirectory(prefix='kw-lock-plan-') as directory:
            for number in (1, 2):
                report = Path(directory) / f'plan-{number}.json'
                result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--dry-run',
                    '--ignore-installed', '--only-binary=:all:', '--require-hashes',
                    '-r', str(ROOT / 'requirements-dev.lock'), '--report', str(report)],
                    capture_output=True, text=True, timeout=240)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                plans.append({row['metadata']['name'].lower(): (row['metadata']['version'],
                    row['download_info']['archive_info']['hashes']['sha256'])
                    for row in json.loads(report.read_text())['install']})
        self.assertEqual(plans[0], plans[1])
        self.assertGreater(len(plans[0]), 20)

    @unittest.skipUnless(os.getenv('GITHUB_ACTIONS') == 'true' and sys.version_info[:2] == (3, 12),
                         'runtime image is Python 3.12; build once on isolated CI runner')
    def test_runtime_image_repeat_build_has_same_packages_and_records_provenance(self):
        event_file = os.getenv('GITHUB_EVENT_PATH')
        event = json.loads(Path(event_file).read_text()) if event_file else {}
        revision = event.get('pull_request', {}).get('head', {}).get('sha') or subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        image_ids, packages = [], []
        for number in (1, 2):
            result = subprocess.run(['docker', 'build', '--no-cache', '--target', 'runtime',
                '--label', 'org.opencontainers.image.revision=' + revision, '--quiet',
                '--file', str(ROOT / 'deployment/local/Dockerfile'), str(ROOT)],
                env=dict(os.environ, DOCKER_BUILDKIT='1'), capture_output=True, text=True, timeout=600)
            self.assertEqual(result.returncode, 0, result.stderr[-4000:])
            image_id = result.stdout.strip().splitlines()[-1]
            self.assertRegex(image_id, r'^sha256:[0-9a-f]{64}$')
            image_ids.append(image_id)
            command = 'import importlib.metadata as m,json;print(json.dumps(sorted((d.metadata["Name"].lower(),d.version) for d in m.distributions())))'
            packages.append(subprocess.check_output(['docker', 'run', '--rm', '--network', 'none',
                image_id, 'python', '-c', command], text=True).strip())
            user = subprocess.check_output(['docker', 'image', 'inspect', image_id,
                '--format', '{{.Config.User}}'], text=True).strip()
            self.assertEqual(user, '10001:10001')
        self.assertEqual(packages[0], packages[1])
        manifest = json.loads((ROOT / 'build-inputs.json').read_text())
        record = dict(source_revision=revision, image_ids=image_ids,
            image_ids_equal=image_ids[0] == image_ids[1], python_base=manifest['python_base'],
            runtime_lock_sha256=manifest['sha256']['requirements-runtime.lock'],
            package_fingerprint=hashlib.sha256(packages[0].encode()).hexdigest())
        # Public metadata only. The exact validated image must be promoted, never a mutable tag.
        with self.output_capture.disabled():
            print('PILOT_IMAGE_PROVENANCE ' + json.dumps(record, sort_keys=True), flush=True)
        # Exercise the complete API/database/gateway suite with the locked runtime and dev set.
        result = subprocess.run(['docker', 'build', '--target', 'tests', '--quiet',
            '--file', str(ROOT / 'deployment/local/Dockerfile'), str(ROOT)],
            env=dict(os.environ, DOCKER_BUILDKIT='1'), capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        test_image = result.stdout.strip().splitlines()[-1]
        result = subprocess.run(['docker', 'run', '--rm', '--network', 'host',
            '-e', 'ARENA_TEST_DSN', '-e', 'ARENA_REQUIRE_DB=1', test_image],
            capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr[-2000:])
        with self.output_capture.disabled():
            print('LOCKED_CONTAINER_TESTS ' + result.stdout[-1800:], flush=True)
