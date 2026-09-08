-- Email is delivered through the generic task outbox. A caller-provided key
-- prevents retries from creating duplicate messages.
ALTER TABLE task ADD COLUMN idempotency_key TEXT;

CREATE UNIQUE INDEX idx_task_type_idempotency_key
    ON task (task_type, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
