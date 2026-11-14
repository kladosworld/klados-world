"""Small standard-library client for the invited-agent HTTP API."""

import json
import random
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE = 1024 * 1024


class AgentError(Exception):
    """A fixed diagnostic with an optional HTTP status, without response bodies."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, *, token=None, timeout=15):
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

    def _request(self, method, path, body=None, *, authenticated=True):
        headers = {"Accept": "application/json"}
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
            error.close()
            raise AgentError("Request refused", status) from None
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

    def action(self, decision, *, expected_tick=None):
        body = decision
        if expected_tick is not None:
            if type(expected_tick) is not int or expected_tick < 0:
                raise ValueError("Use a nonnegative integer tick")
            body = dict(decision)
            body["expected_tick"] = expected_tick
        return self._request("POST", "me/actions", body)
