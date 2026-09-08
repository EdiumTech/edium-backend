# Production publication plan — do not execute without review

## Current state observed on 2026-09-08

- `edium.online` and `www.edium.online` resolve to `130.193.37.78`.
- The production VM, managed PostgreSQL, and managed Redis were restarted; HTTPS at the apex responds successfully.
- The repository's production Caddy configuration serves `/srv/landing` from the production VM.
- `accounts/platform` declares both VMs, VPC, PostgreSQL, Redis and other unrelated services. Applying it is not an acceptable way to publish this form.
- The platform DNS Terraform module does not declare the apex or `www` record, so those records appear to be managed outside that state. Confirm this in the Cloud DNS console before cutover.

## Publication through the existing production web server

Keep the form API and resume storage in the independent serverless `join-site`
state. Publish only the static Vite build to the production VM's existing
`/opt/edium/landing` directory served by Caddy. Do not apply `accounts/platform`
and do not create a second hosting stack for the main domain.

## Safe sequence

1. Deploy the join API stack in `email_mode = "disabled"` and inspect its isolated Terraform plan.
2. Build the landing with `VITE_JOIN_API_BASE` set to the API Gateway output.
3. Archive the current `/opt/edium/landing` directory for rollback, then upload the new `dist/` contents through the existing site release path.
4. Verify `/`, `/join/`, `/join/admin/`, `/privacy/`, `/terms/`, asset MIME types, 404 behavior, TLS, and HTTP-to-HTTPS redirect.
5. Configure Herald SMTP plus the shared `EMAIL_API_KEY`, send only to controlled test inboxes, then enable `email_mode = "herald"` in the join stack.

## Rollback

Restore the archived `/opt/edium/landing` directory and reload Caddy if needed.
The serverless API is independent, so a static rollback does not delete stored
applications or resumes.

## Approval boundary

Creating the API test stack is isolated and reversible. Replacing the production
landing directory must be explicitly approved after the preview and Terraform
plan are shown. No DNS change is needed.
