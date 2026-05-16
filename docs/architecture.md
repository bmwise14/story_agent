# Story Agent — Architecture Diagrams

Two views of the same system: what runs on your laptop today vs what would run in
production on Cloud Run. The application code is identical; only the surrounding
infrastructure changes.

---

## 1. Local Setup (what you ran today)

Everything runs on your laptop except the two external API calls (Azure OpenAI for
generation, Google Calendar for booking). Pub/Sub is **not** involved — the Gradio UI
simulates Pub/Sub fan-out by firing three parallel HTTP requests directly at the
worker endpoint.

```mermaid
flowchart LR
    subgraph laptop [Your Laptop]
        direction TB

        subgraph browser [Browser - localhost:7860]
            UI[Gradio UI]
        end

        subgraph fastapi [FastAPI server - localhost:8000]
            direction TB
            Router["/story/start<br/>router route"]
            Worker["/internal/worker<br/>worker route"]
            Status["/story/JOB_ID<br/>poll route"]
        end

        Pg[(Local Postgres<br/>game_stories DB)]
        Threads["3 Python threads<br/>parallel POSTs"]
    end

    subgraph external [External APIs]
        Azure[Azure OpenAI]
        Cal[Google Calendar API]
        Sec[GCP Secret Manager]
    end

    UI -->|"submit form"| Threads
    Threads -->|"POST x3 in parallel"| Worker
    Worker -->|"LangGraph<br/>checkpoint per node"| Pg
    Worker -->|"generate chapter"| Azure
    Worker -->|"last worker:<br/>read refresh token"| Sec
    Worker -->|"last worker:<br/>book event"| Cal
    UI -.->|"poll every 3s"| Status
    Status -->|"read job state"| Pg
```

### Key facts about the local setup
- **Single process** — router, worker, and status route are all the same FastAPI app.
- **Same DB used for two purposes** — LangGraph checkpoints (resume mid-chapter on crash) and the story-job state (used by the LLM judge to know when all 3 variants are done).
- **No Pub/Sub** — Gradio's 3 Python threads simulate what Pub/Sub would deliver in production. The base64-encoded message shape in the curl payload is faithful to the real Pub/Sub format so the worker code works in both environments unchanged.
- **No Cloud Run, no Cloud SQL** — pure laptop infrastructure.
- **Secret Manager is the one GCP service actually involved locally** because the OAuth refresh token lives there even when the agent runs on your laptop.

---

## 2. Production Setup (Cloud Run + Pub/Sub)

The same code, deployed. Router and worker become two separate Cloud Run services
with very different ingress and concurrency settings. Pub/Sub does the real fan-out.
Cloud SQL replaces the local Postgres.

```mermaid
flowchart TB
    subgraph client [Your Laptop]
        UI[Gradio UI<br/>API_BASE points at Cloud Run]
    end

    subgraph gcp [GCP Project]
        direction TB

        subgraph publicedge [Public Edge]
            RouterSvc["Cloud Run: story-router<br/>--ingress=all<br/>--concurrency=80<br/>SA: story-router@"]
        end

        Topic[("Pub/Sub topic<br/>story-requests")]
        Sub["Push subscription<br/>auth via story-pubsub-invoker@"]

        subgraph privatesubnet [Internal VPC]
            direction LR
            W0["worker #0<br/>--ingress=internal<br/>--concurrency=1"]
            W1["worker #1<br/>--ingress=internal<br/>--concurrency=1"]
            W2["worker #2<br/>--ingress=internal<br/>--concurrency=1"]
        end

        SQL[(Cloud SQL Postgres<br/>db-f1-micro<br/>checkpoints + job state)]
        Sec[Secret Manager<br/>OAuth refresh token]
        Armor[Model Armor template]
    end

    Azure[Azure OpenAI]
    Cal[Google Calendar API]

    UI -->|"1. POST /story/start"| RouterSvc
    RouterSvc -->|"2. pre-screen"| Armor
    RouterSvc -->|"3. INSERT pending job"| SQL
    RouterSvc -->|"4. publish x3"| Topic
    Topic --> Sub
    Sub -->|"5a. push w/ OIDC"| W0
    Sub -->|"5b. push w/ OIDC"| W1
    Sub -->|"5c. push w/ OIDC"| W2

    W0 -->|"checkpoint each node"| SQL
    W1 -->|"checkpoint each node"| SQL
    W2 -->|"checkpoint each node"| SQL

    W0 --> Azure
    W1 --> Azure
    W2 --> Azure

    W0 -->|"6. write variant<br/>SELECT FOR UPDATE"| SQL
    W1 -->|"6. write variant<br/>SELECT FOR UPDATE"| SQL
    W2 -->|"7. last writer:<br/>judge + finalize"| SQL
    W2 -->|"8. read refresh token"| Sec
    W2 -->|"9. book event"| Cal

    UI -.->|"poll every 3s"| RouterSvc
    RouterSvc -.->|"read job state"| SQL
```

### Key facts about production
- **Two Cloud Run services from the same image** — `SERVICE_ROLE` env var distinguishes them. Identical Docker image, different deployment configs.
- **Public router, private worker** — router accepts the world; worker only accepts Pub/Sub push requests authenticated with an OIDC token from the invoker service account. The `--ingress=internal` flag drops external traffic at the network layer before auth even runs.
- **Concurrency 80 vs 1** — router does cheap I/O, fine to pack many requests per container. Worker does slow LLM calls, one per container forces Cloud Run to scale OUT (more containers) instead of UP (more requests per container).
- **Pub/Sub for free retry + fan-out** — if a worker fails, Pub/Sub re-delivers automatically. LangGraph checkpoints in Postgres mean the retry resumes from the last completed node, not the top.
- **Three service accounts** — router can publish to Pub/Sub. Worker can call LLMs and read secrets. Invoker can call the worker URL. None of them have permissions they don't strictly need.

---

## 3. Side-by-Side Comparison

| Concern | Local (today) | Production (Cloud Run) |
|---|---|---|
| Router process | FastAPI on localhost:8000 | Cloud Run `story-router`, `--ingress=all` |
| Worker process | Same FastAPI on localhost:8000 | Cloud Run `story-worker`, `--ingress=internal` |
| Fan-out mechanism | 3 Python threads in Gradio | Real Pub/Sub push subscription |
| Concurrency model | One process, async event loop + `to_thread` | Multiple containers, one request per container |
| Postgres | Local instance | Cloud SQL `db-f1-micro` |
| Postgres connection | TCP `localhost:5432` | Unix socket `/cloudsql/PROJECT:REGION:INSTANCE` |
| Identity | Your local gcloud auth + .env keys | Three GCP service accounts (least privilege) |
| Auth on worker | None (localhost) | OIDC token validated by Cloud Run |
| Network egress to DB | localhost loopback | Private (VPC + Cloud SQL Auth Proxy) |
| Cold start | None (server is always running) | ~3-5 sec on first request per worker container |
| Cost | $0 | ~$7/mo if Cloud SQL left running; $0 if stopped |
| Code differences | None | Same code; only env vars change |

---

## 4. The One Thing That Changes in Code

Everything else is just env vars and IAM. The single functional difference is the
database connection string.

**Local:**
```python
postgresql://story_user:pwd@localhost:5432/game_stories
```

**Production (Cloud SQL via unix socket):**
```python
postgresql://story_user:pwd@/game_stories?host=/cloudsql/PROJECT:REGION:story-db
```

That's it. Same `psycopg.connect()` call, same `PostgresSaver`, same SQL queries.
Cloud SQL Auth Proxy is mounted automatically by Cloud Run when you pass
`--add-cloudsql-instances` on deploy.

---

## 5. What Each Diagram Tells You About a Different Skill

The **local** diagram shows you can engineer a real agentic system end-to-end on your
own machine: state management, parallelism, persistence, OAuth, LLM evaluation. You
can prove every part of the logic works without paying for cloud infrastructure.

The **production** diagram shows you understand how that same code runs at scale:
managed services, identity and access management, network isolation, async fan-out,
retry semantics, and how each design choice (concurrency 1 vs 80, public vs internal
ingress, three service accounts) maps to a real production concern.
