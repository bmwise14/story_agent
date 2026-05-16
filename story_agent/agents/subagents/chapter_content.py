"""
Chapter content sub-agent — STUB.

Receives real assembled context (beats + prior summaries + revelations)
but returns hardcoded shape-correct Chapter text instead of making an LLM call.
The plumbing is real; the output is canned.

When this becomes functional, replace the return statement with an LLM call
using CONTENT_PROMPT. Note: content generation does NOT use with_structured_output
because the output is free-form prose, not a JSON schema.
"""

from story_agent.agents.models import Chapter, ChapterBeats, Outline
from story_agent.agents.prompts import (
    CONTENT_PROMPT,
    STYLE_GUIDE,
    fmt_outline,
    fmt_prior_summaries,
    fmt_revelations,
)


def run_content_agent(
    chapter_number: int,
    beats: ChapterBeats,
    outline: Outline,
    chapters: dict,
    character_revelations: dict,
    approx_words: int = 800,
) -> Chapter:
    """
    Assemble the full context prompt (so the plumbing is real),
    then return a stub Chapter for the requested chapter.
    """
    beats_text = "\n".join(f"- {b}" for b in beats.beats)

    # Build the prompt exactly as the real agent would — context assembly is real.
    _prompt_text = CONTENT_PROMPT.format(
        style_guide=STYLE_GUIDE,
        chapter_number=chapter_number,
        approx_words=approx_words,
        outline=fmt_outline(outline),
        beats=beats_text,
        prior_summaries=fmt_prior_summaries(chapters),
        character_revelations=fmt_revelations(character_revelations),
    )

    # STUB: return shape-correct placeholder instead of calling the LLM.
    stub_text = (
        f"[STUB CHAPTER {chapter_number}]\n\n"
        f"The house was the same and entirely wrong. Claire stood in the doorway "
        f"long enough for the light to shift, watching dust move through the air "
        f"above her mother's chair — still angled toward the window, still waiting "
        f"for someone to sit in it who never would again.\n\n"
        f"She found the first note on page {chapter_number * 7} of the journal "
        f"she wasn't supposed to read. She read it twice. The second time her "
        f"hands were steadier, which felt like the wrong response.\n\n"
        f"Emotional arc: {beats.emotional_arc}"
    )

    return Chapter(
        chapter_number=chapter_number,
        text=stub_text,
    )
