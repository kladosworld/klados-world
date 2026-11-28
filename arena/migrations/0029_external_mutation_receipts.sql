-- Additive durable HTTP mutation receipts. No existing rows, ledger or ticks change.
-- Requires a short schema maintenance window; regular transaction-owned DDL.
CREATE TABLE society_mutation_receipts (
 scope text NOT NULL CHECK (length(scope) BETWEEN 1 AND 160),
 request_id text NOT NULL CHECK (request_id ~ '^[A-Za-z0-9_.-]{1,80}$'),
 endpoint text NOT NULL CHECK (length(endpoint) BETWEEN 1 AND 100),
 body_sha256 text NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
 outcome jsonb NOT NULL,
 credential_once boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL,
 PRIMARY KEY(scope,request_id)
);
