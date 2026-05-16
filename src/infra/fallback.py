"""
GPT-4o → GPT-4o-mini fallback on 429 / rate limit errors.

Strategy:
  1. Try GPT-4o with exponential backoff + full jitter (up to 4 attempts)
  2. If still rate-limited after backoff, swap to GPT-4o-mini
  3. User sees a marginal quality drop instead of a 30-second hang or error screen

Interview line: "The user sees a marginal quality drop instead of a 30-second
hang or an error screen. GPT-4o-mini is meaningfully cheaper and faster — it's
not a terrible fallback. We log a metric on every fallback so we know if GPT-4o
is consistently saturated and needs quota increases."
"""

import os

from dotenv import load_dotenv
from openai import RateLimitError
# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage

from src.infra.retry import with_backoff

load_dotenv()

_RATE_LIMIT_EXCEPTIONS = (RateLimitError,)


def _build_llm(deployment: str, temperature: float = 0.7) -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_deployment=deployment,
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version="2024-08-01-preview",
        temperature=temperature,
    )


def call_with_fallback(
    prompt: str,
    temperature: float = 0.7,
) -> str:
    """
    Call the primary Azure deployment with backoff; fall back to the fallback
    deployment on persistent 429s.

    Primary:  AZURE_OPENAI_DEPLOYMENT_NAME
    Fallback: AZURE_OPENAI_FALLBACK_DEPLOYMENT (defaults to primary if not set)
    """
    primary = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")
    fallback = os.getenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", primary)

    primary_llm = _build_llm(primary, temperature)

    def _call_primary() -> str:
        result = primary_llm.invoke([HumanMessage(content=prompt)])
        return result.content

    try:
        return with_backoff(
            _call_primary,
            max_attempts=4,
            base_delay=1.0,
            max_delay=16.0,
            retryable_exceptions=_RATE_LIMIT_EXCEPTIONS,
        )
    except _RATE_LIMIT_EXCEPTIONS:
        print(f"  [fallback] primary deployment rate-limited — switching to {fallback}")
        fallback_llm = _build_llm(fallback, temperature)
        result = fallback_llm.invoke([HumanMessage(content=prompt)])
        return result.content
