"""Actual HTTP mutations, lost responses and restarts on disposable PostgreSQL."""
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from arena import db
from tests.test_arena_external_agents import InviteCase


class DurableMutationTests(InviteCase):
    def test_keys_required_for_every_mutation_and_reads_remain_compatible(self):
        agent = self.register().json()
        with TestClient(self.client.app) as raw:
            for method, path, body in (
                ('POST', '/api/agents/register', {}),
                ('POST', '/api/agents/me/actions', {'action': 'rest'}),
                ('POST', '/api/agents/me/credentials/rotate', {}),
                ('POST', '/api/agents/me/owner-claim', {'handle': 'example'}),
                ('POST', '/api/agents/me/retire', {}),
                ('PATCH', '/api/agents/me', {'description': 'An update.'}),
            ):
                with self.subTest(path=path):
                    response = raw.request(method, path, headers=self.headers(agent), json=body)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()['detail'], 'Idempotency-Key required')
            self.assertEqual(raw.get('/api/agents/me', headers=self.headers(agent)).status_code, 200)

    def test_concurrent_same_key_and_restarted_client_have_one_committed_effect(self):
        agent = self.register().json()
        self.tick()
        headers = dict(self.headers(agent), **{'Idempotency-Key': 'durable-action'})
        body = {'action': 'post', 'text': 'One committed idea.', 'note': 'PRIVATE_NOTE'}
        def send(_):
            return self.client.post('/api/agents/me/actions', headers=headers, json=body)
        with ThreadPoolExecutor(4) as executor:
            results = list(executor.map(send, range(4)))
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertTrue(all(r.json() == results[0].json() for r in results))
        with TestClient(self.client.app) as restarted:
            self.assertEqual(restarted.post('/api/agents/me/actions', headers=headers, json=body).json(), results[0].json())
            self.assertEqual(restarted.post('/api/agents/me/actions', headers=headers, json=dict(body, text='Different')).status_code, 409)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts').fetchone()['n'], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_notes').fetchone()['n'], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_turns').fetchone()['n'], 0)

    def test_issuance_replay_never_stores_or_reissues_bearer_tokens(self):
        invitation = self.invitation()
        headers = {'Idempotency-Key': 'register-once'}
        body = {'invite': invitation['invite'], 'name': 'Durable', 'description': 'A persistent identity.'}
        first = self.client.post('/api/agents/register', headers=headers, json=body)
        self.assertEqual(first.status_code, 201)
        replay = self.client.post('/api/agents/register', headers=headers, json=body)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_agents WHERE kind='external'").fetchone()['n'], 1)
        agent = first.json()
        rotate_headers = dict(self.headers(agent), **{'Idempotency-Key': 'rotate-once'})
        rotated = self.client.post('/api/agents/me/credentials/rotate', headers=rotate_headers, json={})
        self.assertEqual(rotated.status_code, 200)
        self.assertEqual(self.client.get('/api/agents/me', headers=self.headers(agent)).status_code, 401)
        new = dict(agent, token=rotated.json()['token'])
        replay_headers = dict(self.headers(new), **{'Idempotency-Key': 'rotate-once'})
        self.assertEqual(self.client.post('/api/agents/me/credentials/rotate', headers=replay_headers, json={}).status_code, 409)
        self.assertEqual(self.client.get('/api/agents/me', headers=self.headers(new)).status_code, 200)
        stored = json.dumps([dict(r) for r in self.conn.execute('SELECT * FROM society_mutation_receipts')], default=str)
        for secret in (invitation['invite'], agent['token'], new['token']):
            self.assertNotIn(secret, stored)

    def test_migration_is_additive_and_repeatable_on_populated_society(self):
        self.register()
        self.conn.execute('DROP TABLE society_mutation_receipts')
        self.conn.execute("DELETE FROM arena_migrations WHERE name='0029_external_mutation_receipts.sql'")
        before = [dict(r) for r in self.conn.execute('SELECT * FROM society_agents ORDER BY id')]
        db.migrate(self.conn)
        db.migrate(self.conn)
        self.assertEqual(before, [dict(r) for r in self.conn.execute('SELECT * FROM society_agents ORDER BY id')])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_mutation_receipts').fetchone()['n'], 0)

    def test_operator_recovery_requires_exact_verified_ownership_and_preserves_controls(self):
        from arena import external_agents as external
        from datetime import timedelta
        agent = self.register().json()
        claim = self.conn.execute("SELECT id FROM external_owner_claims WHERE agent_id=%s AND status='verified'", (agent['id'],)).fetchone()['id']
        expected_digest = self.conn.execute('SELECT token_digest FROM agent_tokens WHERE agent_id=%s', (agent['id'],)).fetchone()['token_digest']
        before = [dict(r) for r in self.conn.execute('SELECT * FROM society_agents ORDER BY id')]
        for changes in ({'attest': False}, {'verified_claim_id': claim + 1000}, {'expected_token_digest': '0' * 64}):
            arguments = dict(verified_claim_id=claim, expected_token_digest=expected_digest, attest=True, reference='Synthetic fresh ownership proof', approved_by='test', now=self.now)
            arguments.update(changes)
            with self.assertRaises(ValueError), self.conn.transaction():
                external.recover_token(self.conn, agent['id'], **arguments)
        result = external.recover_token(self.conn, agent['id'], verified_claim_id=claim, expected_token_digest=expected_digest,
            attest=True, reference='Synthetic fresh ownership proof', approved_by='test', now=self.now)
        self.conn.commit()
        self.assertEqual(self.client.get('/api/agents/me', headers=self.headers(agent)).status_code, 401)
        recovered = dict(agent, token=result['token'])
        self.assertEqual(self.client.get('/api/agents/me', headers=self.headers(recovered)).status_code, 200)
        self.assertEqual(before, [dict(r) for r in self.conn.execute('SELECT * FROM society_agents ORDER BY id')])
        expected_digest = self.conn.execute('SELECT token_digest FROM agent_tokens WHERE agent_id=%s', (agent['id'],)).fetchone()['token_digest']
        external.control_agent(self.conn, agent['id'], 'suspend', self.now + timedelta(seconds=1))
        self.conn.commit()
        with self.assertRaises(ValueError):
            external.recover_token(self.conn, agent['id'], verified_claim_id=claim, expected_token_digest=expected_digest,
                attest=True, reference='Synthetic fresh proof', approved_by='test', now=self.now)
        self.conn.rollback()
