#!/bin/bash
# Deploy the public router Cloud Run service.
#
# Settings rationale:
#   --ingress=all          : public entry point — users hit this directly
#   --allow-unauthenticated: browser-facing, no auth required at this layer
#   --concurrency=80       : router does cheap I/O (Pub/Sub publish + Firestore read)
#                            high concurrency is fine — no slow LLM calls here
#   --min-instances=1      : avoid cold start on the user-facing path
#   --max-instances=5      : cap cost; router doesn't need to scale much

set -euo pipefail

PROJECT=$(gcloud config get-value project)
REGION="us-central1"
IMAGE="gcr.io/${PROJECT}/story-agent:latest"
SERVICE="story-router"
ROUTER_SA="story-router@${PROJECT}.iam.gserviceaccount.com"

echo "Building and pushing image..."
gcloud builds submit --tag "$IMAGE" .

echo "Deploying router..."
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$ROUTER_SA" \
  --set-env-vars="SERVICE_ROLE=router,GOOGLE_CLOUD_PROJECT=${PROJECT}" \
  --ingress=all \
  --allow-unauthenticated \
  --concurrency=80 \
  --min-instances=1 \
  --max-instances=5 \
  --memory=512Mi \
  --cpu=1

echo "Router deployed:"
gcloud run services describe "$SERVICE" --region="$REGION" --format="value(status.url)"
