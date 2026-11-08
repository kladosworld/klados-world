"""Synthetic approval/packaging tests. These fixtures are not legal policies."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from arena import legal_publication as legal, website
from arena.app import create_app

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = "# Synthetic test document\n\nNot a legal policy. Klados World, EU\n\n[Privacy](mailto:privacy@kladosworld.com)\n"


def approve(folder, text=SYNTHETIC):
    folder.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in legal.FILES.values():
        raw = text.encode('utf-8')
        (folder / name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    (folder / 'APPROVED.json').write_text(json.dumps(dict(schema_version=1, status='approved',
        approval_reference='SYNTHETIC TEST ONLY; not Product Owner approval', sha256=hashes)), encoding='utf-8')
    return hashes


def copy_build_context(target):
    target.mkdir()
    for name in ('pyproject.toml', 'hatch_build.py', 'README.md', 'CHANGELOG.md', '.dockerignore',
                 'requirements-runtime.lock', 'requirements-build.lock', 'requirements-dev.lock',
                 'requirements-build.in', 'build-inputs.json', 'src', 'arena', 'tee_arena', 'config',
                 'tests', 'evals'):
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, target / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(source, target / name)
    shutil.copytree(ROOT / 'deployment/local', target / 'deployment/local')



class LegalPublicationTests(unittest.TestCase):
    def test_explicit_final_mode_and_both_approved_hashes_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            approve(root)
            for enabled in ('0', '1'):
                with patch.dict(os.environ, KLADOS_LEGAL_FINAL=enabled), TestClient(create_app(legal_directory=root)) as client:
                    for page in legal.FILES:
                        response = client.get('/site/legal/' + page + '.json')
                        expected = SYNTHETIC if enabled == '1' else legal.PLACEHOLDER
                        self.assertEqual(response.json()['text'], expected)
                        self.assertEqual(response.json()['text'].encode('utf-8'), expected.encode('utf-8'))
                        self.assertEqual(('/' + page) in client.get('/sitemap.xml').text, enabled == '1')
            for broken in ('missing', 'one-file', 'pending', 'hash', 'reference'):
                approve(root)
                if broken == 'missing':
                    (root / 'APPROVED.json').unlink()
                elif broken == 'one-file':
                    (root / 'TERMS.md').unlink()
                else:
                    manifest = json.loads((root / 'APPROVED.json').read_text(encoding='utf-8'))
                    if broken == 'pending': manifest['status'] = 'pending'
                    if broken == 'hash': manifest['sha256']['TERMS.md'] = '0' * 64
                    if broken == 'reference': manifest['approval_reference'] = ''
                    (root / 'APPROVED.json').write_text(json.dumps(manifest), encoding='utf-8')
                with patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'), self.assertRaisesRegex(ValueError, '^Legal publication gate failed$'):
                    create_app(legal_directory=root)
                with patch.dict(os.environ, KLADOS_LEGAL_FINAL='0'):
                    self.assertEqual(website.legal_content('terms', root)['text'], legal.PLACEHOLDER)
            with tempfile.TemporaryDirectory() as empty, patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'):
                with self.assertRaises(ValueError): create_app(legal_directory=empty)

    def test_no_silent_rewriting_and_unsupported_or_incomplete_text_fails(self):
        fragments = ('[[OPERATOR]]', '{{DATE}}', 'TODO', 'TBD', '*italic*', '_italic_',
            '#### Deep heading', '<script>x</script>', '![image](https://example.com/a)',
            '```code```', '`code`', '[x](javascript:alert)', '[x](http://example.com)',
            '> **REVIEW:** private', '> Draft for legal review', '    indented code')
        for fragment in fragments:
            with self.subTest(fragment=fragment), self.assertRaises(ValueError):
                legal.validate_text((SYNTHETIC + '\n' + fragment + '\n').encode())
        for raw in (b'', b' \n', SYNTHETIC.encode().replace(b'\n', b'\r\n'), b'\xef\xbb\xbf' + SYNTHETIC.encode(), SYNTHETIC.rstrip().encode()):
            with self.subTest(raw=raw[:15]), self.assertRaises(ValueError): legal.validate_text(raw)
        text = SYNTHETIC + '\n**Bold**\n\n- A point\n\n> An ordinary review of the policy.\n'
        self.assertEqual(legal.validate_text(text.encode()), text)
        self.assertEqual(website.public_legal_text(text), text)

    def test_changed_document_after_startup_fails_closed_without_serving_content(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'):
            root = Path(tmp); approve(root)
            with TestClient(create_app(legal_directory=root), raise_server_exceptions=False) as client:
                (root / 'TERMS.md').write_bytes(b'Unapproved changed text\n')
                for route in ('/site/legal/terms.json', '/site/legal/privacy.json', '/terms', '/sitemap.xml'):
                    response = client.get(route)
                    self.assertEqual(response.status_code, 500)
                    self.assertNotIn('Unapproved changed text', response.text)

    def test_packaged_release_matches_approved_source_or_remains_unpublished(self):
        # Runs inside the actual locked runtime test image as well as source/host suites.
        folder = legal.directory()
        self.assertTrue((folder / 'APPROVED.json').is_file())
        manifest = json.loads((folder / 'APPROVED.json').read_bytes())
        if manifest['status'] == 'approved':
            docs = legal.approved_documents()
            with patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'), TestClient(create_app()) as client:
                for page, name in legal.FILES.items():
                    self.assertTrue(website.legal_file(page).is_file())
                    raw = website.legal_file(page).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest['sha256'][name])
                    self.assertEqual(client.get('/site/legal/' + page + '.json').json()['text'].encode(), raw)
                    self.assertEqual(docs[page].encode(), raw)
        else:
            with patch.dict(os.environ, KLADOS_LEGAL_FINAL='0'):
                self.assertEqual(website.legal_content('terms')['text'], legal.PLACEHOLDER)
            with patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'), self.assertRaises(ValueError): create_app()

    @unittest.skipUnless(importlib.util.find_spec('hatchling'), 'requires hash-locked build tools; runtime packaging is also tested in CI Docker')
    def test_built_wheel_contains_only_allowlisted_exact_legal_resources(self):
        with tempfile.TemporaryDirectory(prefix='kw-legal-wheel-') as tmp:
            context = Path(tmp) / 'context'
            copy_build_context(context)
            folder = context / 'docs/legal'
            hashes = approve(folder)
            (folder / 'PRIVATE_REVIEW.txt').write_text('UNPUBLISHED_REVIEW_CANARY', encoding='utf-8')
            result = subprocess.run([sys.executable, '-m', 'hatchling', 'build', '-t', 'wheel'],
                cwd=context, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            with zipfile.ZipFile(next((context / 'dist').glob('*.whl'))) as wheel:
                resources = {name for name in wheel.namelist() if name.startswith('arena/legal/')}
                self.assertEqual(resources, {'arena/legal/' + name for name in (*legal.FILES.values(), 'APPROVED.json')})
                for name in legal.FILES.values():
                    self.assertEqual(wheel.read('arena/legal/' + name), (folder / name).read_bytes())
                    self.assertEqual(hashlib.sha256(wheel.read('arena/legal/' + name)).hexdigest(), hashes[name])
