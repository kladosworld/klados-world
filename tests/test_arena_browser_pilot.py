"""Actual browser against a disposable, persisted mock society; no paid requests."""
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests.arena_support import DSN, psycopg
from tests.test_arena_society_life import WorkerCase, RULES

CHROME = shutil.which('google-chrome') or shutil.which('chromium')
if os.getenv('GITHUB_ACTIONS') == 'true' and not CHROME:
    raise RuntimeError('CI browser validation requires the runner installed Chrome')


@unittest.skipUnless(DSN and psycopg and CHROME, 'requires disposable PostgreSQL and installed Chrome')
class PersistedBrowserTests(WorkerCase):
    def test_mock_worker_public_api_and_responsive_accessible_browser(self):
        from arena import db, society, worker
        from arena.app import create_app
        from playwright.sync_api import sync_playwright
        from psycopg import sql
        import uvicorn

        directory = tempfile.TemporaryDirectory(prefix='kw-browser-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        book = worker.open_ledger(root / 'ledger.json', '1')
        gateway = worker.build_gateway(book, environ={'KLADOS_AI_MODE': 'mock'})
        result = worker.Worker(self.conn, RULES, gateway, observer_gateway=gateway,
                              now=lambda: self.now.replace(minute=59)).run()
        self.assertEqual((result['turns'], result['failed']), (4, 0))
        society.set_paused(self.conn, RULES, True, self.now, 'isolated browser validation')
        self.conn.commit()
        count = self.conn.execute("SELECT count(*) AS n FROM society_posts WHERE status='published'").fetchone()['n']
        self.assertEqual(count, 3)

        def connect():
            conn = db.connect(DSN)
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return conn

        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        environ = {'KLADOS_SOCIETY_PUBLIC': '1', 'KLADOS_AI_MODE': 'mock',
                   'ARENA_AUTH_ENABLED': '0', 'ARENA_GUEST_ENABLED': '0'}
        with patch.dict(os.environ, environ):
            server = uvicorn.Server(uvicorn.Config(create_app(connection_factory=connect),
                host='127.0.0.1', port=port, log_level='error', ws='none'))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 10)
            self.addCleanup(setattr, server, 'should_exit', True)
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            self.assertTrue(server.started)
            base = f'http://127.0.0.1:{port}'
            axe = Path(__file__).with_name('axe-core-4.14.0.cjs').read_text(encoding='utf-8')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
                page = browser.new_page()
                errors, requests = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: requests.append(request.url))
                audits = []
                for label, width, height in [('desktop',1440,1000), ('tablet',900,1000), ('mobile',390,844)]:
                    page.set_viewport_size({'width':width, 'height':height})
                    for route,name in [('/society','society'),('/society/agents/alder','profile'),('/','home')]:
                        page.goto(base+route, wait_until='networkidle')
                        page.evaluate('document.fonts.ready')
                        self.assertEqual(page.locator('h1').count(), 1)
                        self.assertEqual(page.locator('nav[aria-label="Main"]').count(), 1)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        self.assertNotIn('Example data', page.locator('body').inner_text())
                        page.keyboard.press('Tab')
                        self.assertTrue(page.locator('.skip-link').evaluate('el => el === document.activeElement'))
                        page.keyboard.press('Enter')
                        if width < 701:
                            page.get_by_role('button', name='Menu', exact=True).click()
                            self.assertEqual(page.locator('#menu-toggle').get_attribute('aria-expanded'),'true')
                            page.keyboard.press('Escape')
                            self.assertEqual(page.locator('#menu-toggle').get_attribute('aria-expanded'),'false')
                        page.evaluate(axe)
                        audit=page.evaluate("async () => await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})")
                        self.assertEqual(audit['violations'], [], (route,width))
                        audits.append({'page':name,'width':width,'violations':0})
                        if name == 'society':
                            summary=page.locator('#world-summary strong').all_text_contents()
                            self.assertEqual(summary[:2], ['4',str(count)])
                            self.assertEqual(page.locator('.thread').count(),count)
                            self.assertIn('Mock AI activity',page.locator('#overview-source').inner_text())
                            self.assertIn('No hashtags',page.locator('#world-topics').inner_text())
                            square=page.request.get(base+'/api/society/square').json()
                            self.assertEqual(len(square['posts']),count)
                            for post in square['posts']:
                                self.assertIn(post['text'],page.locator('body').inner_text())
                            page.get_by_role('searchbox').fill('no-such-content-7392')
                            self.assertTrue(page.locator('#feed-empty').is_visible())
                            page.get_by_role('searchbox').fill('')
                        page.screenshot(path=str(root/f'{name}-{label}.png'), full_page=True)
                self.assertFalse(errors,errors)
                self.assertTrue(all(url.startswith(base) or url.startswith('data:') for url in requests),requests)
                self.assertEqual(society.audit(self.conn),[])
                self.assertTrue(society.clock(self.conn)['paused'])
                browser.close()
        # Screenshot files are temporary runner artifacts; public checked-in screenshots use example mode.
        self.assertEqual(len(audits),9)
