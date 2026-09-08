terraform {
  required_version = ">= 1.5"

  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "~> 0.225.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # Isolated from accounts/platform. This key must never be changed to platform.tfstate.
  backend "s3" {
    endpoint = "https://storage.yandexcloud.net"
    bucket   = "edium-tf-state-platform"
    key      = "join-site.tfstate"
    region   = "ru-central1"

    skip_region_validation      = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "yandex" {
  cloud_id                 = var.cloud_id
  folder_id                = var.folder_id
  zone                     = "ru-central1-a"
  service_account_key_file = var.sa_key_file
}
