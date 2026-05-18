terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

# ---------------------------------------------------------------------------
# Artifact Registry
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "story_agent" {
  repository_id = "story-agent"
  location      = var.region
  format        = "DOCKER"
  description   = "Story Agent container images"
}

# ---------------------------------------------------------------------------
# Service accounts
# ---------------------------------------------------------------------------

resource "google_service_account" "router" {
  account_id   = "story-router"
  display_name = "Story Agent story-router"
}

resource "google_service_account" "worker" {
  account_id   = "story-worker"
  display_name = "Story Agent story-worker"
}

resource "google_service_account" "pubsub_invoker" {
  account_id   = "story-pubsub-invoker"
  display_name = "Story Agent story-pubsub-invoker"
}

# ---------------------------------------------------------------------------
# Project-level IAM bindings
# ---------------------------------------------------------------------------

# Router: can publish to Pub/Sub
resource "google_project_iam_member" "router_pubsub" {
  project = var.project
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.router.email}"
}

# Router: can read/write Cloud SQL (job store)
resource "google_project_iam_member" "router_cloudsql" {
  project = var.project
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.router.email}"
}

# Worker: call Vertex AI / LLM APIs
resource "google_project_iam_member" "worker_vertex" {
  project = var.project
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

# Worker: read/write Cloud SQL (checkpoints + job variants)
resource "google_project_iam_member" "worker_cloudsql" {
  project = var.project
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

# Worker: read OAuth refresh tokens from Secret Manager
resource "google_project_iam_member" "worker_secrets" {
  project = var.project
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

# Worker: call Model Armor for content screening
resource "google_project_iam_member" "worker_model_armor" {
  project = var.project
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

# Pub/Sub invoker SA: allowed to generate OIDC tokens (used to sign push requests)
resource "google_project_iam_member" "invoker_token_creator" {
  project = var.project
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:${google_service_account.pubsub_invoker.email}"
}

# ---------------------------------------------------------------------------
# Pub/Sub topic
# ---------------------------------------------------------------------------

resource "google_pubsub_topic" "story_requests" {
  name = "story-requests"
}

# ---------------------------------------------------------------------------
# Cloud Run: router (public)
# ---------------------------------------------------------------------------

locals {
  common_env = [
    { name = "GOOGLE_CLOUD_PROJECT",          value = var.project },
    { name = "AZURE_OPENAI_ENDPOINT",         value = var.azure_openai_endpoint },
    { name = "AZURE_OPENAI_DEPLOYMENT_NAME",  value = var.azure_openai_deployment_name },
    { name = "AZURE_OPENAI_API_KEY",          value = var.azure_openai_api_key },
    { name = "OAUTH_CLIENT_ID",               value = var.oauth_client_id },
    { name = "OAUTH_CLIENT_SECRET",           value = var.oauth_client_secret },
    { name = "OAUTH_REDIRECT_URI",            value = var.oauth_redirect_uri },
    { name = "DB_HOST",                       value = var.db_host },
    { name = "DB_PORT",                       value = var.db_port },
    { name = "DB_USER",                       value = var.db_user },
    { name = "DB_PASSWORD",                   value = var.db_password },
    { name = "DB_NAME",                       value = var.db_name },
  ]
}

resource "google_cloud_run_v2_service" "router" {
  name     = "story-router"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.router.email

    scaling {
      min_instance_count = 1
      max_instance_count = 5
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      env {
        name  = "SERVICE_ROLE"
        value = "router"
      }
    }

    max_instance_request_concurrency = 80
  }
}

# Allow unauthenticated traffic to the router (it's the public entry point)
resource "google_cloud_run_v2_service_iam_member" "router_public" {
  project  = var.project
  location = var.region
  name     = google_cloud_run_v2_service.router.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# Cloud Run: worker (internal)
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "worker" {
  name     = "story-worker"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.worker.email

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    timeout = "300s"

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
      }

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      env {
        name  = "SERVICE_ROLE"
        value = "worker"
      }
    }

    max_instance_request_concurrency = 1
  }
}

# Only story-pubsub-invoker@ can invoke the worker (Pub/Sub uses this identity)
resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  project  = var.project
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_invoker.email}"
}

# ---------------------------------------------------------------------------
# Pub/Sub push subscription → worker
# ---------------------------------------------------------------------------

resource "google_pubsub_subscription" "worker_push" {
  name  = "story-worker-push"
  topic = google_pubsub_topic.story_requests.name

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.worker.uri}/internal/worker"

    oidc_token {
      service_account_email = google_service_account.pubsub_invoker.email
    }
  }

  ack_deadline_seconds = 300

  # Retry policy: exponential backoff between 10s and 600s
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}
