"""Public connection guide contracts without credentials, a database or inference."""
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from arena.app import create_app
from arena import external_agents, website


class ConnectionGuideTests(unittest.TestCase):
    def test_public_guide_metadata_and_discovery_need_no_database(self):
        def forbidden():
            raise AssertionError('The guide must not access the database')
        for flag in ('off', 'invite'):
            with patch.dict(os.environ, KLADOS_EXTERNAL_AGENTS=flag), TestClient(
                create_app(connection_factory=forbidden)
            ) as client:
                response = client.get('/connect')
                self.assertEqual(response.status_code, 200)
                self.assertIn('<title>For Agents · Klados World</title>', response.text)
                self.assertIn('Participation is invitation-only', response.text)
                self.assertIn('Registration is currently closed', response.text)
                self.assertEqual(response.text.count('<h1>'), 1)
                self.assertIn('Skip to content', response.text)
                self.assertNotIn('<form', response.text)
                self.assertNotIn('<input', response.text)
                self.assertIn('https://kladosworld.com/connect', client.get('/sitemap.xml').text)
                self.assertEqual(client.post('/connect').status_code, 404)
                self.assertEqual(client.get('/static/connect.html').status_code, 200)

    def test_guide_links_existing_public_integration_without_registration_controls(self):
        html = Path(website.__file__).with_name('static').joinpath('connect.html').read_text(encoding='utf-8')
        for path in ('docs/invited-agents.md', 'arena/agent_client.py'):
            self.assertIn('https://github.com/kladosworld/klados-world/blob/main/' + path, html)
        self.assertNotIn('/api/agents/', html)
        self.assertNotIn('YOUR_AGENT_TOKEN', html)
        self.assertIn('Human visitors can explore the website without posting or liking.', html)
        self.assertIn('does not register an agent or issue an invitation', html)

    @unittest.skipUnless(
        Path(__file__).parents[1].joinpath('docs/WEBSITE_COPY.md').is_file(),
        'public integration documentation is repository-only',
    )
    def test_public_documentation_targets_exist_in_source(self):
        root = Path(__file__).parents[1]
        for path in ('docs/invited-agents.md', 'arena/agent_client.py'):
            self.assertTrue(root.joinpath(path).is_file())

    def test_default_off_and_human_write_gates_are_preserved(self):
        def forbidden():
            raise AssertionError('Closed routes must not access the database')
        with patch.dict(os.environ):
            os.environ.pop('KLADOS_EXTERNAL_AGENTS', None)
            self.assertFalse(external_agents.enabled())
            with TestClient(create_app(connection_factory=forbidden)) as client:
                for method, path in external_agents.ROUTES:
                    self.assertEqual(client.request(method, path, json={}).status_code, 404)
                self.assertEqual(client.post('/api/society/posts/1/like', json={}).status_code, 403)
                self.assertFalse(client.get('/society/config.json').json()['likes_enabled'])

    def test_guide_is_in_primary_navigation_without_duplicate_navigation(self):
        for path in Path(website.__file__).with_name('static').glob('*.html'):
            text = path.read_text(encoding='utf-8')
            main_nav = re.search(r'<nav id="primary-nav".*?</nav>', text)
            self.assertIsNotNone(main_nav, path.name)
            self.assertEqual(main_nav[0].count('href="/connect"'), 1, path.name)
