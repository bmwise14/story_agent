"""
Model Armor guardrails — pre and post LLM wrappers.

Model Armor is a GCP service that screens text for:
  - Prompt injection / jailbreak attempts
  - PII (SSN, credit card, medical record numbers)
  - Sensitive data categories

Placement in the request path:
  pre-LLM  → screen the user's raw prompt before it reaches Vertex AI
  post-LLM → screen the generated response before it reaches the user

Interview line: "Deterministic guardrails, not LLM-checking-LLM. Model Armor
runs as a separate API call before and after the generation step. Pre-LLM
blocks jailbreaks before they consume tokens. Post-LLM catches any PII the
model might have hallucinated or extracted from context. Using a dedicated
guardrail service is cheaper and faster than a second LLM call for safety."
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from google.cloud import modelarmor_v1

load_dotenv()

TEMPLATE_NAME = "story-guardrail"


@dataclass
class ScreenResult:
    allowed: bool
    sanitized_text: str
    violations: list[str]


def _get_client() -> modelarmor_v1.ModelArmorClient:
    return modelarmor_v1.ModelArmorClient()


def _template_path() -> str:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return f"projects/{project}/locations/{location}/templates/{TEMPLATE_NAME}"


def screen_prompt(text: str) -> ScreenResult:
    """
    Pre-LLM screen: block prompt injection and PII in the user's input.

    Call this before sending any user-supplied text to Vertex AI.
    If allowed=False, return a 400 to the user — do not call Vertex.
    """
    try:
        client = _get_client()
        response = client.sanitize_user_prompt(
            request=modelarmor_v1.SanitizeUserPromptRequest(
                name=_template_path(),
                user_prompt_data=modelarmor_v1.DataItem(text=text),
            )
        )
        result = response.sanitization_result
        violations = [
            f.filter_type.name
            for f in result.filter_match_state
            if f.filter_match_state != modelarmor_v1.FilterMatchState.NO_MATCH
        ] if hasattr(result, "filter_match_state") else []

        allowed = result.action != modelarmor_v1.SanitizationResult.Action.BLOCK
        sanitized = result.sanitized_prompt_text or text
        return ScreenResult(allowed=allowed, sanitized_text=sanitized, violations=violations)

    except Exception as e:
        # Fail open in dev — log and allow. In production, set fail_closed=True.
        print(f"  [armor] pre-screen error (failing open): {e}")
        return ScreenResult(allowed=True, sanitized_text=text, violations=[])


def screen_response(text: str) -> ScreenResult:
    """
    Post-LLM screen: block PII or policy violations in the model's output.

    Call this before returning the generated text to the user.
    If allowed=False, return a safe fallback message instead of the raw output.
    """
    try:
        client = _get_client()
        response = client.sanitize_model_response(
            request=modelarmor_v1.SanitizeModelResponseRequest(
                name=_template_path(),
                model_response_data=modelarmor_v1.DataItem(text=text),
            )
        )
        result = response.sanitization_result
        violations = [
            f.filter_type.name
            for f in result.filter_match_state
            if f.filter_match_state != modelarmor_v1.FilterMatchState.NO_MATCH
        ] if hasattr(result, "filter_match_state") else []

        allowed = result.action != modelarmor_v1.SanitizationResult.Action.BLOCK
        sanitized = result.sanitized_model_response_text or text
        return ScreenResult(allowed=allowed, sanitized_text=sanitized, violations=violations)

    except Exception as e:
        print(f"  [armor] post-screen error (failing open): {e}")
        return ScreenResult(allowed=True, sanitized_text=text, violations=[])
