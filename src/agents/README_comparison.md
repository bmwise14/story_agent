# LangGraph vs Google ADK — Side-by-Side Comparison

Both agents implement the same gaming-story orchestration: one functional outline
sub-agent, two stub sub-agents (beats, content), a deterministic safety check, a
chapter summarizer, and per-chapter loop control. State persists to the same local
Postgres `game_stories` database. The code is the reference — this doc is the
talking guide.

---

## The Five Dimensions

### 1. State Schema

| | LangGraph | ADK |
|---|---|---|
| How | Explicit `TypedDict` declared upfront | Plain `dict` — no schema |
| Accumulation | `Annotated[dict, reducer]` on the type | Manual merge inside each agent body before `state_delta` |
| Readable at a glance | Yes — schema *is* the contract | No — you must trace every `state_delta` yield across all agent files |

**LangGraph** (`langgraph_agent.py`):
```python
class StoryState(TypedDict):
    outline: Optional[Outline]
    chapter_beats: Annotated[dict, _merge_dicts]          # int → ChapterBeats
    chapters:      Annotated[dict, _merge_dicts]          # int → Chapter
    character_revelations: Annotated[dict, _append_revs]  # str → list[Revelation]
    current_chapter: int
    retry_count: int
    last_check_verdict: str
```
Anyone reading this file knows what the agent tracks and *how* each field accumulates
without reading a single node. Nodes just return the new entry; the reducer handles
the merge:
```python
return {"chapters": {ch: chapter}}          # reducer merges into existing dict
return {"chapter_beats": {ch: beats}}
return {"character_revelations": new_revs}  # reducer extends per-character lists
```

**ADK** (`adk_agent.py`): `session.state` is a plain dict. To understand what it
holds, you have to read every `state_delta` yield across `OutlineAgent`,
`BeatsAndContentAgent`, and `CheckAndSummarizeAgent`. There is no single source of
truth. The merging is done imperatively inline:
```python
revs = state.get("character_revelations", {})
for rev in s_result.new_revelations:
    revs.setdefault(rev.character_name, []).append(rev.model_dump())
yield Event(actions=EventActions(state_delta={"character_revelations": revs}))
```

**Interview line:** "LangGraph treats the state schema as the contract — I can answer
'what does this agent track?' without reading a single node. ADK gives me total
freedom but the state shape is implicit; I have to read all the agent bodies to
reconstruct it."

---

### 2. Persistence

| | LangGraph | ADK |
|---|---|---|
| Mechanism | `PostgresSaver` — bring-your-own checkpointer | `DatabaseSessionService` — managed by the framework |
| Setup | Manual `checkpointer.setup()` creates tables | Automatic — ADK creates its own tables |
| Granularity | Checkpoint written after **every node** | Session state written at the **end of a full invocation** |
| Resume unit | Individual node — can resume mid-chapter | Full chapter turn — replays the whole chapter on resume |
| Postgres tables | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` | `sessions`, `events` |

Both point at the same `game_stories` database, same host, different table layouts.

**Key difference in granularity:** if the process is killed after the safety check but
before the summarizer, LangGraph resumes at `summarize_chapter`. ADK resumes at the
start of that chapter and re-runs beats, content, and check again. LangGraph stores
node-level supersteps; ADK stores turn-level snapshots.

**Critical ADK gotcha discovered in this build:** direct mutation of `ctx.session.state`
is in-memory only with `DatabaseSessionService`. State changes must be yielded as
`EventActions(state_delta={...})` to be committed to Postgres. LangGraph has no
equivalent footgun — returning a dict from a node always persists.

**Interview line:** "Same Postgres database under both, different table layouts and
different granularity. LangGraph checkpoints every node; ADK checkpoints every
invocation turn. That means LangGraph can resume mid-chapter after a hard kill; ADK
replays the full chapter."

---

### 3. Loop Control

| | LangGraph | ADK |
|---|---|---|
| Mechanism | Conditional edge function + state field | `LoopAgent(max_iterations=N)` + escalate event |
| Visibility | Fully explicit — routing logic is a function you write | Config-driven — termination is signaled via `EventActions(escalate=True)` |
| Early exit | Return `END` from routing function | Yield `Event(actions=EventActions(escalate=True))` from a `BaseAgent` |
| Retry logic | `retry_count` in state + `increment_retry` node | Same counter in session state, reset manually in `state_delta` |

**LangGraph** — the routing function is plain Python, readable like a flowchart:
```python
def route_check(self, state: StoryState) -> str:
    verdict = state.get("last_check_verdict", "fail")
    if verdict == "pass":       return "summarize_chapter"
    elif verdict == "max_retries": return END
    else:                       return "increment_retry"

def route_chapter(self, state: StoryState) -> str:
    if state["current_chapter"] > state["config"].chapter_count:
        return END
    return "generate_chapter_beats"
```

**ADK** — `LoopAgent` handles iteration; termination requires an explicit escalate
signal. The `StoryCompleteChecker` runs *first* in the `LoopAgent` (not inside the
`SequentialAgent`) so it can short-circuit at the top of each iteration before any
LLM work:
```python
class StoryCompleteChecker(BaseAgent):
    async def _run_async_impl(self, ctx):
        if ctx.session.state.get("story_complete", False):
            yield Event(actions=EventActions(escalate=True))
```

Placement matters: if this checker runs last (inside the sequential pipeline), a
completed story still triggers beats and content generation on the next iteration
before the loop can stop.

**Interview line:** "LangGraph loop control is a routing function — I see every branch
in code. ADK uses LoopAgent with max_iterations plus an escalate event. The escalate
pattern is less obvious, but once you know it, it's clean."

---

### 4. Resume Behavior

| | LangGraph | ADK |
|---|---|---|
| Resume key | `thread_id` in `{"configurable": {"thread_id": ...}}` | `session_id` passed to `session_service.get_session()` |
| Detection | `graph.get_state(config)` returns existing values | `session_service.get_session(app_name, user_id, session_id)` |
| Idempotency | `make_outline` checks `state.get("outline") is not None` | `OutlineAgent` checks `state.get("outline") is not None` |
| Resume point | Last completed node (node-level checkpoint) | Start of next incomplete chapter (turn-level) |

Both agents detect an existing run and skip the outline on resume:

```
# LangGraph output — second run same thread_id:
Resuming thread 'lg-compare-1' | current_chapter=2 | 1 chapter(s) completed
  [outline] already in state — skipping (resumed)
  [ADK] story complete — escalating to stop loop   ← StoryCompleteChecker fires immediately

# ADK output — second run same session_id:
Resuming ADK session 'adk-compare-1' | current_chapter=2 | 1 chapter(s) completed
  [ADK outline] already in state — skipping (resumed)
  [ADK] story complete — escalating to stop loop   ← no LLM calls, exits in ~1 second
```

---

### 5. Orchestration Style

| | LangGraph | ADK |
|---|---|---|
| Orchestrator | `StateGraph` with explicit nodes and edges | `SequentialAgent` + `LoopAgent` — declarative composition |
| Sub-agents | Node functions (methods on `StoryAgent` class) | `BaseAgent` subclasses with `_run_async_impl` |
| Wiring | `graph.add_edge()`, `graph.add_conditional_edges()` | Pass sub-agents as constructor arguments |
| Async | Optional (`astream`) | Required — all agents are `async` generators |
| Visibility | Graph is renderable (`draw_mermaid()`) | No built-in graph visualization |

**LangGraph** produces a visual graph on startup (`data/graph.mmd` / `data/graph.png`):

```
START → make_outline → generate_chapter_beats → generate_chapter_content
      → check_content →[pass]→ summarize_chapter → increment_chapter →[done]→ END
                      →[fail]→ increment_retry → generate_chapter_content
                      →[max_retries]→ END
                                                 →[continue]→ generate_chapter_beats
```

**ADK** composition is tree-structured:
```
SequentialAgent (story_orchestrator)
├── OutlineAgent
└── LoopAgent (chapter_loop, max_iterations=60)
    ├── StoryCompleteChecker    ← gate: escalate if done
    └── SequentialAgent (chapter_pipeline)
        ├── BeatsAndContentAgent
        └── CheckAndSummarizeAgent
```

---

### 6. How Checkpointing Granularity Shapes Node Decomposition

LangGraph's checkpointing incentive subtly influences how you split up work.

In this build, beats and content are **two separate LangGraph nodes**
(`generate_chapter_beats` → `generate_chapter_content`). That means:
- `chapter_beats` is a first-class field in `StoryState`
- A crash between beats and content resumes at `generate_chapter_content` — beats
  are not re-run
- The graph visualization shows the boundary explicitly

In ADK, beats and content are **one combined agent** (`BeatsAndContentAgent`). Beats
are a local variable inside `_run_async_impl`, never written to `state_delta`, and
therefore invisible in session state. If the process dies mid-agent, the whole agent
re-runs from the top — beats and content together.

This is not a limitation of either framework. You could merge the LangGraph nodes into
one and drop `chapter_beats` from the schema entirely. Or you could add beats to ADK's
`state_delta`. Both are valid. But the design pressure points differently:

**LangGraph** has an implicit incentive to split work finely because each node
boundary is a free checkpoint. Finer nodes = more precise resume = less work repeated
on failure. The schema grows to reflect those boundaries.

**ADK** has no such incentive — it only checkpoints at invocation-turn level
regardless of how many agents run inside a turn. Combining agents costs nothing in
resume granularity, so the natural pull is toward fewer, larger agents.

**Interview line:** "The checkpointing granularity in LangGraph subtly shapes how you
decompose work. Every node boundary is a resume point, so there's a natural pull
toward finer decomposition. ADK only checkpoints at the turn level, so combining
agents costs you nothing — the design pressure runs in the opposite direction."

---

## Actual State Comparison — One Chapter Run

Both agents ran against `examples/gone_home_1ch.json` (1 chapter, same premise).

**LangGraph** (`data/langgraph_state.json`) — typed, structured Pydantic objects:
```json
{
  "outline":   { "logline": "...", "act_breakdown": [...], "character_arcs": {...} },
  "chapter_beats": { "1": { "chapter_number": 1, "beats": [...], "emotional_arc": "..." } },
  "chapters":  { "1": { "chapter_number": 1, "text": "...", "summary": "..." } },
  "character_revelations": {
    "Claire Greenbriar": [
      { "chapter_number": 1, "what_was_revealed": "...", "kind": "fact" }
    ]
  },
  "current_chapter": 2,
  "retry_count": 0,
  "last_check_verdict": "pass"
}
```

**ADK** (`data/adk_state.json`) — same content, plain dict, no `chapter_beats` key
(ADK session state only holds what agents explicitly write via `state_delta`):
```json
{
  "outline": { "logline": "...", "act_breakdown": [...], "character_arcs": {...} },
  "chapters": { "1": { "text": "...", "summary": "...", "chapter_number": 1 } },
  "character_revelations": {
    "Claire Greenbriar": [
      { "chapter_number": 1, "what_was_revealed": "...", "kind": "fact" }
    ]
  },
  "current_chapter": 2,
  "story_complete": true,
  "chapter_passed": true,
  "retry_count": 0
}
```

Notice ADK has `story_complete` and `chapter_passed` (internal flags the agents
explicitly wrote) but no `chapter_beats` — because `BeatsAndContentAgent` never wrote
beats to `state_delta`, only the final chapter dict. In LangGraph, `chapter_beats`
is in the schema so it always appears.

---

### 7. Observability

| | LangGraph | ADK |
|---|---|---|
| Platform | LangSmith (LangChain's hosted tracing UI) | Google Cloud Trace + ADK events table in Postgres |
| Setup cost | Zero — `LANGCHAIN_TRACING_V2=true` in `.env` and every run is traced | Manual Cloud Trace wiring or custom logging per agent |
| What you see | Full graph execution, token counts per node, latency per step, state at each checkpoint, replay individual runs | Events log in the `events` table; queryable but no UI |
| Integration | Native — LangGraph speaks the LangChain tracing protocol | No LangSmith support — different ecosystem entirely |

LangSmith is a genuinely strong pairing with LangGraph and requires zero extra code
in this build. Every run shows up automatically — every node, every LLM call, token
counts, latency, and the full state at each checkpoint.

ADK's observability story is not as mature yet. The events table gives you a raw log
of what fired, but there is no equivalent of LangSmith's UI. For production ADK
deployments you'd wire up Cloud Trace and Cloud Logging manually.

**You could call `session_service.update_session()` manually after each ADK agent to
approximate node-level checkpointing** — but you'd be fighting the framework. You'd
pay one extra Postgres round-trip per agent, own the transactional consistency
guarantee yourself, and risk the events log and session state diverging. If you need
node-level resume and rich observability, that's a signal to use LangGraph instead.

**Interview line (for a Google audience):** "ADK integrates naturally with Google
Cloud Trace and Cloud Logging. LangGraph has deeper observability tooling through
LangSmith — zero-config tracing, a full graph replay UI, and per-node token counts.
LangSmith is more mature right now; the ADK ecosystem is still catching up."

---

## Summary Table

| Dimension | LangGraph | ADK |
|---|---|---|
| State schema | Explicit `TypedDict` + `Annotated` reducers | Implicit plain dict |
| Persistence | `PostgresSaver`; manual setup; node-level checkpoints | `DatabaseSessionService`; automatic; turn-level snapshots |
| State update contract | Return dict from node — always persisted | Must yield `EventActions(state_delta=...)` — direct mutation is in-memory only |
| Loop control | Conditional edge functions — fully visible in code | `LoopAgent` + `escalate=True` event |
| Resume granularity | Last completed **node** | Last completed **chapter turn** |
| Graph visibility | `draw_mermaid()` renders the full graph | No built-in visualization |
| Observability | LangSmith — zero config, full UI | Cloud Trace + raw events table — manual wiring |
| Orchestration style | Nodes + edges (graph topology) | Nested agent objects (tree topology) |
| Async | Optional | Required |

---

## 90-Second Interview Answer

> "Both frameworks implement the same orchestrator-plus-sub-agents pattern. I built a
> story agent where a functional outline sub-agent feeds into a per-chapter loop of
> beats, content, safety check, and summarizer.
>
> The most important contrast is state. LangGraph makes me declare a TypedDict upfront
> with explicit reducers on the accumulating fields — anyone reading the schema knows
> what the agent tracks and how it grows. ADK's session state is a plain dict; the
> shape lives scattered across the agent bodies.
>
> On persistence: both hit the same Postgres database, different tables. LangGraph
> checkpoints after every node — if you kill it mid-chapter, the next run resumes at
> the exact node that was about to execute. ADK checkpoints at the end of a full
> invocation turn, so a hard kill replays the whole chapter.
>
> Loop control: LangGraph uses routing functions you write — every branch is readable
> code. ADK uses LoopAgent with max_iterations and an escalate event from a guard
> agent that runs at the top of each iteration.
>
> For a use case like this — multi-chapter story with retry logic, resume from failure,
> and structured state evolution — I'd lean LangGraph because the schema-as-contract
> pays off fast when something goes wrong. ADK is the right call when you want managed
> infrastructure and are composing agents from a shared library rather than writing the
> routing yourself."
