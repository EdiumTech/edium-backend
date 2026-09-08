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

resource "yandex_iam_service_account" "runtime" {
  name        = "edium-join-runtime"
  description = "Least-privilege runtime for the Edium team application form"
  folder_id   = var.folder_id
}

resource "yandex_iam_service_account" "gateway" {
  name        = "edium-join-gateway"
  description = "Invokes only the Edium join API function"
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

resource "yandex_ydb_database_iam_binding" "runtime_editor" {
  database_id = yandex_ydb_database_serverless.applications.id
  role        = "ydb.editor"
  members     = ["serviceAccount:${yandex_iam_service_account.runtime.id}"]
}

resource "yandex_lockbox_secret" "runtime" {
  name        = "edium-join-runtime"
  description = "Storage signing credentials, admin token, rate-limit salt, and Herald API key"
  folder_id   = var.folder_id
  labels      = local.tags
}

resource "yandex_lockbox_secret_version" "runtime" {
  secret_id = yandex_lockbox_secret.runtime.id
  entries {
    key        = "s3-access-key"
    text_value = yandex_iam_service_account_static_access_key.storage.access_key
  }
  entries {
    key        = "s3-secret-key"
    text_value = yandex_iam_service_account_static_access_key.storage.secret_key
  }
  entries {
    key        = "admin-token"
    text_value = random_password.admin_token.result
  }
  entries {
    key        = "ip-hash-salt"
    text_value = random_password.ip_hash_salt.result
  }
  dynamic "entries" {
    for_each = var.email_mode == "herald" ? { herald = true } : {}
    content {
      key        = "herald-api-key"
      text_value = var.herald_api_key
    }
  }
}

resource "yandex_lockbox_secret_iam_member" "runtime" {
  secret_id = yandex_lockbox_secret.runtime.id
  role      = "lockbox.payloadViewer"
  member    = "serviceAccount:${yandex_iam_service_account.runtime.id}"
}

resource "yandex_function" "api" {
  name               = "edium-join-api"
  description        = "Public form API and server-authorized candidate administration"
  folder_id          = var.folder_id
  runtime            = "python312"
  entrypoint         = "handler.api"
  memory             = 512
  execution_timeout  = 30
  service_account_id = yandex_iam_service_account.runtime.id
  user_hash          = data.archive_file.function.output_base64sha256
  environment = {
    RESUME_BUCKET   = yandex_storage_bucket.resumes.bucket
    YDB_ENDPOINT    = yandex_ydb_database_serverless.applications.ydb_api_endpoint
    YDB_DATABASE    = yandex_ydb_database_serverless.applications.database_path
    ALLOWED_ORIGINS = join(",", var.allowed_origins)
    ADMIN_URL       = var.admin_url
  }
  secrets {
    id                   = yandex_lockbox_secret.runtime.id
    version_id           = yandex_lockbox_secret_version.runtime.id
    key                  = "s3-access-key"
    environment_variable = "S3_ACCESS_KEY"
  }
  secrets {
    id                   = yandex_lockbox_secret.runtime.id
    version_id           = yandex_lockbox_secret_version.runtime.id
    key                  = "s3-secret-key"
    environment_variable = "S3_SECRET_KEY"
  }
  secrets {
    id                   = yandex_lockbox_secret.runtime.id
    version_id           = yandex_lockbox_secret_version.runtime.id
    key                  = "admin-token"
    environment_variable = "ADMIN_TOKEN"
  }
  secrets {
    id                   = yandex_lockbox_secret.runtime.id
    version_id           = yandex_lockbox_secret_version.runtime.id
    key                  = "ip-hash-salt"
    environment_variable = "IP_HASH_SALT"
  }
  content { zip_filename = data.archive_file.function.output_path }
  log_options { min_level = "ERROR" }
  tags   = ["$latest"]
  labels = local.tags
  depends_on = [
    yandex_lockbox_secret_iam_member.runtime,
    yandex_storage_bucket_iam_binding.runtime_editor,
    yandex_ydb_database_iam_binding.runtime_editor,
  ]
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
    RESUME_BUCKET    = yandex_storage_bucket.resumes.bucket
    YDB_ENDPOINT     = yandex_ydb_database_serverless.applications.ydb_api_endpoint
    YDB_DATABASE     = yandex_ydb_database_serverless.applications.database_path
    ALLOWED_ORIGINS  = join(",", var.allowed_origins)
    EMAIL_MODE       = var.email_mode
    TEAM_EMAIL       = var.team_email
    HERALD_EMAIL_URL = var.herald_email_url
    ADMIN_URL        = var.admin_url
  }
  secrets {
    id                   = yandex_lockbox_secret.runtime.id
    version_id           = yandex_lockbox_secret_version.runtime.id
    key                  = "s3-access-key"
    environment_variable = "S3_ACCESS_KEY"
  }
  secrets {
    id                   = yandex_lockbox_secret.runtime.id
    version_id           = yandex_lockbox_secret_version.runtime.id
    key                  = "s3-secret-key"
    environment_variable = "S3_SECRET_KEY"
  }
  secrets {
    id                   = yandex_lockbox_secret.runtime.id
    version_id           = yandex_lockbox_secret_version.runtime.id
    key                  = "admin-token"
    environment_variable = "ADMIN_TOKEN"
  }
  secrets {
    id                   = yandex_lockbox_secret.runtime.id
    version_id           = yandex_lockbox_secret_version.runtime.id
    key                  = "ip-hash-salt"
    environment_variable = "IP_HASH_SALT"
  }
  dynamic "secrets" {
    for_each = var.email_mode == "herald" ? { herald = true } : {}
    content {
      id                   = yandex_lockbox_secret.runtime.id
      version_id           = yandex_lockbox_secret_version.runtime.id
      key                  = "herald-api-key"
      environment_variable = "HERALD_API_KEY"
    }
  }
  content { zip_filename = data.archive_file.function.output_path }
  log_options { min_level = "ERROR" }
  tags   = ["$latest"]
  labels = local.tags
  depends_on = [
    yandex_lockbox_secret_iam_member.runtime,
    yandex_storage_bucket_iam_binding.runtime_editor,
    yandex_ydb_database_iam_binding.runtime_editor,
  ]
  lifecycle {
    precondition {
      condition     = var.email_mode == "disabled" || (var.team_email != "" && var.herald_api_key != "")
      error_message = "email_mode=herald requires team_email and herald_api_key."
    }
  }
}

resource "yandex_function_iam_binding" "gateway_api_invoker" {
  function_id = yandex_function.api.id
  role        = "functions.functionInvoker"
  members     = ["serviceAccount:${yandex_iam_service_account.gateway.id}"]
}

resource "yandex_function_iam_binding" "gateway_maintenance_invoker" {
  function_id = yandex_function.maintenance.id
  role        = "functions.functionInvoker"
  members     = ["serviceAccount:${yandex_iam_service_account.gateway.id}"]
}

resource "yandex_api_gateway" "join" {
  name        = "edium-join-api"
  description = "API Gateway for the Edium team application form"
  folder_id   = var.folder_id
  labels      = local.tags
  spec = templatefile("${path.module}/openapi.yaml.tftpl", {
    function_id        = yandex_function.api.id
    service_account_id = yandex_iam_service_account.gateway.id
  })
  depends_on = [yandex_function_iam_binding.gateway_api_invoker]
}

resource "yandex_function_trigger" "maintenance" {
  name        = "edium-join-maintenance"
  description = "Every five minutes: outbox delivery and orphan cleanup"
  folder_id   = var.folder_id
  timer { cron_expression = "*/5 * ? * * *" }
  function {
    id                 = yandex_function.maintenance.id
    service_account_id = yandex_iam_service_account.gateway.id
    retry_attempts     = 1
    retry_interval     = 30
  }
  labels     = local.tags
  depends_on = [yandex_function_iam_binding.gateway_maintenance_invoker]
}
