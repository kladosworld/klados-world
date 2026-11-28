"""Admission is bounded, fails predictably, and never leaks connection permits."""
import os
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from arena import db
from arena.app import create_app
from tests.arena_support import DSN


class AdmissionTests(unittest.TestCase):
    def test_saturation_returns_503_without_exposing_configuration(self):
        def busy():
            raise db.DatabaseBusy('PRIVATE_DIAGNOSTIC')
        with patch.dict(os.environ, KLADOS_SOCIETY_PUBLIC='1'):
            with TestClient(create_app(connection_factory=busy)) as client:
                response = client.get('/api/society/square')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['retry-after'], '2')
        self.assertNotIn('PRIVATE', response.text)

    def test_failed_connect_releases_permit(self):
        permit = threading.BoundedSemaphore(1)
        with patch.object(db, '_ADMISSION', permit):
            with patch.object(db.AdmittedConnection, 'connect', side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    db.connect('not a credential')
            self.assertTrue(permit.acquire(blocking=False))
            permit.release()

    def test_bad_configuration_fails_closed(self):
        for raw in ('0', '65', '-1', '', '1.5', ' 8', '\u0668'):
            with self.subTest(raw=raw), patch.object(db, '_ADMISSION', None):
                with patch.dict(os.environ, KLADOS_DB_CONNECTION_LIMIT=raw):
                    with self.assertRaises(db.DatabaseBusy):
                        db._admission()

    @unittest.skipUnless(DSN, 'Disposable PostgreSQL required')
    def test_context_exit_and_double_close_release_exactly_one_permit(self):
        permit = threading.BoundedSemaphore(1)
        with patch.object(db, '_ADMISSION', permit):
            with db.connect(DSN) as connection:
                self.assertFalse(permit.acquire(blocking=False))
                with self.assertRaises(db.DatabaseBusy):
                    db.connect(DSN)
            connection.close()
            with self.assertRaises(RuntimeError):
                with db.connect(DSN):
                    raise RuntimeError('Synthetic rollback')
            self.assertTrue(permit.acquire(blocking=False))
            permit.release()

    @unittest.skipUnless(DSN, 'Disposable PostgreSQL required')
    def test_web_timeouts_do_not_change_migration_connections(self):
        with db.connect_web(DSN) as connection:
            self.assertEqual(connection.execute('SHOW statement_timeout').fetchone()['statement_timeout'],'5s')
            self.assertEqual(connection.execute('SHOW lock_timeout').fetchone()['lock_timeout'],'2s')
            self.assertEqual(connection.execute('SHOW idle_in_transaction_session_timeout').fetchone()['idle_in_transaction_session_timeout'],'15s')
        with db.connect(DSN) as connection:
            self.assertEqual(connection.execute('SHOW statement_timeout').fetchone()['statement_timeout'],'0')

    def test_transient_database_failures_return_only_controlled_backpressure(self):
        from psycopg import OperationalError
        from psycopg.errors import QueryCanceled, LockNotAvailable, DeadlockDetected, SerializationFailure
        for failure in (OperationalError, QueryCanceled, LockNotAvailable, DeadlockDetected, SerializationFailure):
            def busy():
                raise failure('PRIVATE_QUERY_OR_CONFIGURATION')
            with self.subTest(error=failure.__name__), patch.dict(os.environ,KLADOS_SOCIETY_PUBLIC='1'):
                with TestClient(create_app(connection_factory=busy)) as client:
                    response = client.get('/api/society/square')
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.headers['retry-after'],'2')
            self.assertNotIn('PRIVATE',response.text)
