"""Durable on-disk reference client. POSIX disk guarantees require native validation."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arena.agent_client import Client, AgentError
from arena.agent_client_state import State, StateError
from tests.test_arena_external_agents import InviteCase


class StoragePlatformTests(unittest.TestCase):
    @unittest.skipUnless(os.name != 'posix', 'Non-POSIX fail-closed contract')
    def test_unverified_windows_acl_storage_is_not_used_for_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(StateError):
                State(Path(directory) / 'client')
            self.assertFalse((Path(directory) / 'client').exists())

    def test_insecure_directory_metadata_is_rejected_on_every_platform(self):
        import stat
        from types import SimpleNamespace
        value = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        with patch('arena.agent_client_state.os.fstat',return_value=value):
            with patch('arena.agent_client_state.os.geteuid',return_value=0,create=True):
                with self.assertRaises(StateError):
                    State._check(0,directory=True)


@unittest.skipUnless(os.name == 'posix', 'Native POSIX owner/mode/fsync and process locks required')
class PersistentClientTests(InviteCase):
    def test_lost_response_and_new_process_replay_same_identity_once(self):
        agent = self.register().json()
        self.tick()
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'client'
            client = Client('https://synthetic.example', token=agent['token'], state_dir=directory)
            sent = []
            def server(method, path, body=None, *, authenticated=True, request_id=None):
                sent.append(request_id)
                headers = dict(self.headers(agent), **{'Idempotency-Key': request_id})
                response = self.client.request(method, '/api/agents/' + path, headers=headers, json=body)
                self.assertEqual(response.status_code, 200)
                if len(sent) == 1:
                    raise AgentError('Connection failed; request outcome is unknown')
                return response.json()
            with patch.object(client, '_send', side_effect=server):
                with self.assertRaises(AgentError):
                    client.action({'action': 'post', 'text': 'A durable external thought.'})
            stored = json.loads((directory / 'client.json').read_text())
            self.assertIsNotNone(stored['pending'])
            self.assertEqual(os.stat(directory).st_mode & 0o077, 0)
            self.assertEqual(os.stat(directory / 'client.json').st_mode & 0o077, 0)
            restarted = Client('https://synthetic.example', state_dir=directory)
            with patch.object(restarted, '_send', side_effect=server):
                self.assertEqual(restarted.resume()['action'], 'post')
            self.assertEqual(sent, [stored['pending']['request_id']] * 2)
            self.assertIsNone(json.loads((directory / 'client.json').read_text())['pending'])
            self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts').fetchone()['n'], 1)
            self.assertEqual(restarted.token, agent['token'])

    def test_insecure_or_symlinked_state_is_refused_without_chmod(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'client'
            directory.mkdir(mode=0o700)
            # Feed rejected metadata without ever weakening a real directory.
            actual = os.fstat
            inode = directory.stat().st_ino
            def insecure_metadata(fd):
                from types import SimpleNamespace
                value = actual(fd)
                return (SimpleNamespace(st_uid=value.st_uid,st_mode=value.st_mode | 0o055)
                        if value.st_ino == inode else value)
            with patch('arena.agent_client_state.os.fstat', side_effect=insecure_metadata):
                with self.assertRaises(AgentError):
                    Client('https://synthetic.example', state_dir=directory)
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
            target = Path(root) / 'target'
            target.write_text('DO_NOT_TOUCH')
            (directory / 'client.json').symlink_to(target)
            with self.assertRaises(AgentError):
                Client('https://synthetic.example', state_dir=directory)
            self.assertEqual(target.read_text(), 'DO_NOT_TOUCH')
