"""Public release fails closed for unverified activity, without touching history."""
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from arena.app import create_app
from arena import society_public, website


class Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def fetchone(self):
        return self.rows[0]


class Connection:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=None):
        self.statements.append(query)
        if 'society_clock' in query:
            return Result([{'tick': 72, 'paused': True}])
        if 'public_description AS description' in query:
            return Result([{'id': 'alder', 'name': 'Alder', 'kind': 'founder', 'description': 'An individual identity.'}])
        raise AssertionError('Public projection accessed an activity/private table: ' + query)


class PublicReleaseTests(unittest.TestCase):
    def test_all_modes_expose_only_registered_identities_and_real_pause_state(self):
        for mode in ('mock', 'live', 'unknown'):
            conn = Connection()
            with patch.dict(os.environ, KLADOS_SOCIETY_PUBLIC='1', KLADOS_AI_MODE=mode), TestClient(create_app(connection_factory=lambda: conn)) as client:
                feed = client.get('/api/society/feed').json()
                self.assertTrue(feed['paused'])
                self.assertEqual(feed['events'], [])
                self.assertNotIn('tick', feed)
                self.assertEqual(set(feed['agents'][0]), {'id', 'name', 'kind', 'description'})
                self.assertEqual(client.get('/api/society/square').json(), {'posts': []})
                profile = client.get('/api/society/agents/alder').json()
                for key in ('posts', 'energy_history'):
                    self.assertEqual(profile[key], [])
                self.assertEqual(profile['relations'], {})
                self.assertIsNone(profile['last_words'])
                self.assertIsNone(profile['fossil'])
                self.assertNotIn('energy', profile)
                stats = client.get('/api/society/stats').json()
                self.assertEqual(stats['population'], {'registered': 1})
                self.assertEqual(stats['published'], {'posts': 0, 'replies': 0})
                self.assertEqual(stats['activity_by_day'], [])
                self.assertEqual(stats['interactions'], [])
                self.assertEqual(client.get('/api/society/agents/missing').status_code, 404)
                self.assertTrue(all(query.startswith('SELECT') for query in conn.statements))

    def test_fixtures_are_default_closed_and_only_explicit_development_preview_opens_them(self):
        for examples in (False, True):
            with TestClient(create_app(examples=examples)) as client:
                config = client.get('/society/config.json').json()
                self.assertEqual(config['examples'], examples)
                self.assertFalse(config['likes_enabled'])
                for resource in ('feed.json', 'square.json', 'stats.json', 'agents/alder.json'):
                    response = client.get('/static/fixtures/' + resource)
                    self.assertEqual(response.status_code, 200 if examples else 404)
                    if examples:
                        self.assertTrue(response.json()['_example'])

    def test_public_writes_never_reach_database_even_with_session_or_csrf_headers(self):
        def forbidden():
            raise AssertionError('Disabled visitor likes must not access the database')
        with patch.dict(os.environ, KLADOS_SOCIETY_PUBLIC='1', KLADOS_AI_MODE='live'), TestClient(create_app(connection_factory=forbidden)) as client:
            for headers in ({}, {'X-CSRF-Token': 'synthetic', 'X-Dev-Voter': 'synthetic'}):
                self.assertEqual(client.post('/api/society/posts/1/like', headers=headers).status_code, 403)

    def test_official_contacts_cannot_expose_operator_forwarding_configuration(self):
        with patch.dict(os.environ, KLADOS_CONTACT_EMAIL='private@example.com', KLADOS_PRIVACY_EMAIL='private@example.com', KLADOS_REPORT_EMAIL='private@example.com'), TestClient(create_app()) as client:
            config = client.get('/society/config.json').json()
            for key, address in website.CONTACTS.items():
                self.assertEqual(config[key], address)
            self.assertNotIn('private@example.com', json.dumps(config))
        self.assertIn('not currently available', json.dumps(website.docs_content()))
