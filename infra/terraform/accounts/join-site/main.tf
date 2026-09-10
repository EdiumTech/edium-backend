locals {
  tags = {
    project = "edium-join"
    managed = "terraform"
  }
}

data "archive_file" "function" {
  type        = "zip"
  source_dir  = "${path.module}/functions/app"
  output_path = "${path.module}/function.zip"
}

resource "random_password" "admin_token" {
  length  = 48
  special = false
}

resource "random_password" "ip_hash_salt" {
  length  = 48
  special = false
}

resource "random_password" "contest_token_key" {
  length  = 64
  special = false
}

resource "yandex_iam_service_account" "runtime" {
  name        = "edium-join-runtime"
  description = "Least-privilege runtime for the Edium team application form"
  folder_id   = var.folder_id
}

resource "yandex_iam_service_account" "runner" {
  count       = var.runner_image_url == "" ? 0 : 1
  name        = "edium-join-runner"
  description = "Image pull only; candidate code has no access to this identity"
  folder_id   = var.folder_id
}

resource "yandex_iam_service_account_static_access_key" "storage" {
  service_account_id = yandex_iam_service_account.runtime.id
  description        = "Used only by the join functions for signed resume upload/download URLs"
}

resource "yandex_storage_bucket" "resumes" {
  bucket    = var.resume_bucket_name
  folder_id = var.folder_id
  max_size  = 1073741824
  versioning {
    enabled = false
  }
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }
  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = var.allowed_origins
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
  force_destroy = false
}

resource "yandex_storage_bucket_iam_binding" "runtime_editor" {
  bucket  = yandex_storage_bucket.resumes.bucket
  role    = "storage.editor"
  members = ["serviceAccount:${yandex_iam_service_account.runtime.id}"]
}

# Kept in the isolated join stack and protected from accidental deletion. Contest
# aggregates currently use conditional Object Storage writes; this reserved YDB
# can support future query-heavy workflows without sharing another subsystem.
resource "yandex_ydb_database_serverless" "applications" {
  name                = "edium-join-applications"
  folder_id           = var.folder_id
  deletion_protection = true
  serverless_database {
    enable_throttling_rcu_limit = true
    throttling_rcu_limit        = 10
    provisioned_rcu_limit       = 0
    storage_size_limit          = 1
  }
  labels = local.tags
}

resource "yandex_function" "api" {
  name               = "edium-join-api"
  description        = "Public form API and server-authorized candidate administration"
  folder_id          = var.folder_id
  runtime            = "python312"
  entrypoint         = "handler.api"
  memory             = 512
  execution_timeout  = 125
  service_account_id = yandex_iam_service_account.runtime.id
  user_hash          = data.archive_file.function.output_base64sha256
  environment = {
    RESUME_BUCKET     = yandex_storage_bucket.resumes.bucket
    ALLOWED_ORIGINS   = join(",", var.allowed_origins)
    ADMIN_URL         = var.admin_url
    S3_ACCESS_KEY     = yandex_iam_service_account_static_access_key.storage.access_key
    S3_SECRET_KEY     = yandex_iam_service_account_static_access_key.storage.secret_key
    ADMIN_TOKEN       = random_password.admin_token.result
    IP_HASH_SALT      = random_password.ip_hash_salt.result
    CONTEST_TOKEN_KEY = random_password.contest_token_key.result
    CONTEST_URL       = var.contest_url
    RUNNER_URL        = try(yandex_serverless_container.runner[0].url, "")
    RUNNER_LANGUAGES  = var.runner_image_url == "" ? "javascript" : "javascript,kotlin,swift,python,go"
  }
  content { zip_filename = data.archive_file.function.output_path }
  log_options { min_level = "ERROR" }
  labels     = local.tags
  depends_on = [yandex_storage_bucket_iam_binding.runtime_editor]
}

resource "yandex_function" "maintenance" {
  name               = "edium-join-maintenance"
  description        = "Retries notifications and removes orphaned/deleting resumes"
  folder_id          = var.folder_id
  runtime            = "python312"
  entrypoint         = "handler.maintenance"
  memory             = 512
  execution_timeout  = 60
  service_account_id = yandex_iam_service_account.runtime.id
  user_hash          = data.archive_file.function.output_base64sha256
  environment = {
    RESUME_BUCKET     = yandex_storage_bucket.resumes.bucket
    ALLOWED_ORIGINS   = join(",", var.allowed_origins)
    EMAIL_MODE        = var.email_mode
    TEAM_EMAIL        = var.team_email
    HERALD_EMAIL_URL  = var.herald_email_url
    ADMIN_URL         = var.admin_url
    S3_ACCESS_KEY     = yandex_iam_service_account_static_access_key.storage.access_key
    S3_SECRET_KEY     = yandex_iam_service_account_static_access_key.storage.secret_key
    ADMIN_TOKEN       = random_password.admin_token.result
    IP_HASH_SALT      = random_password.ip_hash_salt.result
    HERALD_API_KEY    = var.herald_api_key
    CONTEST_TOKEN_KEY = random_password.contest_token_key.result
    CONTEST_URL       = var.contest_url
    RUNNER_URL        = ""
  }
  content { zip_filename = data.archive_file.function.output_path }
  log_options { min_level = "ERROR" }
  labels     = local.tags
  depends_on = [yandex_storage_bucket_iam_binding.runtime_editor]
  lifecycle {
    precondition {
      condition     = var.email_mode == "disabled" || (var.team_email != "" && var.herald_api_key != "")
      error_message = "email_mode=herald requires team_email and herald_api_key."
    }
  }
}

resource "yandex_serverless_container" "runner" {
  count              = var.runner_image_url == "" ? 0 : 1
  name               = "edium-join-runner"
  description        = "Private QuickJS/Wasm sandbox for the candidate contest"
  folder_id          = var.folder_id
  memory             = 6144
  cores              = 3
  core_fraction      = 100
  execution_timeout  = "120s"
  concurrency        = 1
  service_account_id = yandex_iam_service_account.runner[0].id
  image {
    url = var.runner_image_url
  }
  log_options { min_level = "ERROR" }
  labels = local.tags
}

resource "yandex_serverless_container_iam_binding" "runner_invoker" {
  count        = var.runner_image_url == "" ? 0 : 1
  container_id = yandex_serverless_container.runner[0].id
  role         = "serverless-containers.containerInvoker"
  members      = ["serviceAccount:${yandex_iam_service_account.runtime.id}"]
}

resource "yandex_api_gateway" "join" {
  name        = "edium-join-api"
  description = "API Gateway for the Edium team application form"
  folder_id   = var.folder_id
  labels      = local.tags
  spec = templatefile("${path.module}/openapi.yaml.tftpl", {
    function_id        = yandex_function.api.id
    service_account_id = var.invoker_service_account_id
    allowed_origins    = var.allowed_origins
  })
}

resource "yandex_function_trigger" "maintenance" {
  name        = "edium-join-maintenance"
  description = "Every five minutes: outbox delivery and orphan cleanup"
  folder_id   = var.folder_id
  timer { cron_expression = "*/5 * ? * * *" }
  function {
    id                 = yandex_function.maintenance.id
    service_account_id = var.invoker_service_account_id
    retry_attempts     = 1
    retry_interval     = 30
  }
  labels = local.tags
}
