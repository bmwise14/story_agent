"""
OAuth calendar client — Phase 2.

Three responsibilities:
  1. store_refresh_token()  — called once from /auth/callback; writes the
                              refresh token to Secret Manager.
  2. get_access_token()     — called at agent execution time; reads the
                              refresh token from Secret Manager and exchanges
                              it for a fresh, short-lived access token.
  3. book_game_night()      — uses get_access_token() to create a Calendar
                              event on the user's behalf.

Identity contrast (interview talking point):
  - The Cloud Run service account is the agent's own identity (agent-as-itself).
  - The OAuth access token is the *user's* identity delegated to the agent
    (agent-as-user). Both are used in Phase 3 within a single worker execution.
"""

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.cloud import secretmanager
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
SECRET_PREFIX = "oauth-refresh"


def _secret_name(user_id: str) -> str:
    return f"projects/{PROJECT_ID}/secrets/{SECRET_PREFIX}-{user_id}/versions/latest"


def _secret_id(user_id: str) -> str:
    return f"{SECRET_PREFIX}-{user_id}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def store_refresh_token(user_id: str, refresh_token: str) -> None:
    """
    Write the refresh token to Secret Manager.

    Called exactly once per user — from /auth/callback after the initial
    OAuth code exchange. The token is never written to app state, logs, or
    the database.

    Secret name pattern: oauth-refresh-{user_id}
    """
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT_ID}"
    secret_id = _secret_id(user_id)

    # Create the secret container if it doesn't exist yet
    try:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            }
        )
    except Exception:
        pass  # already exists — just add a new version

    client.add_secret_version(
        request={
            "parent": f"{parent}/secrets/{secret_id}",
            "payload": {"data": refresh_token.encode("utf-8")},
        }
    )
    print(f"  [oauth] refresh token stored → Secret Manager: {secret_id}")


# ---------------------------------------------------------------------------
# Retrieve + mint access token
# ---------------------------------------------------------------------------

def get_access_token(user_id: str) -> str:
    """
    Read refresh token from Secret Manager, exchange for a fresh access token.

    The access token is valid for ~1 hour. It is never persisted — callers
    use it immediately and discard it. Short lifetime limits blast radius if
    the token leaks (e.g., in a log line).
    """
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": _secret_name(user_id)})
    refresh_token = response.payload.data.decode("utf-8")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["OAUTH_CLIENT_ID"],
        client_secret=os.environ["OAUTH_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    creds.refresh(Request())
    print(f"  [oauth] access token minted for user '{user_id}' (expires ~1 hour)")
    return creds.token


# ---------------------------------------------------------------------------
# Calendar action
# ---------------------------------------------------------------------------

def book_game_night(user_id: str, story_title: str, date_iso: str) -> str:
    """
    Create a 'Game Night' Calendar event on the user's calendar.

    Args:
        user_id:     identifies which Secret Manager secret to read
        story_title: used as the event description
        date_iso:    ISO date string e.g. "2026-05-19"

    Returns:
        HTML link to the created event.

    This function demonstrates the full agent-as-user flow:
      Secret Manager → refresh token → access token → Calendar API → event
    """
    access_token = get_access_token(user_id)

    creds = Credentials(
        token=access_token,
        scopes=SCOPES,
    )
    service = build("calendar", "v3", credentials=creds)

    start_dt = datetime.fromisoformat(date_iso).replace(
        hour=19, minute=0, tzinfo=timezone.utc
    )
    end_dt = start_dt + timedelta(hours=2)

    event = {
        "summary": f"Game Night — {story_title}",
        "description": (
            f"Reading session for '{story_title}', generated by the Story Agent."
        ),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "UTC"},
    }

    created = service.events().insert(calendarId="primary", body=event).execute()
    link = created.get("htmlLink", "")
    print(f"  [calendar] event created → {link}")
    return link
