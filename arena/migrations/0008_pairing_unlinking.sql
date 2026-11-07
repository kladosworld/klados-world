-- Retained closed-generation pairings may no longer identify their voter.
ALTER TABLE pairings ALTER COLUMN voter_id DROP NOT NULL;

CREATE FUNCTION guard_unlinked_pairing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.voter_id IS NULL AND NOT EXISTS (
        SELECT 1 FROM answers a JOIN answers b ON b.id = NEW.right_answer_id
        JOIN generation_closures ga ON ga.generation = a.generation
        JOIN generation_closures gb ON gb.generation = b.generation
        WHERE a.id = NEW.left_answer_id
    ) THEN
        RAISE EXCEPTION 'only closed-generation pairings may be unlinked' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER pairing_unlink_guard BEFORE INSERT OR UPDATE ON pairings
    FOR EACH ROW EXECUTE FUNCTION guard_unlinked_pairing();
