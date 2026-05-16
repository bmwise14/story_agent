"""
LangGraph orchestrator for the Story Agent.

Graph shape:
    START → make_outline → generate_chapter_beats → generate_chapter_content
          → check_content → (pass)        → summarize_chapter → increment_chapter
                                                               → [done] END
                                                               → [continue] generate_chapter_beats
                          → (fail)        → increment_retry → generate_chapter_content
                          → (max_retries) → END
"""

import argparse
import json
import os
from typing import Annotated, Optional, Literal, TypedDict

from dotenv import load_dotenv

load_dotenv()

# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from src.agents.models import (
    StoryConfig,
    Outline,
    ChapterBeats,
    Chapter,
    Revelation,
)
from src.agents.prompts import (
    CHECK_PROMPT,
    SUMMARIZE_PROMPT,
)
from src.agents.subagents.outline import run_outline_agent
from src.agents.subagents.chapter_beats import run_beats_agent
from src.agents.subagents.chapter_content import run_content_agent

MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# State reducers
# ---------------------------------------------------------------------------

def _merge_dicts(existing: dict, update: dict) -> dict:
    """Merge update into existing without overwriting prior chapter entries."""
    return {**existing, **update}


def _append_revs(existing: dict, update: dict) -> dict:
    """Extend per-character revelation lists across chapters."""
    merged = {k: list(v) for k, v in existing.items()}
    for char, revs in update.items():
        merged.setdefault(char, []).extend(revs)
    return merged


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class StoryState(TypedDict):
    thread_id: str
    config: StoryConfig
    outline: Optional[Outline]
    chapter_beats: Annotated[dict, _merge_dicts]          # int → ChapterBeats
    chapters: Annotated[dict, _merge_dicts]               # int → Chapter
    character_revelations: Annotated[dict, _append_revs]  # str → list[Revelation]
    current_chapter: int
    retry_count: int
    last_check_verdict: str


# ---------------------------------------------------------------------------
# Internal result models
# ---------------------------------------------------------------------------

class CheckResult(BaseModel):
    verdict: Literal["pass", "fail"]
    reason: str


class SummarizeResult(BaseModel):
    summary: str
    new_revelations: list[Revelation]


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class StoryAgent:
    """
    Orchestrates story generation via a LangGraph StateGraph.

    Nodes are instance methods so they share self._llm() and MAX_RETRIES.
    self.graph is the compiled app — invoke it directly or stream it.
    """

    def __init__(self, db_uri: str):
        self._pool = ConnectionPool(
            conninfo=db_uri,
            max_size=5,
            open=True,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        checkpointer = PostgresSaver(self._pool)
        checkpointer.setup()

        graph = StateGraph(StoryState)

        # --- Register nodes ---
        graph.add_node("make_outline", self.make_outline)
        graph.add_node("generate_chapter_beats", self.generate_chapter_beats)
        graph.add_node("generate_chapter_content", self.generate_chapter_content)
        graph.add_node("check_content", self.check_content)
        graph.add_node("increment_retry", self.increment_retry)
        graph.add_node("summarize_chapter", self.summarize_chapter)
        graph.add_node("increment_chapter", self.increment_chapter)

        # --- Wire edges ---
        graph.add_edge(START, "make_outline")
        graph.add_edge("make_outline", "generate_chapter_beats")
        graph.add_edge("generate_chapter_beats", "generate_chapter_content")
        graph.add_edge("generate_chapter_content", "check_content")
        graph.add_conditional_edges("check_content", self.route_check)
        graph.add_edge("increment_retry", "generate_chapter_content")
        graph.add_edge("summarize_chapter", "increment_chapter")
        graph.add_conditional_edges("increment_chapter", self.route_chapter)

        self.graph = graph.compile(checkpointer=checkpointer)

        # Write Mermaid diagram on startup
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        mermaid_path = os.path.join(repo_root, "data", "graph.mmd")
        with open(mermaid_path, "w") as f:
            f.write(self.graph.get_graph().draw_mermaid())
        print(f"  [graph] Mermaid diagram written to {mermaid_path}")
        print(self.graph.get_graph().draw_mermaid())

    # -----------------------------------------------------------------------
    # LLM factory — each node calls this with its own decoding parameters
    # -----------------------------------------------------------------------

    def _llm(self, temperature: float, top_p: float) -> AzureChatOpenAI:
        return AzureChatOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version="2024-08-01-preview",
            temperature=temperature,
            top_p=top_p,
        )

    # -----------------------------------------------------------------------
    # Nodes
    # -----------------------------------------------------------------------

    def make_outline(self, state: StoryState) -> dict:
        """Runs once per thread. Skipped on resume if outline already in state."""
        if state.get("outline") is not None:
            print("  [outline] already in state — skipping (resumed)")
            return {}
        print("  [outline] calling LLM...")
        outline = run_outline_agent(state["config"])
        print(f"  [outline] done — {outline.logline[:80]}...")
        return {"outline": outline}

    def generate_chapter_beats(self, state: StoryState) -> dict:
        ch = state["current_chapter"]
        print(f"  [beats] generating for chapter {ch}")
        beats = run_beats_agent(
            chapter_number=ch,
            outline=state["outline"],
            chapters=state.get("chapters", {}),
            character_revelations=state.get("character_revelations", {}),
        )
        return {"chapter_beats": {ch: beats}}

    def generate_chapter_content(self, state: StoryState) -> dict:
        ch = state["current_chapter"]
        retry = state.get("retry_count", 0)
        suffix = f" (retry {retry})" if retry > 0 else ""
        print(f"  [content] generating chapter {ch}{suffix}")
        beats = state["chapter_beats"][ch]
        chapter = run_content_agent(
            chapter_number=ch,
            beats=beats,
            outline=state["outline"],
            chapters=state.get("chapters", {}),
            character_revelations=state.get("character_revelations", {}),
            approx_words=state["config"].approx_words_per_chapter,
        )
        return {"chapters": {ch: chapter}}

    def check_content(self, state: StoryState) -> dict:
        """
        Deterministic style/safety check.
        temperature=0.0 so the same input always produces the same verdict.
        Routes to max_retries when the retry budget is exhausted.
        """
        ch = state["current_chapter"]
        chapter_text = state["chapters"][ch].text
        retry_count = state.get("retry_count", 0)

        llm = self._llm(temperature=0.0, top_p=1.0).with_structured_output(CheckResult)
        prompt = CHECK_PROMPT.format(chapter_text=chapter_text)
        result: CheckResult = llm.invoke([HumanMessage(content=prompt)])

        print(f"  [check] chapter {ch} → {result.verdict} | {result.reason}")

        if result.verdict == "fail" and retry_count >= MAX_RETRIES - 1:
            return {"last_check_verdict": "max_retries"}

        return {"last_check_verdict": result.verdict}

    def increment_retry(self, state: StoryState) -> dict:
        new_count = state.get("retry_count", 0) + 1
        print(f"  [retry] retry_count → {new_count}")
        return {"retry_count": new_count}

    def summarize_chapter(self, state: StoryState) -> dict:
        """
        Double-duty functional node:
        - 3-sentence chapter summary
        - Extracts new character revelations
        temperature=0.2 — near-deterministic, faithful to source text.
        """
        ch = state["current_chapter"]
        chapter_text = state["chapters"][ch].text
        config: StoryConfig = state["config"]
        character_names = ", ".join(c.name for c in config.characters)

        print(f"  [summarize] chapter {ch}...")
        llm = self._llm(temperature=0.2, top_p=0.9).with_structured_output(SummarizeResult)
        prompt = SUMMARIZE_PROMPT.format(
            chapter_number=ch,
            chapter_text=chapter_text,
            character_names=character_names,
        )
        result: SummarizeResult = llm.invoke([HumanMessage(content=prompt)])
        print(f"  [summarize] summary written | {len(result.new_revelations)} revelation(s) extracted")

        # Reducer merges these into the existing dicts/lists automatically
        updated_chapter = state["chapters"][ch].model_copy(update={"summary": result.summary})
        new_revs = {rev.character_name: [rev] for rev in result.new_revelations}

        return {
            "chapters": {ch: updated_chapter},
            "character_revelations": new_revs,
        }

    def increment_chapter(self, state: StoryState) -> dict:
        next_ch = state["current_chapter"] + 1
        print(f"  [chapter] complete — moving to chapter {next_ch}")
        return {"current_chapter": next_ch, "retry_count": 0}

    # -----------------------------------------------------------------------
    # Routing
    # -----------------------------------------------------------------------

    def route_check(self, state: StoryState) -> str:
        verdict = state.get("last_check_verdict", "fail")
        if verdict == "pass":
            return "summarize_chapter"
        elif verdict == "max_retries":
            return END
        else:
            return "increment_retry"

    def route_chapter(self, state: StoryState) -> str:
        if state["current_chapter"] > state["config"].chapter_count:
            return END
        return "generate_chapter_beats"

    # -----------------------------------------------------------------------
    # Run helper
    # -----------------------------------------------------------------------

    def run(self, config: StoryConfig, thread_id: str, max_attempts: int = 3) -> None:
        for attempt in range(max_attempts):
            try:
                self._run_once(config, thread_id)
                return
            except Exception as e:
                if attempt == max_attempts - 1:
                    raise
                print(f"\n  [retry] attempt {attempt + 1}/{max_attempts} failed: {e!r}")
                print(f"  [retry] resuming from last checkpoint...")

    def _run_once(self, config: StoryConfig, thread_id: str) -> None:
        thread_cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}

        existing = self.graph.get_state(thread_cfg)
        if existing.values:
            current_ch = existing.values.get("current_chapter", 1)
            chapters_done = len(existing.values.get("chapters", {}))
            print(f"Resuming thread '{thread_id}' | current_chapter={current_ch} | {chapters_done} chapter(s) completed")
            input_state = None
        else:
            print(f"Starting new story | thread: {thread_id}")
            input_state = StoryState(
                thread_id=thread_id,
                config=config,
                outline=None,
                chapter_beats={},
                chapters={},
                character_revelations={},
                current_chapter=1,
                retry_count=0,
                last_check_verdict="",
            )

        for _ in self.graph.stream(input_state, config=thread_cfg, stream_mode="updates"):
            pass  # Progress printed inside each node

        final = self.graph.get_state(thread_cfg)
        chapters_done = len(final.values.get("chapters", {}))
        print(f"\nDone. {chapters_done} chapter(s) complete for thread '{thread_id}'.")
        self._dump_state(final.values, thread_id)

    def _dump_state(self, state: dict, thread_id: str) -> None:
        """Write full state to data/langgraph_state.json for comparison."""
        def _serialize(obj):
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            if isinstance(obj, dict):
                return {k: _serialize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_serialize(i) for i in obj]
            return obj

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        out_path = os.path.join(repo_root, "data", "langgraph_state.json")
        with open(out_path, "w") as f:
            json.dump(_serialize(state), f, indent=2, default=str)
        print(f"  [state] written to data/langgraph_state.json")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Story Agent — LangGraph orchestrator")
    parser.add_argument("--config", required=True, help="Path to StoryConfig JSON")
    parser.add_argument("--thread-id", required=True, help="Thread ID for checkpointing / resume")
    args = parser.parse_args()

    with open(args.config) as f:
        config = StoryConfig(**json.load(f))

    db_uri = (
        f"postgresql://{os.environ['DB_USER']}:{os.environ.get('DB_PASSWORD', '')}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
    )

    agent = StoryAgent(db_uri=db_uri)
    try:
        agent.run(config=config, thread_id=args.thread_id)
    finally:
        agent._pool.close()


if __name__ == "__main__":
    main()
