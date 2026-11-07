"""Turnstile-gated guest sessions. No IP address or raw verification token stored."""
from datetime import timedelta
import hmac
import os
import re
import secrets
from uuid import uuid4
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from arena import auth

ORIGIN = 'https://challenges.cloudflare.com'
COOKIE = '__Host-arena-guest'


def configuration():
    if auth.mode() != 'github' or os.getenv('ARENA_GUEST_VOTING') != '1':
        raise HTTPException(403, 'Guest voting is disabled')
    sitekey = os.getenv('ARENA_TURNSTILE_SITEKEY', '')
    secret = os.getenv('ARENA_TURNSTILE_SECRET', '')
    auth.identity_secret()
    auth.guest_vote_weight()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', sitekey) or not secret:
        raise HTTPException(503, 'Guest voting is not configured')
    return sitekey, secret


def available():
    try:
        configuration()
        return True
    except HTTPException:
        return False


def content_security_policy():
    if available():
        return "default-src 'self'; script-src 'self' " + ORIGIN + '; frame-src ' + ORIGIN
    return "default-src 'self'"


class TurnstileVerifier:
    def verify(self, token, secret, hostname):
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                response = client.post(ORIGIN + '/turnstile/v0/siteverify',
                                       data={'secret': secret, 'response': token})
                response.raise_for_status()
                result = response.json()
            return (isinstance(result, dict) and result.get('success') is True
                    and result.get('hostname') == hostname and result.get('action') == 'guest-vote')
        except (httpx.HTTPError, ValueError, TypeError):
            return False


class GuestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(min_length=1, max_length=2048)
    adult_confirmed: StrictBool


def install(app, connect, now, verifier=None):
    verifier = verifier or TurnstileVerifier()

    @app.get('/auth/guest')
    def begin_guest():
        sitekey, _ = configuration()
        state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with connect() as conn:
            conn.execute('INSERT INTO guest_challenges(state_digest, browser_digest, expires_at) VALUES (%s,%s,%s)',
                         (auth.digest(state), auth.digest(browser), now() + timedelta(minutes=10)))
        response = JSONResponse({'sitekey': sitekey, 'csrf_token': state}, headers={'Cache-Control': 'no-store'})
        auth._cookie(response, COOKIE, browser, 600)
        return response

    @app.post('/auth/guest')
    def finish_guest(request: Request, payload: GuestInput):
        _, secret = configuration()
        if payload.adult_confirmed is not True:
            raise HTTPException(403, 'Confirm you are 18 or older')
        state = request.headers.get('X-CSRF-Token', '')
        browser = request.cookies.get(COOKIE, '')
        if not state or not browser or max(len(state), len(browser)) > 256:
            raise HTTPException(403, 'Guest challenge required')
        with connect() as conn:
            if request.cookies.get(auth.SESSION_COOKIE):
                try:
                    auth._session(conn, request, now())
                except HTTPException as exc:
                    if exc.status_code != 401:
                        raise
                else:
                    raise HTTPException(409, 'A voting session is already active')
            # One-use browser-bound challenge and local token digest, consumed
            # before external verification, even if the provider rejects it.
            conn.execute('SELECT pg_advisory_xact_lock(73423192)')
            challenge = conn.execute('SELECT * FROM guest_challenges WHERE state_digest=%s FOR UPDATE',
                                     (auth.digest(state),)).fetchone()
            if (challenge is None or challenge['used_at'] is not None or challenge['expires_at'] <= now()
                    or not hmac.compare_digest(challenge['browser_digest'], auth.digest(browser))):
                raise HTTPException(403, 'Invalid or expired guest challenge')
            if conn.execute('SELECT 1 FROM guest_challenges WHERE token_digest=%s', (auth.digest(payload.token),)).fetchone():
                raise HTTPException(400, 'Guest verification failed')
            conn.execute('UPDATE guest_challenges SET used_at=%s, token_digest=%s, expires_at=greatest(expires_at,%s) WHERE state_digest=%s',
                         (now(), auth.digest(payload.token), now() + timedelta(minutes=10), auth.digest(state)))
        try:
            verified = verifier.verify(payload.token, secret, request.url.hostname)
        except Exception:
            verified = False
        if verified is not True:
            raise HTTPException(400, 'Guest verification failed')
        with connect() as conn:
            voter_id = 'voter-' + uuid4().hex
            conn.execute("INSERT INTO voters(id, provider, created_at) VALUES (%s, 'guest', %s)", (voter_id, now()))
            raw = auth.issue_session(conn, voter_id, now(), adult=True)
        response = JSONResponse({'ok': True}, headers={'Cache-Control': 'no-store'})
        auth._cookie(response, auth.SESSION_COOKIE, raw, auth.SESSION_SECONDS)
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='lax', path='/')
        return response
