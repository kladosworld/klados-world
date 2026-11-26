-- Additive, empty W0 storage. No seed, UPDATE, DELETE, or existing-table rewrite.
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
