-- 0006_wallet_idempotency.sql — money-movement idempotency (ADR-0013)
--
-- Every premium collection and claim payout carries a caller-supplied ref;
-- this table records ref → posted ledger tx so a retried request is
-- answered from the record, never re-posted.

BEGIN;

CREATE TABLE IF NOT EXISTS wallet_idem (
    ref        TEXT PRIMARY KEY,           -- caller idempotency key (claim id, PSP receipt)
    tx         TEXT NOT NULL,              -- the ledger txid it posted as
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
