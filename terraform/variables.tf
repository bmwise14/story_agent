variable "project" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Full Artifact Registry image URL (built and pushed separately via gcloud builds submit)"
  type        = string
  # e.g. us-central1-docker.pkg.dev/intense-optics-474721-n7/story-agent/story-agent:latest
}

# --- Application env vars passed to both Cloud Run services ---

variable "azure_openai_endpoint" { type = string }
variable "azure_openai_deployment_name" { type = string }
variable "azure_openai_api_key" {
  type      = string
  sensitive = true
}
variable "oauth_client_id" { type = string }
variable "oauth_client_secret" {
  type      = string
  sensitive = true
}
variable "oauth_redirect_uri" { type = string }
variable "db_host" { type = string }
variable "db_port" { type = string, default = "5432" }
variable "db_user" { type = string }
variable "db_password" {
  type      = string
  sensitive = true
}
variable "db_name" { type = string }
