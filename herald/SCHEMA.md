# Herald — схема данных

## База данных

```mermaid
erDiagram
    pending_otp {
        text  phone      PK
        text  channel    PK
        int   chat_id
        ts    created_at
        ts    expires_at
    }

    sms_task {
        uuid  id               PK
        text  phone
        text  text
        text  status
        uuid  idempotency_key  "UNIQUE, nullable"
        text  trace_ctx
        int   retry_count
        int   max_retries
        ts    created_at
        ts    processed_at
    }

    task {
        uuid  id               PK
        text  task_type
        jsonb payload
        text  status
        int   attempts
        int   max_attempts
        text  idempotency_key  "UNIQUE with task_type, nullable"
        ts    available_at
        text  last_error
        text  trace_ctx
        ts    created_at
        ts    updated_at
    }
```

`pending_otp.channel` — строковое поле: `tg`, `sms`.

`sms_task.status` — строковое поле: `pending`, `sent`.

Email uses `task.task_type = email_delivery`. Its payload contains one recipient,
subject, plain-text body and optional HTML body. The `(task_type,
idempotency_key)` partial unique index prevents duplicate enqueue calls. Recipient
and message content are not copied into technical logs.
