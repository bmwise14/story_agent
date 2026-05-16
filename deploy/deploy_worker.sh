#!/bin/bash
# Deploy the internal worker Cloud Run service + wire Pub/Sub push subscription.
#
# Settings rationale:
#   --ingress=internal          : NO public URL — only Pub/Sub push can invoke this
#   --no-allow-unauthenticated  : OIDC token required (Pub/Sub push provides one)
#   --concurrency=1             : one slow LLM call per instance
#                                 forces Cloud Run to scale OUT (not up) per request
#                                 packing more requests per instance increases p99 latency
#   --min-instances=0           : workers are bursty; OK to cold-start under traffic
#   --max-instances=10          : hard cap — 3 fan-out messages needs at least 3

set -euo pipefail

PROJECT=$(gcloud config get-value project)
REGION="us-central1"
IMAGE="gcr.io/${PROJECT}/story-agent:latest"
SERVICE="story-worker"
WORKER_SA="story-worker@${PROJECT}.iam.gserviceaccount.com"
INVOKER_SA="story-pubsub-invoker@${PROJECT}.iam.gserviceaccount.com"
TOPIC="story-requests"
SUBSCRIPTION="story-worker-push"

echo "Deploying worker (uses same image as router)..."
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$WORKER_SA" \
  --set-env-vars="SERVICE_ROLE=worker,GOOGLE_CLOUD_PROJECT=${PROJECT}" \
  --ingress=internal \
  --no-allow-unauthenticated \
  --concurrency=1 \
  --min-instances=0 \
  --max-instances=10 \
  --memory=2Gi \
  --cpu=1 \
  --timeout=300

WORKER_URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format="value(status.url)")
echo "Worker URL (internal only): $WORKER_URL"

# Grant Pub/Sub invoker permission to call the worker
gcloud run services add-iam-policy-binding "$SERVICE" \
  --region="$REGION" \
  --member="serviceAccount:$INVOKER_SA" \
  --role="roles/run.invoker"

# Create Pub/Sub push subscription pointing at the worker
# OIDC token authenticates as INVOKER_SA — Cloud Run validates it automatically
gcloud pubsub subscriptions create "$SUBSCRIPTION" \
  --topic="$TOPIC" \
  --push-endpoint="${WORKER_URL}/internal/worker" \
  --push-auth-service-account="$INVOKER_SA" \
  --ack-deadline=300 \
  2>/dev/null || echo "  Subscription already exists — updating endpoint..."

echo "Pub/Sub push subscription '$SUBSCRIPTION' → $WORKER_URL/internal/worker"
echo "Worker deployment complete."
echo ""
echo "Verify no public access:"
echo "  curl $WORKER_URL/health   # should return 403"
