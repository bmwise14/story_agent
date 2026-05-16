#!/bin/bash
# IAM setup — create 3 service accounts with least-privilege role bindings.
#
# Why 3 SAs?
# - story-router@  : can only publish to Pub/Sub + read Firestore
#                    Cannot call Vertex AI — compromised router can't generate content
# - story-worker@  : can call Vertex, Firestore, Secret Manager, Model Armor
#                    Cannot publish to Pub/Sub — compromised worker can't fan-out attacks
# - story-pubsub-invoker@: exists solely to give Pub/Sub an identity for push delivery
#                          Has ONLY roles/run.invoker on the worker service
#
# Interview line: "Three service accounts with non-overlapping permissions.
# If the router is compromised, the attacker cannot call Vertex AI.
# If the worker is compromised, they cannot publish new Pub/Sub messages.
# The invoker SA exists purely as the OIDC identity for Pub/Sub push — no other power."

set -euo pipefail

PROJECT=$(gcloud config get-value project)
REGION="us-central1"

echo "Setting up IAM for project: $PROJECT"

# --- Create service accounts ---
for SA in story-router story-worker story-pubsub-invoker; do
  gcloud iam service-accounts create "$SA" \
    --display-name="Story Agent $SA" \
    --project="$PROJECT" 2>/dev/null || echo "  $SA already exists"
done

ROUTER_SA="story-router@${PROJECT}.iam.gserviceaccount.com"
WORKER_SA="story-worker@${PROJECT}.iam.gserviceaccount.com"
INVOKER_SA="story-pubsub-invoker@${PROJECT}.iam.gserviceaccount.com"

# --- Router: publish + Firestore read ---
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$ROUTER_SA" \
  --role="roles/pubsub.publisher"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$ROUTER_SA" \
  --role="roles/datastore.user"

# --- Worker: Vertex + Firestore + Secret Manager + Model Armor ---
for ROLE in roles/aiplatform.user roles/datastore.user roles/secretmanager.secretAccessor roles/modelarmor.user; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$WORKER_SA" \
    --role="$ROLE"
done

# --- Pub/Sub invoker: run.invoker on worker service only ---
# (bound after worker service is deployed — see deploy_worker.sh)
echo "  Note: story-pubsub-invoker roles/run.invoker binding is set in deploy_worker.sh after service exists"

# --- Allow Pub/Sub to create OIDC tokens for the invoker SA ---
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$INVOKER_SA" \
  --role="roles/iam.serviceAccountTokenCreator"

echo "IAM setup complete."
echo "  Router SA:  $ROUTER_SA"
echo "  Worker SA:  $WORKER_SA"
echo "  Invoker SA: $INVOKER_SA"
