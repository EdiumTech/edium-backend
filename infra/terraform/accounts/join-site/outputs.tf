output "api_base_url" {
  description = "Pass this value as VITE_JOIN_API_BASE when building the landing."
  value       = "https://${yandex_api_gateway.join.domain}"
}

output "runner_url" {
  description = "Private runner URL; empty when runner_image_url is not configured."
  value       = try(yandex_serverless_container.runner[0].url, "")
}

output "admin_token" {
  description = "Share only with authorized staff; rotate through Terraform if disclosed."
  value       = random_password.admin_token.result
  sensitive   = true
}

output "resume_bucket" {
  value = yandex_storage_bucket.resumes.bucket
}
