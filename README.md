# Story Agent — Phase 1

A psychological / personal-journey story generation system built with **LangGraph** and **Google ADK**, backed by **Gemini** and **Postgres** state persistence.

## Project Structure

```
story_agent/
├── src/                          # all Python source
│   ├── agents/
│   │   ├── models.py             # Pydantic types: StoryConfig, Outline, Chapter, etc.
│   │   ├── prompts.py            # style guide + prompt templates
│   │   ├── langgraph_agent.py    # LangGraph orchestrator (StoryAgent class)
│   │   ├── adk_agent.py          # ADK orchestrator
│   │   ├── README_comparison.md  # LangGraph vs ADK side-by-side
│   │   └── subagents/
│   │       ├── outline.py        # functional — real Gemini call
│   │       ├── chapter_beats.py  # STUB
│   │       └── chapter_content.py# STUB
├── data/                         # generated artifacts
│   ├── graph.mmd                 # Mermaid source
│   └── graph.png                 # rendered graph image
├── examples/
│   └── gone_home_style.json      # example StoryConfig input
├── requirements.txt
└── .env.example
```

## Architecture

Orchestrator → Outline sub-agent → Chapter beats (stub) → Chapter content (stub) → Safety check → Summarizer (extracts chapter summary + character revelations) → loop

State persists to a local Postgres `game_stories` database via `PostgresSaver`. Resume is automatic — re-running with the same `--thread-id` continues from the last completed chapter.

## Setup

### 1. Python environment

```bash
mkvirtualenv story_agent
workon story_agent
pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
# Edit .env — add GOOGLE_API_KEY and verify DB credentials
```

### 3. Postgres

Ensure a local Postgres instance is running with a `game_stories` database:

```bash
createdb game_stories
psql "postgresql://${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}" -c "SELECT 1;"
```

`PostgresSaver.setup()` creates the checkpoint tables on first run automatically.

## Running

```bash
# Fresh story
python -m src.agents.langgraph_agent \
    --config examples/gone_home_style.json \
    --thread-id demo-1

# Resume (same thread-id after interruption)
python -m src.agents.langgraph_agent \
    --config examples/gone_home_style.json \
    --thread-id demo-1

# ADK equivalent
python -m src.agents.adk_agent \
    --config examples/gone_home_style.json \
    --session-id demo-1
```

## Story Style

All stories follow the **psychological / personal-journey** tradition (Gone Home, Firewatch, Disco Elysium). See `src/agents/prompts.py` for the style guide embedded in every prompt.

## Graph

The LangGraph graph is rendered to `data/graph.mmd` and `data/graph.png` on every run.

![Story Agent Graph](data/graph.png)

## LangGraph vs ADK

See `src/agents/README_comparison.md` for a side-by-side comparison of state management, persistence, loop control, and resume behavior.
