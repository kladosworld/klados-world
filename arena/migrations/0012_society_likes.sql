-- MVP v1: likes replace clips and arena prizes as the way to earn energy.
ALTER TABLE energy_entries DROP CONSTRAINT energy_entries_kind_check;
ALTER TABLE energy_entries ADD CONSTRAINT energy_entries_kind_check
    CHECK (kind IN ('birth', 'living', 'action', 'like', 'penalty', 'refund'));
