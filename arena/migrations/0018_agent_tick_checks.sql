-- Persist polling cooldown across workers, restarts and credential rotations.
-- Existing agent credentials, counters and society records are unchanged.
ALTER TABLE agent_tokens ADD COLUMN last_tick_check_at timestamptz;
