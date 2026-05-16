"""
FastAPI app — Phase 2 bootstrap.

Routes added here (Phase 2):
  GET /auth/login     — redirects browser to Google OAuth consent screen
  GET /auth/callback  — receives auth code, exchanges for tokens, stores
                        refresh token in Secret Manager

Routes added in Phase 3:
  POST /story/start
  GET  /story/{job_id}
  POST /internal/worker  (Pub/Sub push target)

Run locally:
  uvicorn src.api.main:app --reload --port 8000
"""

import os
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow

load_dotenv()

app = FastAPI(title="Story Agent API")

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
