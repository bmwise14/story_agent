"""
LLM-as-judge — picks the best of N story variants.

Bias mitigations:
  1. Randomize variant order in the prompt (kills position bias)
  2. Anonymize variant IDs from the judge (it scores by position, not ID)
  3. Use a different model than the generator (Flash judges Pro output)
  4. Single structured call with explicit JSON schema

Interview line: "I use Gemini Flash as the judge, not the same model that
generated the variants. Using the same model would create self-preference bias —
a model tends to rate outputs that match its own style more highly. Different
model, different temperature (0.0 for determinism), randomized order to kill
position bias."
"""

import json
import os
import random

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

load_dotenv()

JUDGE_MODEL = "gemini-2.5-flash"

RUBRIC = """
You are evaluating opening chapters for a gaming story campaign. Score the variants and pick the best one.

Rubric:
1. Engaging opening — does it hook the reader in the first paragraph?
2. Internal consistency — no contradictions or logic gaps
3. Sets up at least 2 plot threads for future chapters
4. Tone — appropriate for a psychological / personal-journey narrative (Gone Home style)

You will receive the variants in random order labeled A, B, C.
Return JSON only: { "winner_label": "A" | "B" | "C", "reasoning": "<one sentence>" }
"""


class JudgeResult(BaseModel):
    winner_label: str
    reasoning: str


def pick_winner(variants: dict[int, str]) -> tuple[int, str]:
    """
    Given a dict of { variant_id: chapter_text }, return (winning_variant_id, reasoning).

    Args:
        variants: dict mapping int variant_id (0, 1, 2) to generated chapter text

    Returns:
        Tuple of (winning variant_id, judge's one-sentence reasoning)
    """
    if len(variants) == 1:
        only_id = next(iter(variants))
        return only_id, "Only one variant available."

    # Shuffle to kill position bias
    items = list(variants.items())
    random.shuffle(items)
    labels = ["A", "B", "C"]

    # Build prompt with anonymized, shuffled variants
    variant_text = "\n\n".join(
        f"--- Variant {labels[i]} ---\n{text}"
        for i, (_, text) in enumerate(items)
    )

    llm = ChatGoogleGenerativeAI(
        model=JUDGE_MODEL,
        google_api_key=os.environ["GOOGLE_API_KEY"],
        temperature=0.0,  # deterministic verdict
    )

    prompt = f"{RUBRIC}\n\n{variant_text}\n\nReturn JSON only."
    response = llm.invoke([HumanMessage(content=prompt)])

    # Parse JSON from response
    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = JudgeResult(**json.loads(raw.strip()))

    # Map label back to original variant_id
    label_index = labels.index(result.winner_label.upper())
    winning_id = items[label_index][0]

    print(f"  [judge] winner: variant {winning_id} (label {result.winner_label}) — {result.reasoning}")
    return winning_id, result.reasoning
