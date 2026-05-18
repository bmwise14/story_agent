output "router_url" {
  description = "Public URL for the story router"
  value       = google_cloud_run_v2_service.router.uri
}

output "worker_url" {
  description = "Internal URL for the story worker (not reachable from public internet)"
  value       = google_cloud_run_v2_service.worker.uri
}

output "pubsub_topic" {
  description = "Pub/Sub topic name"
  value       = google_pubsub_topic.story_requests.name
}

output "image_repo" {
  description = "Artifact Registry repo URL — use this as the base for gcloud builds submit"
  value       = "${var.region}-docker.pkg.dev/${var.project}/${google_artifact_registry_repository.story_agent.repository_id}"
}
