variable "cloud_id" {
  type        = string
  description = "Yandex Cloud ID."
}

variable "folder_id" {
  type        = string
  description = "Folder for the isolated join-form resources."
}

variable "sa_key_file" {
  type        = string
  description = "Terraform service-account key path."
  default     = "key.json"
}

variable "resume_bucket_name" {
  type        = string
  description = "Globally unique private Object Storage bucket name."
  default     = "edium-team-applications"
}

variable "allowed_origins" {
  type        = list(string)
  description = "Exact browser origins allowed to call the API and upload resumes."
  default     = ["https://edium.online", "https://www.edium.online", "http://localhost:4173", "http://127.0.0.1:4173"]
}

variable "email_mode" {
  type        = string
  description = "disabled keeps the outbox pending; herald queues mail in the existing notification service."
  default     = "disabled"
  validation {
    condition     = contains(["disabled", "herald"], var.email_mode)
    error_message = "email_mode must be disabled or herald."
  }
}

variable "team_email" {
  type        = string
  description = "Team notification recipient. Leave empty in test mode."
  default     = ""
}

variable "herald_email_url" {
  type        = string
  description = "Protected Herald email enqueue endpoint."
  default     = "https://api.edium.online/herald/v1/emails"
}

variable "herald_api_key" {
  type        = string
  description = "Bearer token shared only by the join maintenance function and Herald."
  default     = ""
  sensitive   = true
}

variable "admin_url" {
  type        = string
  description = "Closed candidate-list URL included in team notifications."
  default     = "https://edium.online/join/admin/"
}
