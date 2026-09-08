# Edium team applications — isolated serverless stack

This directory is intentionally independent from `accounts/platform`. It has its own Terraform state key (`join-site.tfstate`) and declares **no Compute Cloud resources, VPCs, Managed PostgreSQL, or Redis**.

## Architecture

1. `POST /v1/uploads` validates file metadata, honeypot and rate limit, then creates a one-hour upload record in Serverless YDB and returns a 10-minute presigned POST.
2. The browser uploads the file directly to the private Object Storage bucket. This bypasses the 2.5 MB API Gateway request limit while keeping the 10 MB product limit.
3. `POST /v1/applications` downloads the private object inside the function, verifies its exact size and actual PDF/DOCX structure, and atomically attaches it to the YDB application record.
4. The saved row contains an outbox state. A timer invokes the maintenance function every five minutes and queues email in the existing Herald service. Herald/API/SMTP failures are retried with exponential backoff and never roll back the application.
5. `/join/admin/` sends a bearer token only in request headers. The API checks it server-side before listing data or issuing a 60-second resume download URL. The browser does not persist the token.
6. The same maintenance function deletes one-hour orphan uploads and completes interrupted application deletions.

The initial admin token is a narrow, server-checked fallback because the existing Doorman authentication is hosted on the currently stopped VM stack and has no staff-role flow. Replace this token with an organization/federation JWT authorizer once the owner identifies the staff identities.

## Security properties

- The resume bucket is private; public read/list/config access is explicitly disabled.
- Upload and download URLs expire after 10 minutes and 60 seconds respectively.
- Object keys are random and do not contain the original filename. The original filename exists only as YDB metadata.
- Both declared metadata and actual file bytes are checked. A DOCX must be a valid ZIP with Word document members; a PDF needs PDF header and EOF markers.
- Rate-limit keys are HMAC hashes of IP + action + window. Raw IP addresses and application contents are not written to technical logs.
- YDB transactions make the upload ID the application ID, so retrying a completed request returns success without creating a second application.
- All runtime credentials and the admin token are injected from Lockbox. The Terraform state also contains generated/static secret material and must remain in the private state bucket with restricted IAM.
- The YDB throughput cap is 10 RU/s and its maximum storage is 1 GB to bound accidental spend.

## Prerequisites

- Terraform 1.5+.
- A Yandex Cloud service-account key authorized to create these isolated resources.
- Access to the existing private Terraform state bucket.
- The site build from `edium-mobile/landing`.

Do not copy values from production service logs into tests. Use only synthetic names, contacts, and resumes.

## Validate without changing cloud resources

```bash
cd infra/terraform/accounts/join-site
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check -recursive
terraform validate
python3 -m unittest discover -s tests -v
```

`terraform plan -out join.tfplan` is read-only with respect to managed resources. Review it and confirm that the plan contains only resources whose names start with `edium-join` plus the private resume bucket. Never run `terraform apply` in `accounts/platform` for this feature.

## Test deployment

Keep `email_mode = "disabled"`; notifications remain pending and no message leaves the system.

```bash
terraform plan -out join.tfplan
terraform show join.tfplan
terraform apply join.tfplan
terraform output -raw admin_token
```

Build the landing against the returned gateway URL:

```bash
cd ../../../../../edium-mobile/landing
VITE_JOIN_API_BASE="$(terraform -chdir=../../../edium-backend/infra/terraform/accounts/join-site output -raw api_base_url)" npm run build
npm run preview -- --host 127.0.0.1
```

Open `http://127.0.0.1:4173/join/` and test with synthetic data. The closed list is at `http://127.0.0.1:4173/join/admin/`.

## Email activation

Herald owns SMTP delivery. Configure and deploy Herald first with:

- `EMAIL_API_KEY` — a random service-to-service token;
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`;
- verified `SMTP_FROM`;
- `SMTP_TLS_MODE` (`starttls` for port 587 or `implicit` for port 465).

Then place the same `EMAIL_API_KEY` value into the join stack as the sensitive
`herald_api_key`, set `team_email`, switch `email_mode` to `herald`, review a new
plan and apply only this directory. Existing pending outbox rows will then be
queued in Herald by the timer. Resumes are never sent to Herald or attached to
email.

## Publishing plan (not executed)

1. Review the isolated Terraform plan and deploy it in test mode.
2. Run the end-to-end checklist below against the API Gateway domain and local Vite preview.
3. Approve the candidate privacy wording and identify authorized staff.
4. Configure Herald SMTP and the shared API key, send only to controlled test inboxes, then set the join stack to `email_mode = "herald"`.
5. Publish the static `dist/` contents through the existing site release process. Do not start a VM merely to run the form backend.
6. Verify `https://edium.online/join/`, then keep the prior static build ready for rollback.

## End-to-end checklist

- valid PDF and valid DOCX; file replacement/removal and drag/drop;
- too large, renamed executable, corrupt PDF/ZIP, empty file;
- invalid Telegram/phone/email/URL and short/long motivation;
- double click, retry after network interruption, retry after storage succeeded;
- application remains saved when Herald or SMTP is unavailable and is delivered after recovery;
- unauthenticated list/detail/download/update/delete all return 401;
- status filtering and every status transition;
- deleting a row removes its object; an interrupted deletion is completed by maintenance;
- an unattached upload disappears after one hour;
- 390 px mobile layout, keyboard order, visible focus and screen-reader errors.

## Data deletion and stack removal

Delete one candidate from `/join/admin/`; the API marks the row, removes the object and then purges the database record. The maintenance run completes an interrupted deletion.

Do not use `terraform destroy` while applications remain. Export or delete applications first, verify that the bucket is empty, then set YDB `deletion_protection = false`, apply that change, review a destroy plan, and only then destroy this isolated state. `force_destroy = false` prevents accidental resume loss.

## Cost assumptions

Assume 100 applications/month, an average 2 MB resume, 500 administrative reads, two public API calls per application and a five-minute maintenance timer (~8,640 calls/month):

- API Gateway and function invocation/compute are expected to remain within their monthly free tiers.
- YDB operations and under 1 GB of data are expected to remain within the small serverless free allowances.
- Object Storage stays below its first free 1 GB with these assumptions.
- One Lockbox secret version is the predictable fixed cost: about 19.73 RUB/month at the documented Russia-region example rate (`720 × 0.0274 RUB`), plus negligible reads.
- SMTP/provider charges and outgoing resume downloads depend on the selected provider and actual usage.

Recalculate before production if expected application volume, retention, or average resume size changes.
