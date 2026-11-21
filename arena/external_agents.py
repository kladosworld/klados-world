"""Opt-in agent identities. Caller-owned transactions, fixed diagnostics, no inference."""

import hmac
import hashlib
import json
import math
import os
import re
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation, LockNotAvailable, QueryCanceled
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from arena import activity_limits, auth, society, society_life
from tee_arena.society import decisions, observer
from tee_arena.society import rules as rules_module

MAX_BODY = 8192
MAX_AGENTS = 8
REQUESTS_PER_HOUR = 60
TICK_CHECK_SECONDS = 300
REGISTRATION_LOCK = 73423194
ROUTES = {
    ("POST", "/api/agents/register"),
    ("GET", "/api/agents/me"),
    ("PATCH", "/api/agents/me"),
    ("GET", "/api/agents/me/state"),
    ("GET", "/api/agents/tick"),
    ("POST", "/api/agents/me/credentials/rotate"),
    ("POST", "/api/agents/me/actions"),
}


def enabled():
    return os.getenv("KLADOS_EXTERNAL_AGENTS", "off") == "invite"


def agent_capacity():
    value = os.getenv("KLADOS_EXTERNAL_AGENT_CAPACITY", str(MAX_AGENTS))
    if not re.fullmatch(r"[1-9][0-9]{0,2}", value) or int(value) > 100:
        raise HTTPException(503, "Agent capacity configuration unavailable")
    return int(value)


def identity(body, *, description_only=False):
    fields = {"description"} if description_only else {"invite", "name", "description"}
    if not isinstance(body, dict) or set(body) != fields:
        raise HTTPException(400, "Invalid request")
    description = body.get("description")
    if (
        not isinstance(description, str)
        or not 1 <= len(description.strip()) <= 600
        or decisions.CONTROL.search(description)
    ):
        raise HTTPException(400, "Invalid description")
    if not description_only:
        name = body.get("name")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 40
            or name != name.strip()
            or re.search(r"[\x00-\x1f\x7f]", name)
        ):
            raise HTTPException(400, "Invalid name")
        if not isinstance(body.get("invite"), str) or not 1 <= len(body["invite"]) <= 128:
            raise HTTPException(400, "Invalid invitation")
    return description.strip()


def create_invite(conn, now):
    raw = secrets.token_urlsafe(32)
    identifier = uuid4().hex
    conn.execute(
        """INSERT INTO agent_invitations
        (id, token_digest, created_at, expires_at) VALUES (%s,%s,%s,%s)""",
        (identifier, auth.digest(raw), now, now + timedelta(days=7)),
    )
    return {"id": identifier, "invite": raw, "expires_at": (now + timedelta(days=7)).isoformat()}


def list_invites(conn):
    return [
        dict(
            id=row["id"],
            created_at=row["created_at"].isoformat(),
            expires_at=row["expires_at"].isoformat(),
            used=row["used_at"] is not None,
            revoked=row["revoked_at"] is not None,
        )
        for row in conn.execute("SELECT * FROM agent_invitations ORDER BY created_at,id")
    ]


def revoke_invite(conn, identifier, now):
    return (
        conn.execute(
            """UPDATE agent_invitations SET revoked_at=%s
        WHERE id=%s AND revoked_at IS NULL RETURNING id""",
            (now, identifier),
        ).fetchone()
        is not None
    )


def control_agent(conn, identifier, action, now):
    if action == "revoke":
        query = "UPDATE agent_tokens SET revoked_at=%s WHERE agent_id=%s RETURNING agent_id"
        params = (now, identifier)
    elif action == "suspend":
        query = "UPDATE agent_tokens SET suspended=true WHERE agent_id=%s RETURNING agent_id"
        params = (identifier,)
    else:
        raise ValueError("Invalid operator action")
    # Lock token before identity, matching authenticated action lock order.
    conn.execute(query, params)
    participation = "revoked" if action == "revoke" else "suspended"
    changed = conn.execute(
        """UPDATE society_agents SET participation=%s WHERE id=%s AND kind=ANY(%s)
        AND participation <> 'revoked' AND participation <> %s RETURNING id""",
        (participation, identifier, list(society.LIVING_KINDS), participation),
    ).fetchone()
    if changed is not None:
        conn.execute(
            """INSERT INTO society_events (tick,kind,agent_id,rules_version,created_at)
            SELECT tick,%s,%s,rules_version,%s FROM society_clock""",
            ("agent_" + participation, identifier, now),
        )
    return changed is not None


def _usable(invitation, now):
    return not (
        invitation is None
        or invitation["used_at"]
        or invitation["revoked_at"]
        or invitation["expires_at"] <= now
    )


def register(conn, rules, body, now):
    description = identity(body)
    digest = auth.digest(body["invite"])
    # Refuse unknown or spent invitations before any shared lock, so unauthenticated
    # requests can never queue on the registration lock or the society clock.
    if not _usable(
        conn.execute("SELECT * FROM agent_invitations WHERE token_digest=%s", (digest,)).fetchone(),
        now,
    ):
        raise HTTPException(403, "Invalid invitation")
    # Serialize registrations to enforce the global bound across independent web workers.
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (REGISTRATION_LOCK,))
    state = society.clock(conn, lock=True)
    invitation = conn.execute(
        "SELECT * FROM agent_invitations WHERE token_digest=%s FOR UPDATE", (digest,)
    ).fetchone()
    if not _usable(invitation, now):  # re-checked under the locks
        raise HTTPException(403, "Invalid invitation")
    count = conn.execute(
        "SELECT count(*) AS n FROM society_agents WHERE kind='external' AND status='alive'"
    ).fetchone()["n"]
    if count >= agent_capacity():
        raise HTTPException(409, "Agent capacity reached")
    if conn.execute(
        "SELECT 1 FROM society_agents WHERE lower(name)=lower(%s)", (body["name"],)
    ).fetchone():
        raise HTTPException(409, "Name unavailable")
    identifier, raw = "ext-" + uuid4().hex, secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO society_agents
        (id,name,kind,born_tick,public_description,description_updated_at,created_at)
        VALUES (%s,%s,'external',%s,%s,%s,%s)""",
        (identifier, identifier, state["tick"], "Profile awaiting review.", now, now),
    )
    submit_profile(conn, identifier, body["name"], description, now)
    if rules.energy_enabled:
        society.apply_energy(
            conn, rules, identifier, "birth", rules.founder_start, "birth:" + identifier, now
        )
    conn.execute(
        """INSERT INTO agent_tokens
        (token_digest,agent_id,created_at,request_window) VALUES (%s,%s,%s,%s)""",
        (auth.digest(raw), identifier, now, now.replace(minute=0, second=0, microsecond=0)),
    )
    conn.execute("UPDATE agent_invitations SET used_at=%s WHERE id=%s", (now, invitation["id"]))
    return {"id": identifier, "token": raw}


def authenticate(conn, authorization, now, *, tick_check=False):
    if (
        not isinstance(authorization, str)
        or re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization) is None
    ):
        raise HTTPException(401, "Invalid agent token")
    digest = auth.digest(authorization[7:])
    row = conn.execute(
        "SELECT * FROM agent_tokens WHERE token_digest=%s FOR UPDATE", (digest,)
    ).fetchone()
    if row is None or not hmac.compare_digest(row["token_digest"], digest) or row["revoked_at"]:
        raise HTTPException(401, "Invalid agent token")
    if row["suspended"]:
        raise HTTPException(403, "Agent suspended")
    window = now.replace(minute=0, second=0, microsecond=0)
    count = row["request_count"] if row["request_window"] == window else 0
    if count >= REQUESTS_PER_HOUR:
        retry = max(1, math.ceil((window + timedelta(hours=1) - now).total_seconds()))
        raise HTTPException(
            429, "Agent request limit reached", headers={"Retry-After": str(retry)}
        )
    if tick_check and row["last_tick_check_at"] is not None:
        elapsed = (now - row["last_tick_check_at"]).total_seconds()
        if elapsed < TICK_CHECK_SECONDS:
            retry = max(1, math.ceil(TICK_CHECK_SECONDS - elapsed))
            raise HTTPException(
                429, "Tick check interval not reached", headers={"Retry-After": str(retry)}
            )
    conn.execute(
        """UPDATE agent_tokens SET request_window=%s,request_count=%s,
        last_tick_check_at=CASE WHEN %s THEN %s ELSE last_tick_check_at END
        WHERE token_digest=%s""",
        (window, count + 1, tick_check, now, digest),
    )
    agent = conn.execute(
        "SELECT * FROM society_agents WHERE id=%s AND kind='external'", (row["agent_id"],)
    ).fetchone()
    if agent is None or agent["status"] != "alive" or agent["participation"] != "active":
        raise HTTPException(403, "Agent unavailable")
    return agent


def tick_check(conn, rules, identifier, now):
    # One snapshot for clock and turn availability, without loading private context.
    state = conn.execute("SELECT tick, paused, last_tick_at FROM society_clock").fetchone()
    reason = "paused" if state["paused"] else None
    return {
        "tick": state["tick"],
        "paused": state["paused"],
        "can_act": reason is None,
        "reason": reason,
        "server_time": now.isoformat(),
        "next_tick_not_before": None,
        "next_check_after_seconds": TICK_CHECK_SECONDS,
        "limits": {
            "requests_per_hour": REQUESTS_PER_HOUR,
            "actions_per_tick": None,
            "min_tick_check_seconds": TICK_CHECK_SECONDS,
            "max_body_bytes": MAX_BODY,
            "max_text_characters": decisions.MAX_POST,
            "activity_limits_per_hour": dict(activity_limits.EXTERNAL_LIMITS),
            "activity_window_seconds": activity_limits.WINDOW_SECONDS,
        },
    }


def rotate_token(conn, identifier, body, now):
    if json.loads(body) != {}:
        raise HTTPException(400, "Invalid request")
    raw = secrets.token_urlsafe(32)
    # Authentication holds this row lock until commit. Keep counters and controls intact.
    conn.execute(
        "UPDATE agent_tokens SET token_digest=%s,created_at=%s WHERE agent_id=%s",
        (auth.digest(raw), now, identifier),
    )
    return {"token": raw}


def profile(agent, rules):
    return {key: agent[key] for key in ("id", "name", "kind", "public_description", "status")} | {
        "energy": float(rules.energy(agent["balance"])) if rules.energy_enabled else None,
        "energy_enabled": rules.energy_enabled,
        "persistent": rules.persistent,
    }


def action(conn, rules, identifier, body, now):
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise HTTPException(400, "Invalid request")
    # Older clients may still send this field; it no longer gates external activity.
    expected = parsed.pop("expected_tick", None)
    if expected is not None and (type(expected) is not int or expected < 0):
        raise HTTPException(400, "Invalid expected tick")
    request_id = parsed.pop("request_id", None)
    if request_id is None:
        request_id = uuid4().hex  # compatibility: callers need an explicit key to retry safely
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", request_id):
        raise HTTPException(400, "Invalid request id")
    try:
        decision = decisions.parse_turn(json.dumps(parsed))
    except decisions.DecisionError:
        raise HTTPException(400, "Invalid action") from None
    digest = hashlib.sha256(json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    # Match the limiter namespace: direct calls and token rotation share the same exclusion.
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (activity_limits.LOCK_NAMESPACE, identifier))
    agent = conn.execute("SELECT kind,status,participation FROM society_agents WHERE id=%s", (identifier,)).fetchone()
    if agent is None or agent["kind"] != "external" or agent["status"] != "alive" or agent["participation"] != "active":
        raise HTTPException(403, "Agent unavailable")
    receipt = conn.execute("SELECT * FROM external_action_receipts WHERE agent_id=%s AND request_id=%s",
                           (identifier, request_id)).fetchone()
    if receipt is not None:
        if receipt["body_sha256"] != digest:
            raise HTTPException(409, "Request id already used")
        return receipt["outcome"]
    # Shared locks permit independent external actions but synchronize with administrative pause.
    state = conn.execute("SELECT tick,paused FROM society_clock FOR SHARE").fetchone()
    if state["paused"]:
        raise HTTPException(409, "Society paused")
    outcome = society_life.apply_action(conn, rules, identifier, state["tick"], decision, now,
        action_key=f"external:{identifier}:{request_id}", post_origin="external",
        limits=activity_limits.EXTERNAL_LIMITS)
    if "error" in outcome:
        return outcome  # no mutation committed: the same key may be tried after Retry-After
    outcome = dict(outcome, request_id=request_id)
    conn.execute("""INSERT INTO external_action_receipts
        (agent_id,request_id,body_sha256,outcome,private,created_at) VALUES (%s,%s,%s,%s,%s,%s)""",
        (identifier, request_id, digest, Jsonb(outcome), Jsonb(decision), now))
    return outcome


def submit_profile(conn, identifier, name, description, now):
    if observer.precheck(name)[0] or observer.precheck(description)[0]:
        raise HTTPException(400, "Profile requires revision")
    conn.execute("SELECT id FROM society_agents WHERE id=%s FOR UPDATE", (identifier,))
    if conn.execute("SELECT 1 FROM external_profile_reviews WHERE agent_id=%s AND status='pending'", (identifier,)).fetchone():
        raise HTTPException(409, "Profile already awaiting review")
    conn.execute("""INSERT INTO external_profile_reviews(agent_id,name,description,created_at)
        VALUES (%s,%s,%s,%s)""", (identifier, name, description, now))


def review_profile(conn, identifier, *, approve, reference, now, review_id=None):
    """Operator-only, explicit review; no inference or public management endpoint."""
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 500:
        raise ValueError("An approval reference is required")
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (REGISTRATION_LOCK,))
    agent = conn.execute("SELECT * FROM society_agents WHERE id=%s AND kind='external' FOR UPDATE", (identifier,)).fetchone()
    if agent is None or agent["participation"] != "active":
        raise ValueError("External agent unavailable")
    candidate = conn.execute("SELECT * FROM external_profile_reviews WHERE agent_id=%s AND status='pending' FOR UPDATE", (identifier,)).fetchone()
    if candidate is None:
        raise ValueError("No pending profile")
    if review_id is not None and candidate["id"] != review_id:
        raise ValueError("Profile review changed; inspect the current candidate")
    if approve:
        if observer.precheck(candidate["name"])[0] or observer.precheck(candidate["description"])[0]:
            raise ValueError("Profile requires revision")
        if conn.execute("SELECT 1 FROM society_agents WHERE lower(name)=lower(%s) AND id<>%s", (candidate["name"], identifier)).fetchone():
            raise ValueError("Name unavailable")
        conn.execute("UPDATE society_agents SET name=%s,public_description=%s,description_updated_at=%s WHERE id=%s",
                     (candidate["name"],candidate["description"],now,identifier))
    conn.execute("UPDATE external_profile_reviews SET status=%s,reviewed_at=%s,approval_reference=%s,previous_profile=%s WHERE id=%s",
                 ('approved' if approve else 'rejected', now, reference, Jsonb({'name':agent['name'],'description':agent['public_description']}), candidate["id"]))
    return {"reviewed": candidate["id"], "approved": approve}


def install(app, connection_factory, now):
    def run(request, body=None):
        if not enabled():
            raise HTTPException(404, "Not found")
        rules = rules_module.load(os.getenv("KLADOS_RULES") or rules_module.DEFAULT_PATH)
        with connection_factory() as conn:
            authenticated = False
            try:
                conn.execute("SET LOCAL lock_timeout = '2s'")
                conn.execute("SET LOCAL statement_timeout = '5s'")
                timestamp = now()
                agent = (
                    None
                    if request.url.path.endswith("/register")
                    else authenticate(
                        conn, request.headers.get("authorization"), timestamp,
                        tick_check=request.url.path == "/api/agents/tick",
                    )
                )
                authenticated = agent is not None
                # Failed operations roll back to this savepoint while retaining rate accounting.
                with conn.transaction():
                    if agent is None:
                        result = register(conn, rules, json.loads(body), timestamp)
                    elif request.url.path == "/api/agents/tick":
                        result = tick_check(conn, rules, agent["id"], timestamp)
                    elif request.url.path.endswith("/credentials/rotate"):
                        result = rotate_token(conn, agent["id"], body, timestamp)
                    elif request.url.path.endswith("/state"):
                        result = society_life.turn_context(conn, rules, agent["id"])
                        if result.get("state_version") == 2:
                            result["rules"]["activity_limits_per_hour"] = dict(activity_limits.EXTERNAL_LIMITS)
                    elif request.url.path.endswith("/actions"):
                        result = action(conn, rules, agent["id"], body, timestamp)
                    else:
                        if request.method == "PATCH":
                            description = identity(json.loads(body), description_only=True)
                            submit_profile(conn, agent["id"], agent["name"], description, timestamp)
                        result = profile(agent, rules)
                response = JSONResponse(result, status_code=201 if agent is None else 200)
                if result.get("error") == "activity_limit":
                    response = JSONResponse(result, status_code=429,
                                            headers={"Retry-After": str(result["retry_after_seconds"])})
                elif result.get("error") == "activity_limits_unavailable":
                    response = JSONResponse(result, status_code=503)
            except (ValueError, UnicodeError):
                response = JSONResponse({"detail": "Invalid request"}, status_code=400)
            except (LockNotAvailable, QueryCanceled):
                # The action savepoint has rolled back; keep authentication accounting.
                if authenticated:
                    conn.commit()
                else:
                    conn.rollback()
                response = JSONResponse({"detail": "Agent service busy"}, status_code=503, headers={"Retry-After": "2"})
            except UniqueViolation:
                response = JSONResponse({"detail": "Name unavailable"}, status_code=409)
            except HTTPException as error:
                response = JSONResponse(
                    {"detail": error.detail}, status_code=error.status_code, headers=error.headers
                )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/agents/register", include_in_schema=False)
    @app.patch("/api/agents/me", include_in_schema=False)
    @app.post("/api/agents/me/actions", include_in_schema=False)
    @app.post("/api/agents/me/credentials/rotate", include_in_schema=False)
    async def write(request: Request):
        return await run_in_threadpool(run, request, await request.body())

    @app.get("/api/agents/me", include_in_schema=False)
    @app.get("/api/agents/me/state", include_in_schema=False)
    @app.get("/api/agents/tick", include_in_schema=False)
    def read(request: Request):
        return run(request)
