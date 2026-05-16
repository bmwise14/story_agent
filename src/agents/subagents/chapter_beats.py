"""
Chapter beats sub-agent — STUB.

Receives real assembled context (outline + prior summaries + revelations)
but returns hardcoded shape-correct ChapterBeats instead of making an LLM call.
The plumbing is real; the output is canned.

When this becomes functional, replace the return statement with:
    llm = build_llm().with_structured_output(ChapterBeats)
    return llm.invoke([HumanMessage(content=prompt_text)])
"""

from src.agents.models import ChapterBeats, Outline
from src.agents.prompts import (
    BEATS_PROMPT,
    STYLE_GUIDE,
    fmt_outline,
    fmt_prior_summaries,
    fmt_revelations,
)


def run_beats_agent(
    chapter_number: int,
    outline: Outline,
    chapters: dict,
    character_revelations: dict,
) -> ChapterBeats:
    """
    Assemble the full context prompt (so the plumbing is real),
    then return a stub ChapterBeats for the requested chapter.
    """
    # Build the prompt exactly as the real agent would — context assembly is real.
    _prompt_text = BEATS_PROMPT.format(
        style_guide=STYLE_GUIDE,
        chapter_number=chapter_number,
        outline=fmt_outline(outline),
        prior_summaries=fmt_prior_summaries(chapters),
        character_revelations=fmt_revelations(character_revelations),
    )

    # STUB: return shape-correct placeholder instead of calling the LLM.
    return ChapterBeats(
        chapter_number=chapter_number,
        beats=[
            f"[STUB] Chapter {chapter_number} — the protagonist arrives at a threshold they cannot cross without cost.",
            f"[STUB] An object or space triggers a memory that reframes everything before it.",
            f"[STUB] A letter, recording, or absence confirms what the protagonist feared but could not name.",
        ],
        emotional_arc=(
            f"[STUB] Chapter {chapter_number} opens in quiet dread and closes in the specific grief of understanding."
        ),
    )
