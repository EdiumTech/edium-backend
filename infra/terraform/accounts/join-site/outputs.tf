output "api_base_url" {
  description = "Pass this value as VITE_JOIN_API_BASE when building the landing."
  value       = "https://${yandex_api_gateway.join.domain}"
}

output "admin_token" {
  description = "Share only with authorized staff; rotate through Terraform if disclosed."
  value       = random_password.admin_token.result
  sensitive   = true
}

output "resume_bucket" {
  value = yandex_storage_bucket.resumes.bucket
}

output "ydb_database" {
  value = yandex_ydb_database_serverless.applications.database_path
}
