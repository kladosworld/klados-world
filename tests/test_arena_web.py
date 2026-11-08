"""Website routes, configuration and rule-driven public copy."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from arena import website
from tests.test_arena_legal import approve
try:
    from fastapi.testclient import TestClient
    from arena.app import create_app
except ImportError:
    TestClient = None

ROOT = Path(__file__).resolve().parent.parent


class WebsiteCopyTests(unittest.TestCase):
    @unittest.skipUnless(website.COPY.is_file(), "Requires the repository-only approved copy appendix")
    def test_packaged_home_matches_approved_source(self):
        self.assertEqual(website.home_copy(), website.extract_home_copy())

    def test_rules_are_selected_and_optional_window_is_not_invented(self):
        rules = json.loads(website.RULES.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rules.json'
            rules['energy']['cap'] = 321
            rules['private'] = 'PRIVATE_WEBSITE_CANARY'
            rules['likes']['human'].pop('window_days', None)
            path.write_text(json.dumps(rules))
            content = website.docs_content(path)
            self.assertIn('321 E', json.dumps(content))
            self.assertNotIn('PRIVATE_WEBSITE_CANARY', json.dumps(content))
            self.assertIsNone(content['window_days'])
            self.assertNotIn('Posts can be liked for', json.dumps(content))
            rules['likes']['human']['window_days'] = 7
            path.write_text(json.dumps(rules))
            self.assertIn('Posts can be liked for 7 days.', json.dumps(website.docs_content(path)))

    @unittest.skipUnless((ROOT / "arena/static/home-copy.json").is_file(), "Requires repository-only original public assets")
    def test_public_assets_are_available_without_infrastructure_changes(self):
        from arena.website_assets import HOME_COPY, FAVICON
        self.assertEqual(HOME_COPY, json.loads((ROOT / 'arena/static/home-copy.json').read_text()))
        self.assertEqual(FAVICON, (ROOT / 'arena/static/favicon.svg').read_text())

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_browser_pages(self):
        result = subprocess.run(['node', 'tests/website_pages.test.cjs'], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(TestClient, 'requires arena dependencies')
class WebsiteHTTPTests(unittest.TestCase):
    def test_public_routes_headers_and_retired_routes(self):
        for dev, mode in [('0', ''), ('1', ''), ('0', 'github'), ('1', 'github')]:
            with patch.dict(os.environ, ARENA_DEV_AUTH=dev, ARENA_AUTH=mode), TestClient(create_app()) as client:
                for path in ['/', '/docs', *('/docs/' + slug for slug in website.SLUGS),
                             '/privacy', '/terms', '/contact', '/site/copy.json', '/docs/content.json']:
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, path)
                    self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                    self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
                    self.assertIn("default-src 'self'", response.headers['content-security-policy'])
                for path in ['/github', '/about', '/leaderboard', '/family', '/api/pair', '/api/status',
                             '/api/leaderboard', '/api/family', '/static/index.html', '/static/app.js',
                             '/static/about.html', '/static/github.html', '/static/public.js', '/static/style.css']:
                    self.assertEqual(client.get(path).status_code, 404, path)
                self.assertEqual(client.post('/api/vote', json={}).status_code, 404)
                self.assertEqual(client.get('/docs/not-a-page').status_code, 404)

    def test_gate_blocks_existing_unlisted_routes_before_routing(self):
        import importlib
        app_module = importlib.import_module('arena.app')
        for dev, mode in [('1', ''), ('0', 'github')]:
            for live in ('0', '1'):
                with self.subTest(mode=mode or 'dev', live=live), patch.dict(os.environ,
                        ARENA_DEV_AUTH=dev, ARENA_AUTH=mode, KLADOS_SOCIETY_PUBLIC=live):
                    app = create_app()
                    reached = []
                    @app.get('/__gate_probe')
                    def get_probe():
                        reached.append('GET')
                        return {'probe': 'GET'}
                    @app.post('/__gate_probe')
                    def post_probe():
                        reached.append('POST')
                        return {'probe': 'POST'}
                    with TestClient(app) as client:
                        for method in ('GET', 'POST'):
                            denied = client.request(method, '/__gate_probe')
                            self.assertEqual(denied.status_code, 404)
                            self.assertEqual(denied.json(), {'detail': 'Not found'})
                            self.assertEqual(client.request(method, '/unknown-path').status_code, 404)
                        self.assertEqual(reached, [])
                        refused = client.post('/docs')
                        self.assertEqual(refused.status_code, 404)
                        self.assertEqual(refused.json(), {'detail': 'Not found'})
                        for path in ['/', '/robots.txt', '/sitemap.xml', '/docs', *('/docs/' + slug for slug in website.SLUGS),
                                '/contact', '/privacy', '/terms', '/site/copy.json',
                                '/site/legal/privacy.json', '/site/legal/terms.json', '/docs/content.json',
                                '/society', '/society/square', '/society/stats', '/society/agents/custom',
                                '/society/config.json', '/static/home-copy.json', '/static/website.js']:
                            self.assertEqual(client.get(path).status_code, 200, path)
                        with patch.object(app_module, 'PUBLIC_GET_PATHS', app_module.PUBLIC_GET_PATHS | {'/__gate_probe'}), \
                                patch.object(app_module, 'PUBLIC_POST_PATHS', app_module.PUBLIC_POST_PATHS | {'/__gate_probe'}):
                            self.assertEqual(client.get('/__gate_probe').status_code, 200)
                            self.assertEqual(client.post('/__gate_probe').status_code, 200)
                        self.assertEqual(reached, ['GET', 'POST'])

    def test_terms_title_and_social_metadata_match_document_heading(self):
        with TestClient(create_app()) as client:
            markup = client.get('/terms').text
            self.assertIn('<title>Terms of Service · Klados World</title>', markup)
            self.assertIn('property="og:title" content="Terms of Service · Klados World"', markup)
            self.assertIn('name="twitter:title" content="Terms of Service · Klados World"', markup)
            self.assertNotIn('Terms of Use', markup)

    def test_metadata_is_static_unique_and_escaped(self):
        from html.parser import HTMLParser
        class Head(HTMLParser):
            def __init__(self):
                super().__init__(); self.meta = {}; self.title = ''; self.in_title = False
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'title': self.in_title = True
                if tag == 'meta': self.meta[attrs.get('name') or attrs.get('property')] = attrs.get('content')
            def handle_endtag(self, tag):
                if tag == 'title': self.in_title = False
            def handle_data(self, text):
                if self.in_title: self.title += text
        titles = set()
        with TestClient(create_app()) as client:
            for path in ['/', '/docs', *('/docs/' + slug for slug in website.SLUGS),
                    '/privacy', '/terms', '/contact', '/society', '/society/square', '/society/stats', '/society/agents/example']:
                response = client.get(path); self.assertEqual(response.status_code, 200)
                head = Head(); head.feed(response.text)
                self.assertNotIn(head.title, titles); titles.add(head.title)
                self.assertTrue(head.meta['description'])
                self.assertEqual(head.meta['og:title'], head.title)
                self.assertEqual(head.meta['og:description'], head.meta['description'])
                self.assertEqual(head.meta['og:type'], 'website')
                self.assertIn('/static/favicon.svg', response.text)
            self.assertEqual(client.get('/static/favicon.svg').status_code, 200)

    def test_crawl_routes_list_only_published_public_pages(self):
        import xml.etree.ElementTree as ET
        for live in ('0', '1'):
            for legal_exists in (False, True):
                with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, KLADOS_SOCIETY_PUBLIC=live, KLADOS_LEGAL_FINAL="1" if legal_exists else "0"):
                    if legal_exists:
                        approve(Path(directory), '# Synthetic test policy\n\nKlados World, EU\n')
                    with TestClient(create_app(legal_directory=directory)) as client:
                        robots = client.get('/robots.txt')
                        for prefix in ['/api/', '/auth/', '/v1/']:
                            self.assertIn('Disallow: ' + prefix, robots.text)
                        response = client.get('/sitemap.xml')
                        root = ET.fromstring(response.content)
                        urls = [el.text for el in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
                        expected = ['/', '/docs', *('/docs/' + slug for slug in website.SLUGS), '/contact']
                        if legal_exists:
                            expected.extend(('/privacy', '/terms'))
                        if live == '1':
                            expected.extend(('/society', '/society/square', '/society/stats'))
                        self.assertEqual(urls, ['https://kladosworld.com' + path for path in expected])
                        self.assertEqual(client.post('/sitemap.xml').status_code, 404)

    def test_legal_review_blocks_are_removed_before_json_and_metadata_is_current(self):
        fixture = ('# Policy\n\n> First line of private paragraph\n> **REVIEW:** PRIVATE_REVIEW_CANARY\n'
                   '> continuation of private question\n\n> **Draft for legal review pending**\n> PRIVATE_DRAFT_CANARY\n'
                   '\n> Ordinary public quotation.\n\nPublic policy text.')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, KLADOS_LEGAL_FINAL='0'):
            for name in ('PRIVACY.md', 'TERMS.md'):
                (Path(directory) / name).write_text(fixture)
            with TestClient(create_app(legal_directory=directory)) as client:
                for page in ('privacy', 'terms'):
                    response = client.get('/site/legal/' + page + '.json')
                    self.assertEqual(response.status_code, 200)
                    for private in ('REVIEW', 'Draft for legal review', 'PRIVATE_', 'continuation', 'First line of private'):
                        self.assertNotIn(private, response.text)
                    self.assertEqual(response.json()['text'], 'This page is being prepared.')
                    self.assertIn('This page is being prepared', client.get('/' + page).text)
            with patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'):
                with self.assertRaisesRegex(ValueError, '^Legal publication gate failed$'):
                    create_app(legal_directory=directory)
            approve(Path(directory), '# Synthetic test policy\n\nKlados World, EU\n')
            with patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'):
                website.validate_legal_final(directory)

    def test_final_policy_review_prose_and_ordinary_quote_are_published(self):
        text = ('# Final policy\n\nSome likes are held until a person reviews them.\n\n'
                '> An ordinary review of the policy.\n> Review is a legitimate word here.\n')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'):
            approve(Path(directory), text)
            website.validate_legal_final(directory)
            self.assertEqual(website.public_legal_text(text), text)
            with TestClient(create_app(legal_directory=directory)) as client:
                for page in ('privacy', 'terms'):
                    response = client.get('/site/legal/' + page + '.json')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['text'], text)
                    self.assertEqual(client.get('/' + page).status_code, 200)

    def test_exact_editorial_markers_remove_whole_quote_and_block_final_publication(self):
        for block in ('>   **REVIEW:** private question\n> continuation of the private note\n',
                      '> **Draft for legal review**\n> continuation of draft banner\n'):
            with self.subTest(block=block.splitlines()[0]), tempfile.TemporaryDirectory() as directory:
                text = '# Public policy\n\n' + block + '\nPublic text remains.\n'
                (Path(directory) / 'PRIVACY.md').write_text(text)
                filtered = website.public_legal_text(text)
                self.assertNotIn(block, filtered)
                self.assertNotIn('continuation', filtered)
                self.assertIn('Public text remains.', filtered)
                with patch.dict(os.environ, KLADOS_LEGAL_FINAL='0'):
                    self.assertEqual(website.legal_content('privacy', directory)['text'], 'This page is being prepared.')
                with patch.dict(os.environ, KLADOS_LEGAL_FINAL='1'):
                    with self.assertRaisesRegex(ValueError, '^Legal publication gate failed$'):
                        website.validate_legal_final(directory)

    def test_repository_legal_publication_gate(self):
        website.validate_legal_final()

    def test_contacts_are_independent_validated_and_uncached(self):
        variables = ['KLADOS_CONTACT_EMAIL', 'KLADOS_REPORT_EMAIL', 'KLADOS_PRIVACY_EMAIL']
        for selected in [None, *variables]:
            values = {name: '  ' for name in variables}
            if selected:
                values[selected] = 'operator@example.com'
            with patch.dict(os.environ, values), TestClient(create_app()) as client:
                response = client.get('/society/config.json')
                self.assertEqual(response.headers['cache-control'], 'no-store')
                for field, variable in zip(['contact', 'report', 'privacy'], variables):
                    self.assertEqual(response.json()[field], website.CONTACTS[field])
        self.assertIsNone(website.contact('not an email'))

    def test_legal_files_are_plain_text_and_docs_use_injected_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            rules = json.loads(website.RULES.read_text())
            rules['energy']['cap'] = 321
            path = folder / 'rules.json'
            path.write_text(json.dumps(rules))
            (folder / 'PRIVACY.md').write_text('<script>not executable</script>')
            with TestClient(create_app(rules_path=path, legal_directory=folder)) as client:
                self.assertEqual(client.get('/site/legal/privacy.json').json()['text'], 'This page is being prepared.')
                self.assertEqual(client.get('/site/legal/terms.json').json()['text'], 'This page is being prepared.')
                content = client.get('/docs/content.json').json()
                self.assertEqual(len(content['pages']), 9)
                self.assertIn('321 E', json.dumps(content))
                self.assertEqual(content['pages'][-1]['status'], 'Planned')
                if shutil.which('node'):
                    script = """const assert = require('node:assert/strict');
const {browser,tick} = require('./tests/website_pages.test.cjs');
const docs = JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const b = browser('docs','/docs/energy-actions-and-limits',{live:false},{docs});
tick().then(() => assert.match(b.byId('site-content').textContent, /321 E/));"""
                    result = subprocess.run(['node', '-e', script], input=json.dumps(content),
                        cwd=ROOT, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class WebsiteProvenanceTests(unittest.TestCase):
    def test_source_mode_and_safe_resources_are_public(self):
        for mode in ('mock', 'live'):
            with patch.dict(os.environ, KLADOS_AI_MODE=mode, KLADOS_SOCIETY_PUBLIC='1'), TestClient(create_app()) as client:
                config = client.get('/society/config.json').json()
                self.assertEqual(config['ai_mode'], mode)
                self.assertTrue(config['live'])
                self.assertEqual(client.get('/static/home-copy.json').json(), website.home_copy())
                self.assertEqual(client.get('/static/favicon.svg').status_code, 200)

    def test_docs_do_not_claim_unmerged_like_protections(self):
        text = json.dumps(website.docs_content())
        self.assertNotIn('Daily limits per network', text)
        self.assertNotIn('Suspicious likes may be held', text)
        self.assertNotIn('GitHub sign-in is not available yet', text)
