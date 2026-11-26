-- Empty W0 storage, plus the approved institutional text constraint exception.
-- No seed, UPDATE, DELETE, or row rewrite. ALTER/validation requires maintenance.
CREATE TABLE society_organizations (
 id bigserial PRIMARY KEY, name text NOT NULL CHECK (length(name) BETWEEN 3 AND 60),
 charter_text text NOT NULL CHECK (length(charter_text) BETWEEN 1 AND 2000),
 charter_version integer NOT NULL DEFAULT 1 CHECK (charter_version > 0),
 join_policy text NOT NULL CHECK (join_policy IN ('open','by_vote')),
 default_rule jsonb NOT NULL,
 created_by text NOT NULL REFERENCES society_agents(id), created_tick bigint NOT NULL,
 status text NOT NULL DEFAULT 'pending_clearance'
   CHECK (status IN ('pending_clearance','active','dissolved','withheld')),
 activated_tick bigint, dissolved_tick bigint
);
CREATE TABLE society_org_events (
 id bigserial PRIMARY KEY, org_id bigint NOT NULL REFERENCES society_organizations(id),
 kind text NOT NULL CHECK (kind IN ('founded','joined','left','removed','role_assigned',
   'role_revoked','leader_changed','charter_amended','dissolved','decision_applied')),
 actor_id text REFERENCES society_agents(id), subject_id text REFERENCES society_agents(id),
 data jsonb NOT NULL DEFAULT '{}'::jsonb, tick bigint NOT NULL,
 created_at timestamptz NOT NULL, action_key text UNIQUE
);
CREATE INDEX society_org_events_actor ON society_org_events(actor_id, created_at);
CREATE TABLE society_memberships (
 org_id bigint NOT NULL REFERENCES society_organizations(id),
 agent_id text NOT NULL REFERENCES society_agents(id), role text NOT NULL,
 joined_tick bigint NOT NULL, left_tick bigint,
 PRIMARY KEY (org_id,agent_id,joined_tick)
);
CREATE TABLE society_trust (
 id bigserial PRIMARY KEY, from_id text NOT NULL REFERENCES society_agents(id),
 to_id text NOT NULL REFERENCES society_agents(id),
 stance text NOT NULL CHECK (stance IN ('trust','distrust','neutral')),
 reason_text text CHECK (length(reason_text) <= 280), risk jsonb NOT NULL DEFAULT '{}'::jsonb,
 tick bigint NOT NULL, created_at timestamptz NOT NULL,
 action_key text NOT NULL UNIQUE, request_hash text NOT NULL,
 CHECK (from_id <> to_id)
);
CREATE INDEX society_trust_pair ON society_trust(from_id,to_id,id);
CREATE TABLE society_proposals (
 id bigserial PRIMARY KEY, scope_kind text NOT NULL CHECK (scope_kind='organization'),
 scope_id bigint NOT NULL REFERENCES society_organizations(id),
 author_id text NOT NULL REFERENCES society_agents(id),
 kind text NOT NULL CHECK (kind IN ('admit_member','remove_member','assign_role','revoke_role',
   'change_leader','amend_charter','set_rule','adopt_forum','resolution','case_ruling','dissolve')),
 payload jsonb NOT NULL, text text NOT NULL, options jsonb NOT NULL,
 opens_tick bigint, closes_tick bigint, rule jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending_clearance' CHECK (status IN
   ('pending_clearance','open','passed','failed','cancelled','applied','withheld')),
 result jsonb
);
CREATE INDEX society_proposals_due ON society_proposals(closes_tick) WHERE status='open';
CREATE TABLE society_votes (
 id bigserial UNIQUE, proposal_id bigint NOT NULL REFERENCES society_proposals(id),
 voter_id text NOT NULL REFERENCES society_agents(id), choice text NOT NULL,
 tick bigint NOT NULL, created_at timestamptz NOT NULL, action_key text NOT NULL UNIQUE,
 request_hash text NOT NULL DEFAULT '',
 PRIMARY KEY(proposal_id,voter_id)
);
CREATE INDEX society_votes_actor ON society_votes(voter_id,created_at);
CREATE TABLE society_commitments (
 id bigserial PRIMARY KEY, author_id text NOT NULL REFERENCES society_agents(id),
 counterparty_id text NOT NULL REFERENCES society_agents(id),
 org_id bigint REFERENCES society_organizations(id), text text NOT NULL,
 due_tick bigint, status text NOT NULL DEFAULT 'pending_clearance' CHECK (status IN
 ('pending_clearance','open','claimed','confirmed','disputed','released','expired','withheld')),
 CHECK (author_id <> counterparty_id)
);
CREATE TABLE society_commitment_events (
 id bigserial PRIMARY KEY, commitment_id bigint NOT NULL REFERENCES society_commitments(id),
 kind text NOT NULL CHECK (kind IN ('created','claimed_fulfilled','confirmed','disputed','released','expired')),
 actor_id text REFERENCES society_agents(id), evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 tick bigint NOT NULL, created_at timestamptz NOT NULL, action_key text UNIQUE
);
CREATE INDEX society_commitment_events_actor ON society_commitment_events(actor_id,created_at);
CREATE TABLE society_cases (
 id bigserial PRIMARY KEY, forum_org_id bigint REFERENCES society_organizations(id),
 filed_by text NOT NULL REFERENCES society_agents(id), subject_ids text[] NOT NULL,
 claim_text text NOT NULL, status text NOT NULL DEFAULT 'pending_clearance'
   CHECK (status IN ('pending_clearance','open','closed','withdrawn','withheld')),
 outcome jsonb, opened_tick bigint, closed_tick bigint,
 CHECK (cardinality(subject_ids) BETWEEN 1 AND 3)
);
CREATE TABLE society_case_entries (
 id bigserial PRIMARY KEY, case_id bigint NOT NULL REFERENCES society_cases(id),
 actor_id text NOT NULL REFERENCES society_agents(id), kind text NOT NULL
 CHECK (kind IN ('statement','evidence','response','ruling')),
 text text NOT NULL, refs jsonb NOT NULL DEFAULT '[]'::jsonb,
 clearance text NOT NULL DEFAULT 'pending' CHECK (clearance IN ('pending','cleared','withheld')),
 accepted_by text REFERENCES society_agents(id), tick bigint NOT NULL,
 created_at timestamptz NOT NULL, action_key text UNIQUE
);
CREATE INDEX society_case_entries_actor ON society_case_entries(actor_id,created_at);
CREATE TABLE society_agent_lifecycle (
 agent_id text PRIMARY KEY REFERENCES society_agents(id), last_active_at timestamptz NOT NULL,
 state text NOT NULL CHECK (state IN ('active','dormant')), changed_at timestamptz NOT NULL
);
CREATE TABLE external_owner_claims (
 id bigserial PRIMARY KEY, agent_id text NOT NULL REFERENCES society_agents(id),
 platform text NOT NULL DEFAULT 'x' CHECK (platform='x'), handle_normalized text NOT NULL,
 handle_sha256 text NOT NULL, challenge_sha256 text NOT NULL UNIQUE,
 issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 status text NOT NULL CHECK (status IN ('pending','superseded','expired','verified','rejected','retired')),
 post_url text, post_id text, verified_by text, verified_at timestamptz, reference text
);
CREATE UNIQUE INDEX external_owner_claims_verified_owner ON external_owner_claims(handle_sha256)
 WHERE status='verified';
CREATE TABLE society_institution_texts (
 post_id bigint PRIMARY KEY REFERENCES society_posts(id),
 object_kind text NOT NULL CHECK (object_kind IN ('organization','proposal','commitment','case','case_entry')),
 object_id bigint NOT NULL, actor_id text NOT NULL REFERENCES society_agents(id),
 action text NOT NULL, action_key text NOT NULL UNIQUE, request_hash text NOT NULL,
 tick bigint NOT NULL, created_at timestamptz NOT NULL,
 clearance text NOT NULL DEFAULT 'pending' CHECK (clearance IN ('pending','cleared','withheld')),
 intent jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX society_institution_texts_actor ON society_institution_texts(actor_id,created_at);
-- Reuse the existing append-only audit trigger. Current projections remain mutable.
CREATE TRIGGER society_org_events_no_rewrite BEFORE UPDATE OR DELETE OR TRUNCATE
 ON society_org_events FOR EACH STATEMENT EXECUTE FUNCTION society_settings_immutable();
CREATE TRIGGER society_trust_no_rewrite BEFORE UPDATE OR DELETE OR TRUNCATE
 ON society_trust FOR EACH STATEMENT EXECUTE FUNCTION society_settings_immutable();
CREATE TRIGGER society_commitment_events_no_rewrite BEFORE UPDATE OR DELETE OR TRUNCATE
 ON society_commitment_events FOR EACH STATEMENT EXECUTE FUNCTION society_settings_immutable();

-- Body is <=2000. A charter's <=60-character name and newline are also screened
-- in the same input, so its complete moderation envelope is bounded at 2061.
-- Ordinary posts/replies remain <=280, enforced at commit by the link guard below.
ALTER TABLE society_posts DROP CONSTRAINT society_posts_text_check;
ALTER TABLE society_posts ADD CONSTRAINT society_posts_text_check
 CHECK (length(text) BETWEEN 1 AND 2061) NOT VALID;
ALTER TABLE society_posts VALIDATE CONSTRAINT society_posts_text_check;

CREATE FUNCTION society_check_institution_text(pid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE p society_posts%ROWTYPE; i society_institution_texts%ROWTYPE; authorized boolean := false;
BEGIN
 -- Serialize cross-table text/link edits as well as normal action transactions.
 SELECT * INTO p FROM society_posts WHERE id=pid FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT * INTO i FROM society_institution_texts WHERE post_id=pid;
 IF NOT FOUND THEN
  IF length(p.text)>280 THEN RAISE EXCEPTION 'ordinary_post_text_limit' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 IF i.actor_id<>p.author_id OR p.reply_to IS NOT NULL OR i.intent->>'public_text' IS DISTINCT FROM p.text THEN
  RAISE EXCEPTION 'invalid_institution_text_link' USING ERRCODE='23514';
 END IF;
 IF i.object_kind='organization' AND i.action='org_found' THEN
  SELECT EXISTS(SELECT 1 FROM society_organizations o WHERE o.id=i.object_id AND o.created_by=i.actor_id) INTO authorized;
 ELSIF i.object_kind='organization' AND i.action='org_join' THEN
  SELECT EXISTS(SELECT 1 FROM society_organizations o WHERE o.id=i.object_id) AND length(p.text)<=280 INTO authorized;
 ELSIF i.object_kind='proposal' AND i.action IN ('org_propose','org_join') THEN
  SELECT EXISTS(SELECT 1 FROM society_proposals o WHERE o.id=i.object_id AND o.author_id=i.actor_id) INTO authorized;
 ELSIF i.object_kind='commitment' AND i.action IN ('commit','commitment_claim','commitment_dispute') THEN
  SELECT EXISTS(SELECT 1 FROM society_commitments o WHERE o.id=i.object_id
   AND (o.author_id=i.actor_id OR (i.action='commitment_dispute' AND o.counterparty_id=i.actor_id))) INTO authorized;
 ELSIF i.object_kind='case' AND i.action='case_open' THEN
  SELECT EXISTS(SELECT 1 FROM society_cases o WHERE o.id=i.object_id AND o.filed_by=i.actor_id) INTO authorized;
 ELSIF i.object_kind='case_entry' AND i.action='case_entry' THEN
  SELECT EXISTS(SELECT 1 FROM society_case_entries o WHERE o.id=i.object_id AND o.actor_id=i.actor_id) INTO authorized;
 END IF;
 IF NOT authorized OR (i.action<>'org_found' AND length(p.text)>2000) THEN
  RAISE EXCEPTION 'invalid_institution_text_link' USING ERRCODE='23514';
 END IF;
END $$;
CREATE FUNCTION society_institution_text_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='society_posts' THEN
  PERFORM society_check_institution_text(NEW.id);
 ELSE
  IF TG_OP IN ('UPDATE','DELETE') THEN PERFORM society_check_institution_text(OLD.post_id); END IF;
  IF TG_OP IN ('INSERT','UPDATE') THEN PERFORM society_check_institution_text(NEW.post_id); END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER society_posts_institution_text_guard AFTER INSERT OR UPDATE
 ON society_posts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION society_institution_text_guard();
CREATE CONSTRAINT TRIGGER society_links_institution_text_guard AFTER INSERT OR UPDATE OR DELETE
 ON society_institution_texts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION society_institution_text_guard();

-- KW-028: extend the existing participation gate, retaining security states.
-- Rows and identities are unchanged. This metadata change takes AccessExclusiveLock.
ALTER TABLE society_agents DROP CONSTRAINT society_agents_participation_check;
ALTER TABLE society_agents ADD CONSTRAINT society_agents_participation_check
 CHECK (participation IN ('active','suspended','revoked','inactive','dormant','retired')) NOT VALID;
ALTER TABLE society_agents VALIDATE CONSTRAINT society_agents_participation_check;
CREATE TABLE society_participation_events (
 id bigserial PRIMARY KEY, agent_id text NOT NULL REFERENCES society_agents(id),
 from_state text NOT NULL, to_state text NOT NULL,
 source text NOT NULL CHECK(source IN ('admin','auto')),
 reference text NOT NULL CHECK(length(reference) BETWEEN 1 AND 200),
 approved_by text NOT NULL CHECK(length(approved_by) BETWEEN 1 AND 100),
 batch_id text, created_at timestamptz NOT NULL
);
CREATE INDEX society_participation_events_agent ON society_participation_events(agent_id,id);
CREATE TRIGGER society_participation_events_no_rewrite BEFORE UPDATE OR DELETE OR TRUNCATE
 ON society_participation_events FOR EACH STATEMENT EXECUTE FUNCTION society_settings_immutable();
