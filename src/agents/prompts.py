"""
Prompt templates for the Story Agent.

Every prompt is prefixed with the STYLE_GUIDE to enforce the
psychological / personal-journey genre constraint across all nodes.
"""

STYLE_GUIDE = """\
STYLE CONSTRAINT — follow this without exception:
Stories in the tradition of Gone Home, Firewatch, and Disco Elysium's quieter moments.
Focus on character interiority, slow-burning emotional arcs, and discovery through
environment and memory rather than action. Threats are psychological, not physical.
Resolution is internal — a shift in understanding, a reframed memory, a choice that
costs something — not external victory. Prose is restrained, sensory, and grounded.
The reader follows a character finding pieces of themselves in a place or a relationship
they thought they knew.
"""

# ---------------------------------------------------------------------------
# Outline
# ---------------------------------------------------------------------------

OUTLINE_PROMPT = """\
{style_guide}

You are an expert story architect. Given the story configuration below, produce a
complete story outline in JSON matching this exact schema:

{{
  "logline": "<one compelling sentence describing the full story arc>",
  "act_breakdown": [
    "<chapter 1: ~2 sentences describing what happens and why it matters emotionally>",
    ... one entry per chapter ...
  ],
  "character_arcs": {{
    "<character name>": "<one paragraph describing their full arc across the story>"
  }}
}}

Return ONLY valid JSON. No markdown fences, no commentary.

STORY CONFIGURATION:
Premise: {premise}
Chapter count: {chapter_count}
Words per chapter (approx): {approx_words_per_chapter}
Themes: {themes}
Characters:
{characters}
"""

# ---------------------------------------------------------------------------
# Chapter beats
# ---------------------------------------------------------------------------

BEATS_PROMPT = """\
{style_guide}

You are a story structure editor. Given the outline and prior story context below,
produce the chapter beats for chapter {chapter_number} in JSON matching this schema:

{{
  "chapter_number": {chapter_number},
  "beats": [
    "<beat 1 — what happens, what it reveals or costs>",
    "<beat 2>",
    "<beat 3>"
    ... 3 to 5 beats total ...
  ],
  "emotional_arc": "<one sentence: where the protagonist starts emotionally and where they end>"
}}

Return ONLY valid JSON. No markdown fences, no commentary.

STORY OUTLINE:
{outline}

PRIOR CHAPTER SUMMARIES:
{prior_summaries}

ACCUMULATED CHARACTER REVELATIONS:
{character_revelations}
"""

# ---------------------------------------------------------------------------
# Chapter content
# ---------------------------------------------------------------------------

CONTENT_PROMPT = """\
{style_guide}

You are a literary fiction author. Write chapter {chapter_number} of the story.
Target length: approximately {approx_words} words.

Use the beats below as your structural backbone — hit each one, but let the prose
breathe. Stay true to the style constraint above: interior, restrained, sensory.

Return ONLY the chapter prose. No titles, no headings, no commentary.

STORY OUTLINE:
{outline}

CHAPTER {chapter_number} BEATS:
{beats}

PRIOR CHAPTER SUMMARIES:
{prior_summaries}

ACCUMULATED CHARACTER REVELATIONS:
{character_revelations}
"""

# ---------------------------------------------------------------------------
# Safety / style check
# ---------------------------------------------------------------------------

CHECK_PROMPT = """\
You are a strict editorial gatekeeper for a psychological / personal-journey story series.

Read the chapter excerpt below and determine whether it violates any of these rules:
1. No action-pulpy or gratuitously violent scenes.
2. No melodramatic or overwrought emotional dialogue.
3. No external "victory" resolution — resolution must be internal.
4. Prose must be restrained and grounded, not purple or florid.

Respond with ONLY one of these two JSON objects — nothing else:

{{"verdict": "pass", "reason": "<one sentence>"}}
{{"verdict": "fail", "reason": "<one sentence explaining what violated the style guide>"}}

CHAPTER EXCERPT:
{chapter_text}
"""

# ---------------------------------------------------------------------------
# Summarize chapter + extract revelations
# ---------------------------------------------------------------------------

SUMMARIZE_PROMPT = """\
You are a careful story analyst. Read the chapter below and produce a JSON response
matching this exact schema:

{{
  "summary": "<3 sentences: what happened, what it cost emotionally, what changed>",
  "new_revelations": [
    {{
      "chapter_number": {chapter_number},
      "character_name": "<must exactly match one of: {character_names}>",
      "what_was_revealed": "<one sentence: what the reader now knows that they didn't before>",
      "kind": "<one of: fact, feeling, relationship, memory, secret>"
    }}
    ... include only genuine new revelations; omit if none ...
  ]
}}

Return ONLY valid JSON. No markdown fences, no commentary.

CHAPTER {chapter_number}:
{chapter_text}
"""


def fmt_characters(characters: list) -> str:
    """Format a list of Character objects into a readable prompt block."""
    lines = []
    for c in characters:
        lines.append(f"- {c.name} ({c.archetype}): {c.short_description}")
    return "\n".join(lines)


def fmt_outline(outline) -> str:
    """Format an Outline object into a readable prompt block."""
    lines = [f"Logline: {outline.logline}", "", "Act breakdown:"]
    for i, act in enumerate(outline.act_breakdown, 1):
        lines.append(f"  Chapter {i}: {act}")
    lines.append("")
    lines.append("Character arcs:")
    for name, arc in outline.character_arcs.items():
        lines.append(f"  {name}: {arc}")
    return "\n".join(lines)


def fmt_prior_summaries(chapters: dict) -> str:
    """Format completed chapter summaries into a prompt block."""
    if not chapters:
        return "None yet."
    lines = []
    for num in sorted(chapters.keys()):
        ch = chapters[num]
        summary = ch.summary or "(no summary)"
        lines.append(f"Chapter {num}: {summary}")
    return "\n".join(lines)


def fmt_revelations(character_revelations: dict) -> str:
    """Format accumulated character revelations into a prompt block."""
    if not character_revelations:
        return "None yet."
    lines = []
    for char_name, revs in character_revelations.items():
        lines.append(f"{char_name}:")
        for r in revs:
            lines.append(f"  [Ch.{r.chapter_number} / {r.kind}] {r.what_was_revealed}")
    return "\n".join(lines)
