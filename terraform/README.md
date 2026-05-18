# Terraform — Story Agent Infrastructure

Replaces `deploy/iam_setup.sh`, `deploy/deploy_router.sh`, and `deploy/deploy_worker.sh`.

## What this manages

- Artifact Registry repo
- 3 service accounts + all IAM bindings (least privilege)
- Pub/Sub topic
- Cloud Run router (public, concurrency=80)
- Cloud Run worker (internal, concurrency=1)
- Pub/Sub push subscription wired to the worker

## What it does NOT manage

Docker image builds. Build and push the image first, then reference the URL in `terraform.tfvars`:

```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/YOUR_PROJECT/story-agent/story-agent:latest .
```

Terraform manages resources with persistent state. An image build is a one-time imperative action — no state to track, nothing to diff, nothing to destroy. The clean separation is: build externally, reference the URL in Terraform.

## Usage

```bash
cd terraform

# 1. Copy and fill in variables
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars

# 2. Initialise (downloads Google provider)
terraform init

# 3. Preview what will be created
terraform plan

# 4. Apply
terraform apply

# Outputs: router_url, worker_url, pubsub_topic, image_repo
```

## Teardown

```bash
terraform destroy
```

Destroys all resources in the right order. Cloud Run services, Pub/Sub subscription, topic, IAM bindings, service accounts, Artifact Registry — all gone in one command.
