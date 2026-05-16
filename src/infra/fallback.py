"""
Gemini Pro → Flash fallback on 429 / rate limit errors.

Strategy:
  1. Try Gemini Pro with exponential backoff + full jitter (up to 4 attempts)
  2. If still rate-limited after backoff, swap to Gemini Flash
  3. User sees a marginal quality drop instead of a 30-second hang or error screen

Interview line: "The user sees a marginal quality drop instead of a 30-second
hang or an error screen. Flash is meaningfully cheaper and faster — it's not a
terrible fallback. We log a metric on every fallback so we know if Pro is
consistently saturated and needs quota increases."
"""

import os
from typing import Callable

from dotenv import load_dotenv
from google.api_core.exceptions import ResourceExhausted, TooManyRequests
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

from src.infra.retry import with_backoff

load_dotenv()

_RATE_LIMIT_EXCEPTIONS = (ResourceExhausted, TooManyRequests)


def _build_llm(model: str, temperature: float = 0.7) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=os.environ["GOOGLE_API_KEY"],
        temperature=temperature,
    )


def call_with_fallback(
    prompt: str,
    pro_model: str = "gemini-2.5-pro",
    flash_model: str = "gemini-2.5-flash",
    temperature: float = 0.7,
) -> str:
    """
    Call Gemini Pro with backoff; fall back to Flash on persistent 429s.

    Args:
        prompt:      The prompt string to send.
        pro_model:   Primary model name.
        flash_model: Fallback model name (faster, cheaper, slightly lower quality).
        temperature: Sampling temperature for both models.

    Returns:
        Generated text string.
    """
    pro_llm = _build_llm(pro_model, temperature)

    def _call_pro() -> str:
        result = pro_llm.invoke([HumanMessage(content=prompt)])
        return result.content

    try:
        return with_backoff(
            _call_pro,
            max_attempts=4,
            base_delay=1.0,
            max_delay=16.0,
            retryable_exceptions=_RATE_LIMIT_EXCEPTIONS,
        )
    except _RATE_LIMIT_EXCEPTIONS:
        print(f"  [fallback] Pro rate-limited after backoff — switching to Flash ({flash_model})")
        flash_llm = _build_llm(flash_model, temperature)
        result = flash_llm.invoke([HumanMessage(content=prompt)])
        return result.content
