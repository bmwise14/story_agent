"""
FastAPI app — Phase 2 + Phase 3.

Routes:
  GET  /auth/login          — OAuth consent redirect
  GET  /auth/callback       — OAuth code exchange, stores refresh token
  POST /story/start         — validates prompt, publishes 3 Pub/Sub messages, returns 202 + job_id
  GET  /story/{job_id}      — polls Postgres for job status and winning chapter
  POST /internal/worker     — Pub/Sub push target (internal ingress only)

Persistence: same game_stories Postgres DB used by LangGraph + ADK.
In Cloud Run, DB_HOST points to AlloyDB — no code changes required.

Run locally:
  uvicorn src.api.main:app --reload --port 8000
"""

import base64
import json
import os
import secrets
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel

from src.infra.db import setup_jobs_table, create_job, get_job, write_variant_and_maybe_judge, finalize_job

load_dotenv()

app = FastAPI(title="Story Agent API")


@app.on_event("startup")
def startup():
    setup_jobs_table()

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Hardcoded for the demo — in production this would come from the session
# (e.g., a JWT or session cookie identifying the logged-in user).
DEMO_USER_ID = "demo-user"

# In-process store for the flow object so the callback can reuse the same
# instance (needed to carry the code_verifier for PKCE through the exchange).
# In production this would be a distributed cache keyed to the state param.
_flow_store: dict[str, Flow] = {}


def _build_flow() -> Flow:
    """Construct an OAuth flow from environment variables."""
    return Flow.from_client_config(
        client_config={
            "web": {
                "client_id": os.environ["OAUTH_CLIENT_ID"],
                "client_secret": os.environ["OAUTH_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [os.environ["OAUTH_REDIRECT_URI"]],
            }
        },
        scopes=SCOPES,
        redirect_uri=os.environ["OAUTH_REDIRECT_URI"],
    )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/auth/login")
def login() -> RedirectResponse:
    """
    Step 1 of the OAuth flow.

    Redirects the browser to Google's consent screen. Google will show the
    user which scopes the app is requesting (calendar.events only — least
    privilege) and ask them to Allow or Deny.

    access_type=offline tells Google to return a refresh token (not just an
    access token), which is what we need for the agent to act on the user's
    behalf in future runs without re-prompting.

    We store the flow object in _flow_store keyed to the state param so the
    callback can reuse the same instance — required for PKCE code_verifier.
    """
    flow = _build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",   # force consent screen so Google always returns refresh token
    )
    _flow_store[state] = flow
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
def callback(request: Request, code: str, state: str) -> HTMLResponse:
    """
    Step 2 of the OAuth flow — Google redirects here after user clicks Allow.

    Google appends ?code=AUTH_CODE&state=... to the redirect URI. This route:
      1. Retrieves the original flow (carrying the PKCE code_verifier)
      2. Exchanges the code for an access token + refresh token
      3. Stores the refresh token in Secret Manager (never in app state)
      4. Discards the access token — it will be re-minted at execution time

    The refresh token is the long-lived credential. It lives only in
    Secret Manager, keyed to the user_id. The agent retrieves it at
    runtime via get_access_token().
    """
    from src.oauth.calendar_client import store_refresh_token

    # Reuse the same flow instance from /auth/login to carry the code_verifier
    flow = _flow_store.pop(state, None) or _build_flow()
    flow.fetch_token(
        code=code,
        authorization_response=str(request.url),
    )

    refresh_token = flow.credentials.refresh_token
    store_refresh_token(user_id=DEMO_USER_ID, refresh_token=refresh_token)

    return HTMLResponse(
        content="""
        <html><body>
        <h2>Authorization complete.</h2>
        <p>Refresh token stored in Secret Manager.</p>
        <p>You can close this tab. The agent can now book calendar events on your behalf.</p>
        </body></html>
        """
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Story routes (Phase 3)
# ---------------------------------------------------------------------------

class StoryStartRequest(BaseModel):
    prompt: str
    user_id: str = DEMO_USER_ID


@app.post("/story/start")
async def story_start(body: StoryStartRequest) -> JSONResponse:
    """
    1. Screen prompt through Model Armor (pre-LLM guardrail)
    2. Generate job_id, write pending record to Firestore
    3. Publish 3 Pub/Sub messages (one per variant) — fan-out to 3 workers
    4. Return 202 immediately — user polls GET /story/{job_id}

    The router never calls Vertex AI. Its only job is cheap I/O:
    validate → store → publish → return. High concurrency (80) is appropriate.
    """
    from src.guardrails.model_armor import screen_prompt
    from src.infra.pubsub_client import publish_story_request

    # Pre-LLM guardrail
    screen = screen_prompt(body.prompt)
    if not screen.allowed:
        raise HTTPException(status_code=400, detail={
            "error": "prompt_blocked",
            "violations": screen.violations,
        })

    job_id = str(uuid.uuid4())

    # Write pending job to Postgres (same game_stories DB as LangGraph + ADK)
    create_job(job_id=job_id, prompt=screen.sanitized_text, user_id=body.user_id)

    # Fan-out: 3 Pub/Sub messages → 3 worker instances (concurrency=1 forces scale-out)
    publish_story_request(job_id=job_id, prompt=screen.sanitized_text, n_variants=3)

    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})


@app.get("/story/{job_id}")
async def story_status(job_id: str) -> dict:
    """
    Poll for job completion. Returns status + winning chapter when done.

    Browser polls this until status == "complete".
    Uses same Postgres DB as LangGraph — consistent persistence layer.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


# ---------------------------------------------------------------------------
# Worker endpoint (Phase 3) — internal ingress only, Pub/Sub push target
# ---------------------------------------------------------------------------

@app.post("/internal/worker")
async def worker(request: Request) -> dict:
    """
    Pub/Sub push target. Cloud Run worker service only — no public URL.

    Pub/Sub delivers a base64-encoded message body. This route:
      1. Decodes the message
      2. Runs the LangGraph story agent (one chapter, one variant)
      3. Screens output through Model Armor (post-LLM)
      4. Writes variant to Firestore
      5. If all 3 variants are done, runs the LLM judge and writes the winner
      6. Books a calendar event on the user's behalf (agent-as-user demo)

    The OIDC token in the Authorization header is verified by Cloud Run
    automatically — only the story-pubsub-invoker@ SA can reach this endpoint.
    """
    from src.guardrails.model_armor import screen_response
    from src.evaluator.llm_judge import pick_winner
    from src.oauth.calendar_client import book_game_night

    # Decode Pub/Sub message
    body = await request.json()
    pubsub_message = body.get("message", {})
    data = base64.b64decode(pubsub_message.get("data", "")).decode("utf-8")
    payload = json.loads(data)

    job_id = payload["job_id"]
    variant_id = payload["variant_id"]
    prompt = payload["prompt"]

    print(f"  [worker] job={job_id} variant={variant_id}")

    # Run the story agent (uses call_with_fallback internally in production)
    # For now: generate a placeholder chapter to prove the plumbing works
    from src.infra.fallback import call_with_fallback
    chapter_text = call_with_fallback(
        f"Write the opening chapter of a story with this premise: {prompt}"
    )

    # Post-LLM guardrail
    screen = screen_response(chapter_text)
    safe_text = screen.sanitized_text if screen.allowed else "[content blocked by guardrail]"

    # Write variant to Postgres; returns all variants if this was the last one.
    # SELECT FOR UPDATE inside write_variant_and_maybe_judge ensures exactly one
    # worker triggers the judge — same guarantee as a Firestore transaction.
    all_variants = write_variant_and_maybe_judge(
        job_id=job_id, variant_id=variant_id, chapter_text=safe_text
    )

    if all_variants is not None:
        # This worker was last — run judge and finalize
        winner_id, reasoning = pick_winner({int(k): v for k, v in all_variants.items()})
        finalize_job(
            job_id=job_id,
            winner_variant_id=winner_id,
            winner_text=all_variants[str(winner_id)],
            judge_reasoning=reasoning,
        )
        # Agent-as-user: book calendar event on behalf of the user (Phase 2 + 3 bridge)
        job = get_job(job_id)
        user_id = job.get("user_id", DEMO_USER_ID) if job else DEMO_USER_ID
        try:
            book_game_night(user_id=user_id, story_title=prompt[:40], date_iso="2026-05-20")
        except Exception as e:
            print(f"  [worker] calendar booking failed (non-fatal): {e}")

    return {"status": "ok", "variant_id": variant_id}
