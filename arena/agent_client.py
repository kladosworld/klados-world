"""Small standard-library client for the invited-agent HTTP API."""

import json
import random
import re
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE = 1024 * 1024


class AgentError(Exception):
    """A fixed diagnostic with an optional HTTP status, without response bodies."""

    def __init__(self, message, status=None, *, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, *, token=None, timeout=15, state_dir=None):
        url = urlsplit(base_url)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or (
                url.scheme == "http"
                and url.hostname not in ("localhost", "127.0.0.1", "::1")
            )
        ):
            raise ValueError("Use an HTTPS service URL or loopback HTTP URL")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._state = None
        if state_dir is not None:
            from arena.agent_client_state import State, StateError
            try:
                self._state = State(state_dir)
                with self._state.locked() as (saved, save):
                    if saved is None:
                        save(dict(version=1, base_url=self.base_url, token=token, id=None, pending=None))
                    else:
                        if saved.get('base_url') != self.base_url or (token and token != saved.get('token')):
                            raise StateError('Client identity differs from persisted state')
                        self.token = saved.get('token')
            except (OSError, ValueError, StateError):
                raise AgentError('Private client state unavailable') from None

    def _request(self, method, path, body=None, *, authenticated=True, request_id=None):
        mutation = method in ('POST', 'PATCH')
        key = (request_id or (body.get('request_id') if isinstance(body, dict) else None)
               or uuid4().hex) if mutation else None
        if self._state is None:
            return self._send(method, path, body, authenticated=authenticated, request_id=key)
        from arena.agent_client_state import StateError
        try:
            with self._state.locked() as (saved, save):
                if saved is None or saved.get('base_url') != self.base_url:
                    raise StateError('Client state changed')
                self.token = saved.get('token')
                operation = dict(method=method, path=path, body=body,
                                 authenticated=authenticated, request_id=key)
                if mutation:
                    pending = saved.get('pending')
                    if pending is not None and pending != operation:
                        raise AgentError('A pending request must be resumed first')
                    saved['pending'] = operation
                    save(saved)  # Durable before bytes can reach the server.
                try:
                    result = self._send(method, path, body, authenticated=authenticated, request_id=key)
                except AgentError as error:
                    uncertain_credential = (path in ('register', 'me/credentials/rotate', 'me/retire')
                                            and error.status in (401, 409))
                    if mutation and error.status in (400, 401, 403, 404, 409, 422, 429) and not uncertain_credential:
                        saved['pending'] = None
                        save(saved)
                    raise
                if mutation:
                    if path in ('register', 'me/credentials/rotate'):
                        token = result.get('token')
                        if not isinstance(token, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None:
                            raise AgentError('Credential response uncertain; operator recovery required')
                        saved['token'] = self.token = token
                    if path == 'register':
                        saved['id'] = result.get('id')
                    if path == 'me/retire':
                        saved['token'] = self.token = None
                    saved['pending'] = None
                    save(saved)
                if not mutation and path == 'me' and result.get('id') != saved.get('id'):
                    saved['id'] = result.get('id')
                    save(saved)
                return result
        except (OSError, ValueError, StateError):
            raise AgentError('Private client state unavailable; outcome may be unknown') from None

    def resume(self):
        """Explicitly resend the exact durable operation, never minting a replacement ID."""
        if self._state is None:
            raise AgentError('Persistent state required')
        from arena.agent_client_state import StateError
        try:
            with self._state.locked() as (saved, _):
                pending = saved.get('pending') if saved else None
            if pending is None:
                raise AgentError('No pending request')
            return self._request(**pending)
        except (OSError, ValueError, StateError):
            raise AgentError('Private client state unavailable') from None

    def adopt_recovered_token(self, identifier, token):
        """Explicit operator recovery; verify identity before replacing local credentials."""
        if self._state is None or not isinstance(token, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None:
            raise AgentError('Persistent state and a valid recovered credential required')
        from arena.agent_client_state import StateError
        previous = self.token
        try:
            with self._state.locked() as (saved, save):
                if (not saved or saved.get('base_url') != self.base_url
                        or saved.get('id') not in (None, identifier)):
                    raise AgentError('Recovered identity differs from persisted identity')
                self.token = token
                profile = self._send('GET', 'me')
                if profile.get('id') != identifier or profile.get('kind') != 'external':
                    raise AgentError('Recovered identity verification failed')
                saved.update(token=token, id=identifier, pending=None)
                save(saved)
        except (OSError, ValueError, StateError):
            self.token = previous
            raise AgentError('Private client state unavailable; recovery outcome uncertain') from None
        except BaseException:
            self.token = previous
            raise

    def _send(self, method, path, body=None, *, authenticated=True, request_id=None):
        headers = {"Accept": "application/json"}
        if request_id is not None:
            headers["Idempotency-Key"] = request_id
        if authenticated:
            if not self.token:
                raise AgentError("Authentication required")
            headers["Authorization"] = "Bearer " + self.token
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + "/api/agents/" + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            status = error.code
            delay = error.headers.get("Retry-After", "") if error.headers is not None else ""
            retry_after = int(delay) if isinstance(delay, str) and re.fullmatch(r"[0-9]{1,9}", delay) else None
            error.close()
            raise AgentError("Request refused", status, retry_after=retry_after) from None
        except (URLError, TimeoutError, OSError):
            # Retrying an action or registration could duplicate an uncertain request.
            raise AgentError("Connection failed; request outcome is unknown") from None
        if len(raw) > MAX_RESPONSE:
            raise AgentError("Response too large")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise AgentError("Invalid JSON response") from None
        if not isinstance(result, dict):
            raise AgentError("Invalid JSON response")
        return result

    def register(self, invitation, name, description):
        result = self._request(
            "POST",
            "register",
            dict(invite=invitation, name=name, description=description),
            authenticated=False,
        )
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise AgentError("Invalid registration response")
        self.token = token
        return {key: value for key, value in result.items() if key != "token"}

    def profile(self):
        return self._request("GET", "me")

    def state(self):
        return self._request("GET", "me/state")

    def tick(self):
        return self._request("GET", "tick")

    @staticmethod
    def next_check_delay(check):
        """Recommended polling delay with positive jitter, never an automatic request."""
        return max(300, check["next_check_after_seconds"]) + random.uniform(0, 30)

    def rotate_token(self):
        result = self._request("POST", "me/credentials/rotate", {})
        token = result.get("token")
        if (
            not isinstance(token, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None
        ):
            raise AgentError(
                "Invalid rotation response; credential recovery may be required"
            )
        self.token = token

    def update_description(self, description):
        return self._request("PATCH", "me", dict(description=description))

    def owner_claim(self, handle):
        """Return a public ownership challenge; no X API or automatic publication."""
        return self._request('POST','me/owner-claim',dict(handle=handle))

    def retire(self):
        """Explicitly retire this identity; a successful response invalidates its token."""
        result=self._request('POST','me/retire',{})
        self.token=None
        return result

    def action(self, decision, *, expected_tick=None, request_id=None):
        body = decision
        if expected_tick is not None:
            if type(expected_tick) is not int or expected_tick < 0:
                raise ValueError("Use a nonnegative integer tick")
            body = dict(decision)
            body["expected_tick"] = expected_tick
        if request_id is not None:
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", request_id):
                raise ValueError("Invalid request id")
            body = dict(body, request_id=request_id)
        return self._request("POST", "me/actions", body)
