-- Additive bounded maintenance metadata. New table is intentionally empty.
-- Existing agent/lifecycle/history row shapes and contents are unchanged.
CREATE TABLE society_maintenance_checks (
 agent_id text PRIMARY KEY REFERENCES society_agents(id),
 checked_at timestamptz NOT NULL
);
CREATE INDEX society_maintenance_checked ON society_maintenance_checks(checked_at,agent_id);
-- Ordinary index creation blocks writers: apply during quiescent maintenance.
CREATE INDEX external_owner_claims_agent ON external_owner_claims(agent_id,id DESC);
CREATE INDEX society_commitments_due ON society_commitments(due_tick,id) WHERE status IN ('open','claimed');
