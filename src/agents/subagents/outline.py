"""
Outline sub-agent — functional.

Makes a real GPT-4o call and returns a parsed Outline Pydantic model.
Uses with_structured_output to enforce the schema and avoid JSON parsing errors.
Temperature 0.6 / top_p 0.9 — moderate creativity, structure over novelty.
"""

import os

# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage

from src.agents.models import Outline, StoryConfig
from src.agents.prompts import (
    OUTLINE_PROMPT,
    STYLE_GUIDE,
    fmt_characters,
)


def build_llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version="2024-08-01-preview",
        temperature=0.6,
        top_p=0.9,
    )


def run_outline_agent(config: StoryConfig) -> Outline:
    """
    Call the LLM with the outline prompt. with_structured_output enforces
    the Outline schema so field names are always correct.
    """
    llm = build_llm().with_structured_output(Outline, method="json_mode")

    prompt_text = OUTLINE_PROMPT.format(
        style_guide=STYLE_GUIDE,
        premise=config.premise,
        chapter_count=config.chapter_count,
        approx_words_per_chapter=config.approx_words_per_chapter,
        themes=", ".join(config.themes),
        characters=fmt_characters(config.characters),
    )

    return llm.invoke([HumanMessage(content=prompt_text)])
