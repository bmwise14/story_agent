"""
Chapter beats sub-agent.

Calls GPT-4o with structured output to generate 3-5 story beats and an
emotional arc for the requested chapter. Uses the same build_llm() as the
outline sub-agent so model/key configuration is centralised.
"""

import os

# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage

from src.agents.models import ChapterBeats, Outline
from src.agents.prompts import (
    BEATS_PROMPT,
    STYLE_GUIDE,
    fmt_outline,
    fmt_prior_summaries,
    fmt_revelations,
)


def _build_llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version="2024-08-01-preview",
        temperature=0.7,
        top_p=0.9,
    )


def run_beats_agent(
    chapter_number: int,
    outline: Outline,
    chapters: dict,
    character_revelations: dict,
) -> ChapterBeats:
    prompt_text = BEATS_PROMPT.format(
        style_guide=STYLE_GUIDE,
        chapter_number=chapter_number,
        outline=fmt_outline(outline),
        prior_summaries=fmt_prior_summaries(chapters),
        character_revelations=fmt_revelations(character_revelations),
    )

    llm = _build_llm().with_structured_output(ChapterBeats, method="json_mode")
    result = llm.invoke([HumanMessage(content=prompt_text)])
    return result
