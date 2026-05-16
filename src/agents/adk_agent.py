"""
ADK orchestrator for the Story Agent.

Architecture (mirrors the LangGraph version conceptually):

    Runner
      └── SequentialAgent  (story_orchestrator)
            ├── OutlineAgent        custom BaseAgent — functional, skipped on resume
            └── ChapterLoopAgent    LoopAgent — terminates when all chapters done
                  └── ChapterPipelineAgent   SequentialAgent per chapter
                        ├── BeatsAgent       LlmAgent — STUB instructions
                        ├── ContentAgent     LlmAgent — STUB instructions
                        ├── CheckAgent       custom BaseAgent — deterministic, temp 0.0
                        └── SummarizeAgent   custom BaseAgent — functional, temp 0.2

State lives in ADK session.state (a plain dict).
DatabaseSessionService persists it to Postgres using a different table schema
than LangGraph's PostgresSaver — but the same physical database.

Translation map vs LangGraph:
  StateGraph + TypedDict    → SequentialAgent + LoopAgent + session.state dict
  Conditional edges         → LoopAgent termination via session.state flag
  PostgresSaver             → DatabaseSessionService
  thread_id                 → session_id
  Node function             → BaseAgent subclass or LlmAgent
"""

import argparse
import asyncio
import json
import os
from typing import AsyncGenerator

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import LlmAgent, SequentialAgent, LoopAgent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai.types import GenerateContentConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel as PydanticBaseModel
from typing import Literal
from google.genai.types import Content, Part

from src.agents.models import StoryConfig, Outline, ChapterBeats, Chapter, Revelation
from src.agents.prompts import (
    CHECK_PROMPT,
    SUMMARIZE_PROMPT,
    fmt_outline,
    fmt_prior_summaries,
    fmt_revelations,
)
from src.agents.subagents.outline import run_outline_agent
from src.agents.subagents.chapter_beats import run_beats_agent
from src.agents.subagents.chapter_content import run_content_agent

MAX_RETRIES = 3
APP_NAME = "story_agent"


def _dump_adk_state(state: dict) -> None:
    """Write full ADK session state to data/adk_state.json for comparison."""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    out_path = os.path.join(repo_root, "data", "adk_state.json")
    with open(out_path, "w") as f:
        json.dump(state, f, indent=2, default=str)
    print(f"  [state] written to data/adk_state.json")


# ---------------------------------------------------------------------------
# Internal result models (same as LangGraph version)
# ---------------------------------------------------------------------------

class CheckResult(PydanticBaseModel):
    verdict: Literal["pass", "fail"]
    reason: str


class SummarizeResult(PydanticBaseModel):
    summary: str
    new_revelations: list[Revelation]


# ---------------------------------------------------------------------------
# LLM factory — identical to LangGraph version, same decoding params
# ---------------------------------------------------------------------------

def _llm(temperature: float, top_p: float) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        google_api_key=os.environ["GOOGLE_API_KEY"],
        temperature=temperature,
        top_p=top_p,
    )


# ---------------------------------------------------------------------------
# Helper: deserialize StoryConfig from session state
# ---------------------------------------------------------------------------

def _get_config(session_state: dict) -> StoryConfig:
    cfg = session_state.get("config")
    if isinstance(cfg, dict):
        return StoryConfig(**cfg)
    return cfg


def _get_outline(session_state: dict):
    outline = session_state.get("outline")
    if isinstance(outline, dict):
        return Outline(**outline)
    return outline


# ---------------------------------------------------------------------------
# Custom agents (BaseAgent subclasses)
# ---------------------------------------------------------------------------

class OutlineAgent(BaseAgent):
    """
    Functional — calls Gemini via run_outline_agent, stores result in session.state.
    Skipped on resume if outline already present (idempotent, same as LangGraph).

    ADK contrast: in LangGraph this is a node function with a conditional skip.
    Here we implement the skip inside _run_async_impl using session state.
    """

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state

        if state.get("outline") is not None:
            print("  [ADK outline] already in state — skipping (resumed)")
            return

        print("  [ADK outline] calling Gemini...")
        config = _get_config(state)
        outline = run_outline_agent(config)
        print(f"  [ADK outline] done — {outline.logline[:80]}...")

        # Must use state_delta to persist to DatabaseSessionService —
        # direct dict mutation is in-memory only.
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            actions=EventActions(state_delta={"outline": outline.model_dump()}),
        )


class CheckAndSummarizeAgent(BaseAgent):
    """
    Runs safety check (temp 0.0) then, on pass, summarizes the chapter (temp 0.2)
    and extracts character revelations.

    ADK contrast: in LangGraph these are two separate nodes connected by a
    conditional edge. Here we combine them in one custom agent and set a
    session state flag to signal the LoopAgent whether to retry or continue.
    """

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        ch = state.get("current_chapter", 1)
        retry_count = state.get("retry_count", 0)
        chapters = state.get("chapters", {})
        chapter_text = chapters.get(str(ch), {}).get("text", "")

        # --- Safety check (temp 0.0 — deterministic) ---
        check_llm = _llm(temperature=0.0, top_p=1.0).with_structured_output(CheckResult)
        prompt = CHECK_PROMPT.format(chapter_text=chapter_text)
        result: CheckResult = check_llm.invoke([HumanMessage(content=prompt)])
        print(f"  [ADK check] chapter {ch} → {result.verdict} | {result.reason}")

        if result.verdict == "fail":
            new_retry = retry_count + 1
            force_pass = new_retry >= MAX_RETRIES
            if force_pass:
                print(f"  [ADK check] max retries hit — force-advancing chapter {ch}")
            else:
                print(f"  [ADK check] retry {new_retry}/{MAX_RETRIES}")
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                actions=EventActions(state_delta={
                    "retry_count": new_retry,
                    "chapter_passed": force_pass,
                }),
            )
            return

        # --- Summarize (temp 0.2 — near-deterministic) ---
        config = _get_config(state)
        character_names = ", ".join(c.name for c in config.characters)
        summarize_llm = _llm(temperature=0.2, top_p=0.9).with_structured_output(SummarizeResult)
        s_prompt = SUMMARIZE_PROMPT.format(
            chapter_number=ch,
            chapter_text=chapter_text,
            character_names=character_names,
        )
        s_result: SummarizeResult = summarize_llm.invoke([HumanMessage(content=s_prompt)])
        print(f"  [ADK summarize] {len(s_result.new_revelations)} revelation(s) extracted")

        # Build updated state — use state_delta so DatabaseSessionService persists it.
        # Direct dict mutation is in-memory only; state_delta is what gets committed.
        chapters[str(ch)]["summary"] = s_result.summary
        revs = state.get("character_revelations", {})
        for rev in s_result.new_revelations:
            revs.setdefault(rev.character_name, []).append(rev.model_dump())

        next_ch = ch + 1
        total = config.chapter_count
        story_complete = next_ch > total
        print(f"  [ADK] chapter {ch} done — {'story complete' if story_complete else f'moving to chapter {next_ch}'}")

        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            actions=EventActions(state_delta={
                "chapters": chapters,
                "character_revelations": revs,
                "current_chapter": next_ch,
                "retry_count": 0,
                "chapter_passed": True,
                "story_complete": story_complete,
            }),
        )


class BeatsAndContentAgent(BaseAgent):
    """
    Runs beats stub then content stub for the current chapter.
    Real plumbing (context assembly) is preserved — stubs return canned output.
    """

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        ch = state.get("current_chapter", 1)
        outline = _get_outline(state)
        chapters_raw = state.get("chapters", {})
        revs = state.get("character_revelations", {})

        # Deserialize chapters dict for context
        chapters = {int(k): Chapter(**v) for k, v in chapters_raw.items()}

        # Deserialize revelations from dicts back to Revelation objects
        revs_obj = {
            name: [Revelation(**r) if isinstance(r, dict) else r for r in rev_list]
            for name, rev_list in revs.items()
        }

        print(f"  [ADK beats] generating for chapter {ch}")
        beats = run_beats_agent(
            chapter_number=ch,
            outline=outline,
            chapters=chapters,
            character_revelations=revs_obj,
        )

        print(f"  [ADK content] generating chapter {ch}")
        config = _get_config(state)
        chapter = run_content_agent(
            chapter_number=ch,
            beats=beats,
            outline=outline,
            chapters=chapters,
            character_revelations=revs_obj,
            approx_words=config.approx_words_per_chapter,
        )

        chapters_raw[str(ch)] = chapter.model_dump()

        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            actions=EventActions(state_delta={
                "chapters": chapters_raw,
                "chapter_passed": False,
            }),
        )


# ---------------------------------------------------------------------------
# Loop termination agent
# ---------------------------------------------------------------------------

class StoryCompleteChecker(BaseAgent):
    """
    Signals the LoopAgent to stop when all chapters are complete.

    ADK contrast with LangGraph: LangGraph uses a conditional edge from
    increment_chapter that routes to END or back to generate_chapter_beats.
    ADK's LoopAgent has no conditional routing — termination requires yielding
    an Event with actions.escalate=True from within a sub-agent.
    """

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        if ctx.session.state.get("story_complete", False):
            print("  [ADK] story complete — escalating to stop loop")
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                actions=EventActions(escalate=True),
            )


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_adk_agent() -> SequentialAgent:
    """
    Build the ADK agent tree.

    ADK contrast with LangGraph:
      - No explicit state schema (TypedDict) — session.state is a plain dict
      - SequentialAgent + LoopAgent replace StateGraph + conditional edges
      - Loop termination is config-driven (max_iterations) rather than via
        conditional edges from a routing function
    """
    chapter_pipeline = SequentialAgent(
        name="chapter_pipeline",
        sub_agents=[
            BeatsAndContentAgent(name="beats_and_content"),
            CheckAndSummarizeAgent(name="check_and_summarize"),
            StoryCompleteChecker(name="story_complete_checker"),
        ],
    )

    chapter_loop = LoopAgent(
        name="chapter_loop",
        sub_agents=[chapter_pipeline],
        max_iterations=60,  # chapter_count × MAX_RETRIES upper bound
    )

    return SequentialAgent(
        name="story_orchestrator",
        sub_agents=[
            OutlineAgent(name="outline"),
            chapter_loop,
        ],
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_adk_agent(config: StoryConfig, session_id: str, db_uri: str) -> None:
    """
    ADK contrast:
      - Resume is via session_id + DatabaseSessionService (vs thread_id + PostgresSaver)
      - Session service manages its own Postgres tables (different schema than LangGraph)
      - No explicit checkpointer.setup() — DatabaseSessionService handles schema
    """
    try:
        session_service = DatabaseSessionService(db_url=db_uri)
        print(f"  [ADK] using DatabaseSessionService → {db_uri.split('@')[-1]}")
    except Exception as e:
        print(f"  [ADK] DatabaseSessionService failed ({e}) — falling back to InMemorySessionService")
        session_service = InMemorySessionService()

    agent = build_adk_agent()
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    # Resume or create session
    initial_state = {
        "config": config.model_dump(),
        "outline": None,
        "chapters": {},
        "character_revelations": {},
        "current_chapter": 1,
        "retry_count": 0,
        "chapter_passed": False,
        "story_complete": False,
    }

    existing = await session_service.get_session(
        app_name=APP_NAME, user_id="user", session_id=session_id
    )

    if existing and existing.state.get("config"):
        ch_done = len(existing.state.get("chapters", {}))
        ch_current = existing.state.get("current_chapter", 1)
        print(f"Resuming ADK session '{session_id}' | current_chapter={ch_current} | {ch_done} chapter(s) completed")
        session = existing
    else:
        print(f"Starting new ADK story | session: {session_id}")
        if existing:
            # Session exists but is empty — delete and recreate cleanly
            await session_service.delete_session(
                app_name=APP_NAME, user_id="user", session_id=session_id
            )
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id="user",
            session_id=session_id,
            state=initial_state,
        )


    async for event in runner.run_async(
        user_id="user",
        session_id=session_id,
        new_message=Content(parts=[Part(text="generate")]),
    ):
        pass  # progress printed inside each agent

    # Re-fetch session to get the final persisted state
    await asyncio.sleep(0.5)
    final = await session_service.get_session(
        app_name=APP_NAME, user_id="user", session_id=session_id
    )
    chapters_done = len(final.state.get("chapters", {})) if final else 0
    print(f"\nDone. {chapters_done} chapter(s) complete for ADK session '{session_id}'.")

    if final:
        _dump_adk_state(final.state)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Story Agent — ADK orchestrator")
    parser.add_argument("--config", required=True, help="Path to StoryConfig JSON")
    parser.add_argument("--session-id", required=True, help="Session ID for persistence / resume")
    args = parser.parse_args()

    with open(args.config) as f:
        config = StoryConfig(**json.load(f))

    # ADK's DatabaseSessionService uses SQLAlchemy URLs — note psycopg driver suffix
    db_uri = (
        f"postgresql+psycopg://{os.environ['DB_USER']}:{os.environ.get('DB_PASSWORD', '')}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
    )

    asyncio.run(run_adk_agent(config=config, session_id=args.session_id, db_uri=db_uri))


if __name__ == "__main__":
    main()
