# Edium team applications — isolated serverless stack

This directory is intentionally independent from `accounts/platform`. It has its own Terraform state key (`join-site.tfstate`). It uses no Compute Cloud VM, shared application database, Managed PostgreSQL, or Redis. The pre-existing isolated YDB resource remains deletion-protected; current low-volume application and contest aggregates live in the private Object Storage bucket.

## Architecture

1. `POST /v1/uploads` validates file metadata, honeypot and rate limit, then creates a one-hour metadata record in the private Object Storage bucket and returns a 10-minute presigned POST.
2. The browser uploads the file directly to the private Object Storage bucket. This bypasses the 2.5 MB API Gateway request limit while keeping the 10 MB product limit.
3. `POST /v1/applications` downloads the private object inside the function, verifies its exact size and actual PDF/DOCX structure, and attaches it to the application metadata record in the same private bucket.
4. The saved row contains an outbox state. A timer invokes the maintenance function every five minutes and queues email in the existing Herald service. Herald/API/SMTP failures are retried with exponential backoff and never roll back the application.
5. `/join/admin/` sends a bearer token only in request headers. The API checks it server-side before listing data or issuing a 60-second resume download URL. The browser does not persist the token.
6. The same maintenance function deletes one-hour orphan uploads and completes interrupted application deletions.
7. HR explicitly selects a task set and creates one deterministic contest aggregate per application. Candidate links contain a signed capability only in the URL fragment; only its SHA-256 hash is stored. Start, autosave, submit, revoke and the single extension use server timestamps and conditional Object Storage writes.
8. Candidate JavaScript runs in QuickJS compiled to WebAssembly inside a private Serverless Container. Each test gets a fresh runtime with memory, stack and CPU interruption limits and no host/network bindings. Only sanitized test results return through the function control plane.

The initial admin token is a narrow, server-checked fallback because the existing Doorman authentication is hosted on the currently stopped VM stack and has no staff-role flow. Replace this token with an organization/federation JWT authorizer once the owner identifies the staff identities.

## Contest lifecycle

| State | Meaning | Candidate writes |
| --- | --- | --- |
| `invited` | Link issued; timer has not started | Start only |
| `opened` | Rules viewed; timer has not started | Start only |
| `started` | Server deadline fixed | Revision-protected answers and submit |
| `submitted` | Candidate submitted early | Frozen |
| `expired` | Start window or server deadline passed | Frozen |
| `revoked` | HR revoked the capability | Frozen |

The browser never supplies a trusted state, `startedAt`, deadline, or completion time. A five-minute maintenance trigger finalizes inactive expired contests; an active browser also observes and persists expiration on its next request.

## Candidate tasks and execution

New applications require one of five areas of interest: Бэкенд, Фронтенд, Мобильная разработка, AI/ML, or Системная разработка. This is candidate preference only. When issuing an invitation, HR explicitly chooses one of those task sets; the candidate receives only its personalized link and cannot switch the assignment. Each direction has three practical, lightly humorous tasks. Backend covers payment webhook receipts, safe HTTP retries and atomic quota reservations. AI/ML covers dataset leakage/conflicts, citation-traceability evaluation and RAG context selection using supplied safety metadata. System tasks concern device buttons, smart-home command queues and stale screen state. Both the selected direction and immutable `task_set_version` are stored at invitation time. Changing the application later does not swap the candidate's tasks. Existing invitations retain their pinned languages, rules and tests; old applications with retired directions still resolve to their corresponding legacy sets.

Answers contain source code and its allowed language only. Explanations, solution links and solution attachments are not required; submission checks for code in every assigned task. Backend invitations use Go (`package main`, `func Solve(input map[string]any) map[string]any`); AI/ML uses Python (`def solve(data: dict) -> dict`). Both provide only their standard library. Frontend and system tasks use JavaScript (`solve(input)`, not TypeScript). Mobile requires **both** Kotlin and Swift: the outbox task accepts only Kotlin, the permission task only Swift, and the download task accepts either. The server validates language on save, test and submit. Each public task contains language-specific signatures and starter code. Changing the optional task language preserves both drafts in the current tab; only the selected answer is saved on the server.

`functions/app/contest_catalog.json` is the shared source for statements, examples and complete test suites. Candidate task metadata includes `testCount`. A run executes every test in the assigned suite, including cases not shown as statement examples. Each test gets its own fresh runtime; one exception or timeout does not skip subsequent tests. The response is `{ passed: number, total: number, allPassed: boolean, tests, durationMs }`. The control plane verifies the number of returned cases and derives the counts from their boolean results rather than trusting a summary. The client cannot select another direction, version or task id outside its assignment.

### Additional languages: local preview, not yet a Linux deployment

The macOS development adapters use real language runtimes, not translation heuristics or source matching. Kotlin 2.2.21 compiles to standalone JavaScript, which executes only in QuickJS/Wasm. Swift 6.3.1 and Go 1.26.1 compile to WASI and run in Wasmtime. Compiler subprocesses use deny-default macOS Seatbelt profiles, fixed options, an empty credential environment and isolated temporary job directories. Python runs in SHA256-pinned CPython 3.12.0/WASI via a trusted Wasmtime host, with a read-only standard-library preopen, no inherited environment or network, and fresh memory/fuel/time-limited instances. Candidate code never runs natively on the host. This old vendor Python build is a local prototype, not an approved production interpreter. Missing toolchains or unavailable isolation return `runtime_unavailable`. Compile errors report 0/N with the compiler diagnostic; successful compilation and Python runs execute the complete suite.

**Deployment gate:** the current `runner/Dockerfile` remains the existing JS-only image. It includes the language dispatcher and explicitly returns 503 for unavailable languages, never runs a compiler without isolation. New backend, AI/ML and mobile invitations are blocked unless `RUNNER_LANGUAGES` explicitly advertises `go`, `python`, or both `kotlin,swift`, respectively. Do not advertise these capabilities in production with this image. A production release still needs a separately verified Linux sandbox, maintained pinned toolchains/interpreter, hard host memory/CPU limits, time limits measured on the actual serverless runtime, and a reviewed isolated Terraform plan. The current 512 MB / 10-second container and 30-second function settings are for JavaScript only. No cloud resource changes or production apply are included in this preview.

See [local preview instructions](dev/README.md), [Kotlin](dev/KOTLIN-TOOLCHAIN.md), [Swift](dev/SWIFT-TOOLCHAIN.md), [Go](dev/GO-TOOLCHAIN.md), and [Python](dev/PYTHON-TOOLCHAIN.md) setup. Reference solutions and runtime proof programs are test/development-only and excluded from the production image.

## Secrets and configuration

- `ADMIN_TOKEN`, `IP_HASH_SALT` and `CONTEST_TOKEN_KEY` are generated sensitive Terraform values. Do not copy them to frontend variables, URLs, logs or test fixtures.
- `EMAIL_API_KEY` remains a GitHub Environment secret and is injected only as `herald_api_key` into the maintenance function.
- `JOIN_RUNNER_IMAGE_URL` is configuration, not a secret, but must contain an immutable `@sha256` digest.
- `VITE_JOIN_API_BASE` is the public API Gateway base URL. Candidate capabilities remain in URL fragments and are sent to the API only in the `Authorization` header.

## Security properties

- The resume bucket is private; public read/list/config access is explicitly disabled.
- Upload and download URLs expire after 10 minutes and 60 seconds respectively.
- Object keys are opaque and do not contain the original filename. The original filename exists only in private JSON metadata.
- Both declared metadata and actual file bytes are checked. A DOCX must be a valid ZIP with Word document members; a PDF needs PDF header and EOF markers.
- Rate-limit keys are HMAC hashes of IP + action + window. Raw IP addresses and application contents are not written to technical logs.
- Deterministic upload/application keys make a retried completed request return success without creating a second application. Herald provides an independent idempotency boundary for email.
- Runtime credentials and the admin token are injected from sensitive Terraform values into encrypted function configuration. The Terraform state contains generated/static secret material and must remain in the private state bucket with restricted IAM.
- The bucket has a 1 GB size cap to bound accidental spend.
- Concurrent starts/submits are idempotent. Autosave uses a revision and returns `409` rather than overwriting a newer tab.
- Task statements are versioned separately from UI code. HR notes and 1–5 scores are review aids only; no automatic hiring decision is produced.
- Final and revoked contest records are automatically removed after 180 days. Deleting an application removes its contest immediately as part of the same deletion workflow.

## Prerequisites

- Terraform 1.5+.
- A Yandex Cloud service-account key authorized to create these isolated resources.
- Access to the existing private Terraform state bucket.
- The existing private Yandex Container Registry and its id in the `YC_REGISTRY_ID` GitHub Environment secret.
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
cd runner && npm ci --ignore-scripts && npm test
```

`terraform plan -out join.tfplan` is read-only with respect to managed resources. Review it and confirm that it contains no resource deletions and only touches the isolated join stack. Never run `terraform apply` in `accounts/platform` for this feature.

## Runner image

Run the `Build Join Contest Runner` workflow. It tests the sandbox, pushes an immutable image and prints a digest-pinned URL. Put that exact URL in the production Environment variable `JOIN_RUNNER_IMAGE_URL`; the deployment workflow passes it to Terraform. An empty value deliberately leaves code execution disabled while the rest of the API can be reviewed.

The Docker build context is this `join-site` directory, with `--file runner/Dockerfile`, so the image and API bundle consume the same catalog. Release in this order: matching runner image, API with the new assignments, then frontend. Keep non-JavaScript invitations gated until the runtime requirements above are verified; publishing the JS-only image does not enable Go, Python, Kotlin or Swift. The updated frontend can derive counts from legacy per-test reports during a rolling release.

## Test deployment

Keep `email_mode = "disabled"`; notifications remain pending and no message leaves the system.

```bash
terraform plan -out join.tfplan
terraform show join.tfplan
# Applying requires a separate explicit production approval.
```

Build the landing against the returned gateway URL:

```bash
cd ../../../../../edium-mobile/landing
VITE_JOIN_API_BASE="$(terraform -chdir=../../../edium-backend/infra/terraform/accounts/join-site output -raw api_base_url)" npm run build
npm run preview -- --host 127.0.0.1
```

Open `http://127.0.0.1:4173/join/` and test with synthetic data. The closed list is at `/join/admin/`; candidate links open `/join/contest/#invite=…`.

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
- invalid, expired and revoked contest links; refresh before/after start;
- double/concurrent start and submit; stale two-tab autosave conflict;
- deadline while editing or running tests; one allowed extension;
- empty, long-running, memory-heavy and malicious JavaScript;
- required Go/Python and mandatory Kotlin/Swift task language enforcement, syntax errors, full suite results, sandbox denial checks and missing-toolchain failures;
- direction-specific invitations, pinned legacy assignments, source-only submission, complete and partial test counts;
- runner unavailable and Herald unavailable without losing saved answers;
- HR answer review, notes, 1–5 scores and conclusion; no auto hiring score;
- 390 px mobile layout, keyboard order, visible focus and screen-reader errors.

## Data deletion and stack removal

Delete one candidate from `/join/admin/`; the API marks the metadata record, removes the resume and then purges the metadata. The maintenance run completes an interrupted deletion.

Do not use `terraform destroy` while applications remain. Export or delete applications first, verify that the bucket is empty, review a destroy plan, and only then destroy this isolated state. `force_destroy = false` prevents accidental resume loss.

## Cost assumptions

Assume 100 applications/month, an average 2 MB resume, 500 administrative reads, three 90-minute contests, 20 sandbox runs per contest and a five-minute maintenance timer (~8,640 calls/month):

- API Gateway and function invocation/compute are expected to remain within their monthly free tiers.
- Object Storage stays below its first free 1 GB with these assumptions; request charges should be negligible at this volume.
- The runner uses one 512 MB / one-core instance only during requests; exact Serverless Container and registry charges should be checked in the reviewed plan and billing calculator before apply.
- SMTP/provider charges and outgoing resume downloads depend on the selected provider and actual usage.

At 60 runner calls/month and a deliberately conservative 2.5 seconds per call, usage is about 0.042 vCPU-hours and 0.021 GB-hours. Together with roughly 0.2 GB of resumes, fewer than 100,000 gateway calls and fewer than 1,000,000 function/container calls, the directly metered join workload is expected to be about 0 ₽/month inside the published free allowances. Registry image storage, Herald/SMTP and traffic beyond free allowances remain separate.

Recalculate before production if expected application volume, retention, or average resume size changes.
