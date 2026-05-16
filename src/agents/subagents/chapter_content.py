"""
Chapter content sub-agent.

Calls GPT-4o to generate free-form prose for the requested chapter.
Does not use with_structured_output — the output is raw narrative text,
not a JSON schema.
"""

import os

# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage

from src.agents.models import Chapter, ChapterBeats, Outline
from src.agents.prompts import (
    CONTENT_PROMPT,
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
        temperature=0.85,
        top_p=0.95,
    )


def run_content_agent(
    chapter_number: int,
    beats: ChapterBeats,
    outline: Outline,
    chapters: dict,
    character_revelations: dict,
    approx_words: int = 800,
) -> Chapter:
    beats_text = "\n".join(f"- {b}" for b in beats.beats)

    prompt_text = CONTENT_PROMPT.format(
        style_guide=STYLE_GUIDE,
        chapter_number=chapter_number,
        approx_words=approx_words,
        outline=fmt_outline(outline),
        beats=beats_text,
        prior_summaries=fmt_prior_summaries(chapters),
        character_revelations=fmt_revelations(character_revelations),
    )

    llm = _build_llm()
    response = llm.invoke([HumanMessage(content=prompt_text)])
    return Chapter(chapter_number=chapter_number, text=response.content)
