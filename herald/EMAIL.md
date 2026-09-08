# Herald email outbox

`POST /herald/v1/emails` accepts email from trusted backend clients, stores it in
the existing PostgreSQL task outbox, and returns `202` only after the task has
been persisted. Delivery runs asynchronously, so an SMTP outage does not lose
the caller's primary operation.

## Configuration

```dotenv
EMAIL_API_KEY=<random service-to-service bearer token>
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=<provider username>
SMTP_PASSWORD=<provider password>
SMTP_FROM=Edium <noreply@example.com>
SMTP_TLS_MODE=starttls
```

`SMTP_TLS_MODE` is one of `starttls` (normally port 587), `implicit` (normally
port 465), or `none` for local development only. If `EMAIL_API_KEY` is absent,
the HTTP endpoint is not registered. If the API key exists but SMTP is not fully
configured, the endpoint returns `503` and does not accept mail.

## Request

```http
POST /herald/v1/emails
Authorization: Bearer <EMAIL_API_KEY>
Idempotency-Key: candidate:550e8400-e29b-41d4-a716-446655440000:team
Content-Type: application/json

{
  "to": "team@example.com",
  "subject": "Новая заявка в команду Edium",
  "text_body": "Открыть закрытую карточку: https://edium.online/join/admin/",
  "html_body": "<p>Открыть закрытую карточку кандидата.</p>"
}
```

The endpoint accepts one recipient per task. `From` is never accepted from the
request and always comes from `SMTP_FROM`. Recipient, subject, and body are not
written to application logs. The caller should keep a stable idempotency key
until it receives `202`; both `queued` and `duplicate` mean the email was
accepted previously or now.

Failed SMTP deliveries use the task retry mechanism. After the configured
maximum attempts the task remains `failed` for investigation; it is not silently
deleted.

Telegram initialization is independent from email. If Telegram is unreachable,
Herald starts its HTTP API and all other configured workers; the bot channel is
temporarily disabled until the next Herald restart.
