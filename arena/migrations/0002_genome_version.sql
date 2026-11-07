-- Answer contract: genome_version is genome.Genome.version, a SHA-256 content hash.
-- Existing invalid rows must be corrected by the operator before this migration;
-- do not invent hashes or rewrite provenance automatically.
ALTER TABLE answers ADD CONSTRAINT answers_genome_version_sha256
    CHECK (genome_version ~ '^[0-9a-f]{64}$');
