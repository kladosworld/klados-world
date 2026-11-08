"""Minimal GitHub OAuth identity, PKCE and opaque server-side sessions.

Provider tokens and profile data are transient. Never include provider diagnostics,
credentials, authorization codes or raw user ids in responses or logs.
"""
import base64
from datetime import timedelta
import hashlib
import hmac
import math
import os
import re
import secrets
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

SESSION_COOKIE = '__Host-arena-session'
FLOW_COOKIE = '__Host-arena-oauth'
SESSION_SECONDS = 7 * 24 * 3600
LEGACY_GUEST_VOTE_WEIGHT = 0.25  # Historical pre-pilot value; never used for new votes.


def mode():
    setting = os.getenv('ARENA_AUTH', '')
    dev = os.getenv('ARENA_DEV_AUTH') == '1'
    github = setting == 'github'
    if setting not in ('', 'github') or dev == github:
        return None
    return 'dev' if dev else 'github'


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def settings():
    values = {key: os.getenv(key, '') for key in (
        'ARENA_GITHUB_CLIENT_ID', 'ARENA_GITHUB_CLIENT_SECRET',
        'ARENA_GITHUB_CALLBACK_URL', 'ARENA_IDENTITY_SECRET')}
    callback = urlsplit(values['ARENA_GITHUB_CALLBACK_URL'])
    if (not all(values.values()) or len(values['ARENA_IDENTITY_SECRET'].encode()) < 32
            or callback.scheme != 'https' or not callback.hostname or callback.username
            or callback.password or callback.fragment or callback.query):
        raise HTTPException(503, 'GitHub sign-in is not configured')
    return values


def github_enabled():
    try:
        settings()
        return True
    except (HTTPException, ValueError):
        return False


def identity_secret():
    secret = os.getenv('ARENA_IDENTITY_SECRET', '').encode()
    if len(secret) < 32:
        raise HTTPException(503, 'Authentication is not configured')
    return secret


def guest_vote_weight():
    """Read the pilot weight in one place; reject invalid configuration safely."""
    try:
        value = float(os.getenv('ARENA_GUEST_VOTE_WEIGHT', '1.0'))
    except (ValueError, OverflowError):
        raise HTTPException(503, 'Guest vote weight is not configured correctly') from None
    if not math.isfinite(value) or not 0 < value <= 1:
        raise HTTPException(503, 'Guest vote weight is not configured correctly')
    return value


def vote_weight(account, now):
    if account['provider'] == 'guest':
        return guest_vote_weight()
    return 0.5 if now - account['created_at'] < timedelta(days=7) else 1.0


def issue_session(conn, voter_id, now, *, adult=False):
    raw = secrets.token_urlsafe(32)
    conn.execute('''INSERT INTO voter_sessions(token_digest, voter_id, created_at, expires_at, adult_confirmed)
        VALUES (%s,%s,%s,%s,%s)''', (digest(raw), voter_id, now, now + timedelta(seconds=SESSION_SECONDS), adult))
    return raw


def csrf_token(raw_session):
    secret = identity_secret()
    return hmac.new(secret, ('csrf:' + raw_session).encode(), hashlib.sha256).hexdigest()


class GitHubProvider:
    def exchange(self, code, verifier, config):
        """Exchange once and retain only the numeric user id, never the token/profile."""
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                response = client.post('https://github.com/login/oauth/access_token',
                    headers={'Accept': 'application/json'}, data={
                        'client_id': config['ARENA_GITHUB_CLIENT_ID'],
                        'client_secret': config['ARENA_GITHUB_CLIENT_SECRET'],
                        'redirect_uri': config['ARENA_GITHUB_CALLBACK_URL'],
                        'code': code, 'code_verifier': verifier})
                response.raise_for_status()
                token = response.json().get('access_token')
                if not isinstance(token, str) or not token:
                    raise ValueError('missing token')
                profile = client.get('https://api.github.com/user', headers={
                    'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
                profile.raise_for_status()
                identifier = profile.json().get('id')
                if type(identifier) is not int or identifier < 1:
                    raise ValueError('invalid provider identity')
                return str(identifier)
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise HTTPException(400, 'GitHub sign-in failed') from None


def _session(conn, request, now):
    raw = request.cookies.get(SESSION_COOKIE, '')
    if not raw or len(raw) > 256:
        raise HTTPException(401, 'Sign in with GitHub')
    row = conn.execute('''SELECT * FROM voter_sessions
        WHERE token_digest = %s AND expires_at > %s FOR UPDATE''', (digest(raw), now)).fetchone()
    if row is None:
        raise HTTPException(401, 'Sign in with GitHub')
    return raw, row


def require_csrf(request, raw):
    submitted = request.headers.get('X-CSRF-Token', '')
    if not re.fullmatch(r'[0-9a-f]{64}', submitted) or not hmac.compare_digest(submitted, csrf_token(raw)):
        raise HTTPException(403, 'CSRF token required')


def voter(conn, request, now, *, vote=False):
    if mode() == 'dev':
        identifier = request.headers.get('X-Dev-Voter')
        row = conn.execute("SELECT * FROM voters WHERE id = %s AND provider = 'dev' FOR UPDATE",
                           (identifier,)).fetchone() if identifier else None
        if row is None:
            raise HTTPException(401, 'Known X-Dev-Voter is required')
        return row
    if mode() != 'github':
        raise HTTPException(403, 'Authentication disabled')
    raw, session = _session(conn, request, now)
    if vote:
        require_csrf(request, raw)
        if not session['adult_confirmed']:
            raise HTTPException(403, 'Confirm you are 18 or older before voting')
    row = conn.execute("SELECT * FROM voters WHERE id = %s AND provider IN ('github', 'guest') FOR UPDATE",
                       (session['voter_id'],)).fetchone()
    if row is None:
        raise HTTPException(401, 'Sign in to vote')
    if row['provider'] == 'guest':
        from arena.guest import available
        if not available():
            raise HTTPException(401, 'Guest voting is disabled')
    return row


def _cookie(response, name, value, seconds):
    response.set_cookie(name, value, max_age=seconds, secure=True, httponly=True,
                        samesite='lax', path='/')


def install(app, connection_factory, now, provider=None):
    provider = provider or GitHubProvider()

    @app.get('/auth/github')
    def begin():
        if mode() != 'github':
            raise HTTPException(403, 'GitHub sign-in disabled')
        config = settings()
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with connection_factory() as conn:
            conn.execute('''INSERT INTO oauth_flows(state_digest, browser_digest, pkce_verifier, expires_at)
                VALUES (%s, %s, %s, %s)''', (digest(state), digest(browser), verifier, now() + timedelta(minutes=10)))
        response = RedirectResponse('https://github.com/login/oauth/authorize?' + urlencode({
            'client_id': config['ARENA_GITHUB_CLIENT_ID'], 'redirect_uri': config['ARENA_GITHUB_CALLBACK_URL'],
            'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}), status_code=302)
        _cookie(response, FLOW_COOKIE, browser, 600)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/auth/github/callback')
    def callback(request: Request, state: str = '', code: str = ''):
        if mode() != 'github':
            raise HTTPException(403, 'GitHub sign-in disabled')
        config = settings()
        browser = request.cookies.get(FLOW_COOKIE, '')
        if not state or not code or not browser or max(len(state), len(code), len(browser)) > 512:
            raise HTTPException(400, 'Invalid sign-in callback')
        # Consume state/code before contacting the provider, even when exchange
        # fails. A concurrent/replayed callback cannot make a second exchange.
        with connection_factory() as conn:
            flow = conn.execute('SELECT * FROM oauth_flows WHERE state_digest = %s FOR UPDATE',
                                (digest(state),)).fetchone()
            if (flow is None or flow['used_at'] is not None or flow['expires_at'] <= now()
                    or not hmac.compare_digest(flow['browser_digest'], digest(browser))
                    or not isinstance(flow['pkce_verifier'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', flow['pkce_verifier'])):
                raise HTTPException(400, 'Invalid or expired sign-in state')
            if conn.execute('SELECT 1 FROM oauth_flows WHERE code_digest = %s', (digest(code),)).fetchone():
                raise HTTPException(400, 'Sign-in code already used')
            try:
                # Transaction lock prevents different states racing the same code.
                conn.execute('SELECT pg_advisory_xact_lock(73423191)')
                if conn.execute('SELECT 1 FROM oauth_flows WHERE code_digest = %s', (digest(code),)).fetchone():
                    raise HTTPException(400, 'Sign-in code already used')
                conn.execute('UPDATE oauth_flows SET used_at = %s, code_digest = %s, pkce_verifier = NULL WHERE state_digest = %s',
                             (now(), digest(code), digest(state)))
            except HTTPException:
                raise
        try:
            identifier = provider.exchange(code, flow['pkce_verifier'], config)
            if not isinstance(identifier, str) or not re.fullmatch(r'[0-9]+', identifier):
                raise ValueError('invalid identity')
        except Exception:
            raise HTTPException(400, 'GitHub sign-in failed') from None
        identity_hash = hmac.new(config['ARENA_IDENTITY_SECRET'].encode(),
            ('github:' + identifier).encode(), hashlib.sha256).hexdigest()
        with connection_factory() as conn:
            account = conn.execute('''INSERT INTO voters(id, provider, provider_id_hash, created_at)
                VALUES (%s, 'github', %s, %s) ON CONFLICT (provider, provider_id_hash)
                DO UPDATE SET provider_id_hash = EXCLUDED.provider_id_hash RETURNING id''',
                ('voter-' + uuid4().hex, identity_hash, now())).fetchone()
            raw_session = issue_session(conn, account['id'], now())
        response = RedirectResponse('/', status_code=303)
        _cookie(response, SESSION_COOKIE, raw_session, SESSION_SECONDS)
        response.delete_cookie(FLOW_COOKIE, secure=True, httponly=True, samesite='lax', path='/')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/auth/session')
    def session_info(request: Request):
        if mode() == 'dev':
            return JSONResponse({'mode': 'dev', 'github_enabled': github_enabled()}, headers={'Cache-Control': 'no-store'})
        from arena.guest import available
        options = {'guest_enabled': available(), 'github_enabled': github_enabled()}
        with connection_factory() as conn:
            try:
                raw, session = _session(conn, request, now())
                account = conn.execute('SELECT provider FROM voters WHERE id=%s', (session['voter_id'],)).fetchone()
                if account is None or (account['provider'] == 'guest' and not options['guest_enabled']):
                    raise HTTPException(401, 'Sign in to vote')
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                return JSONResponse({'mode': 'github', 'authenticated': False, **options}, headers={'Cache-Control': 'no-store'})
        return JSONResponse({'mode': 'github', 'authenticated': True, **options, 'csrf_token': csrf_token(raw),
            'adult_confirmed': session['adult_confirmed'], 'guest_session': account['provider'] == 'guest'}, headers={'Cache-Control': 'no-store'})

    @app.post('/auth/confirm-age')
    def confirm_age(request: Request):
        if mode() != 'github':
            raise HTTPException(403, 'GitHub sign-in required')
        with connection_factory() as conn:
            raw, session = _session(conn, request, now())
            require_csrf(request, raw)
            conn.execute('UPDATE voter_sessions SET adult_confirmed = true WHERE token_digest = %s',
                         (session['token_digest'],))
        return {'ok': True}
