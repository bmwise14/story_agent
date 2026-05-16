"""
Pub/Sub publish helper.

The router calls publish_story_request() once per user request, publishing
3 messages (variant_id 0, 1, 2) to the story-requests topic. Pub/Sub delivers
each message to a separate worker instance via push subscription.

Interview line: "Pub/Sub absorbs the LLM latency. The router returns 202
immediately after publishing — the user doesn't wait for generation. Each
of the 3 messages triggers a separate Cloud Run worker instance because
concurrency=1, giving us parallel fan-out with no code changes."
"""

import json
import os

from dotenv import load_dotenv
from google.cloud import pubsub_v1

load_dotenv()

TOPIC_ID = "story-requests"


def publish_story_request(
    job_id: str,
    config_dict: dict,
    user_id: str,
    n_variants: int = 3,
) -> list[str]:
    """
    Publish n_variants messages to the story-requests Pub/Sub topic.

    Each message carries { job_id, variant_id, config, user_id }.
    The full StoryConfig is included so the worker can run the LangGraph
    agent without needing to look anything up.

    The push subscription delivers each message to /internal/worker on the
    worker Cloud Run service, authenticated via OIDC token from the
    story-pubsub-invoker@ service account.
    """
    project_id = os.environ["GOOGLE_CLOUD_PROJECT"]
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, TOPIC_ID)

    message_ids = []
    for variant_id in range(n_variants):
        payload = json.dumps({
            "job_id": job_id,
            "variant_id": variant_id,
            "config": config_dict,
            "user_id": user_id,
        }).encode("utf-8")

        future = publisher.publish(topic_path, payload)
        message_ids.append(future.result())
        print(f"  [pubsub] published variant {variant_id} for job {job_id}")

    return message_ids
