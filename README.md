# Story Agent — Phase 1

A psychological / personal-journey story generation system built with **LangGraph** and **Google ADK**, backed by **Vertex AI (Gemini)** and **Postgres** state persistence.

## Architecture

Orchestrator → Outline sub-agent → Chapter beats (stub) → Chapter content (stub) → Safety check → Summarizer (extracts chapter summary + character revelations) → loop

State persists to a local Postgres `game_stories` database via `PostgresSaver`. Resume is automatic — re-running with the same `--thread-id` continues from the last completed chapter.

## Setup

### 1. Python environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
# Edit .env — fill in GOOGLE_CLOUD_PROJECT and verify DB credentials
```

### 3. Vertex AI authentication

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

### 4. Postgres

Ensure a local Postgres instance is running with a `game_stories` database:

```bash
createdb game_stories
# Verify:
psql "postgresql://${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}" -c "SELECT 1;"
```

`PostgresSaver.setup()` creates the checkpoint tables on first run automatically.

## Running

```bash
# Fresh story
python -m story_agent.agents.langgraph_agent \
    --config examples/gone_home_style.json \
    --thread-id demo-1

# Resume (same thread-id after interruption)
python -m story_agent.agents.langgraph_agent \
    --config examples/gone_home_style.json \
    --thread-id demo-1

# ADK equivalent
python -m story_agent.agents.adk_agent \
    --config examples/gone_home_style.json \
    --session-id demo-1
```

## Story Style

All stories follow the **psychological / personal-journey** tradition (Gone Home, Firewatch, Disco Elysium). See `story_agent/agents/prompts.py` for the explicit style guide embedded in every prompt.

## Graph

The LangGraph graph is rendered to `story_agent/agents/graph.mmd` on every run. Open in any Mermaid-compatible viewer (VS Code extension, mermaid.live).

## LangGraph vs ADK

See `story_agent/agents/README_comparison.md` for a side-by-side comparison of state management, persistence, loop control, and resume behavior.
