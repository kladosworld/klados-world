-- Keep the original voter-inclusive FK for linked votes. A second FK always
-- binds the pairing/challenge/side order, including when voter_id becomes NULL.
ALTER TABLE votes ALTER COLUMN voter_id DROP NOT NULL;
ALTER TABLE pairings ADD CONSTRAINT pairings_retained_vote_identity
    UNIQUE (id, challenge_id, left_answer_id, right_answer_id);
ALTER TABLE votes ADD CONSTRAINT votes_retained_pairing_identity
    FOREIGN KEY (pairing_id, challenge_id, left_answer_id, right_answer_id)
    REFERENCES pairings(id, challenge_id, left_answer_id, right_answer_id);
CREATE INDEX pairings_voter_time_idx ON pairings(voter_id, created_at);
