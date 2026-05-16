"""
Postgres job store for Phase 3 story generation jobs.

Uses the same game_stories DB as LangGraph and ADK — consistent persistence
layer across all phases. Connection string swaps to AlloyDB in Cloud Run by
changing DB_HOST/DB_PORT env vars only; no code changes required.

Table: story_jobs
  job_id          UUID primary key
  status          text  (pending | complete)
  prompt          text
  user_id         text
  variants        jsonb (variant_id → chapter text)
  winner_variant_id int
  winner_text     text
  judge_reasoning text
  created_at      timestamptz

Race condition for judge trigger: we use SELECT FOR UPDATE inside a
transaction to atomically count variants and trigger the judge exactly once.
Uses SELECT FOR UPDATE — the standard Postgres pattern for atomic conditional updates.
"""

import json
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def _db_uri() -> str:
    return (
        f"postgresql://{os.environ['DB_USER']}:{os.environ.get('DB_PASSWORD', '')}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
    )


def setup_jobs_table() -> None:
    """Create story_jobs table if it doesn't exist. Called once at startup."""
    with psycopg.connect(_db_uri(), autocommit=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS story_jobs (
                job_id           TEXT PRIMARY KEY,
                status           TEXT NOT NULL DEFAULT 'pending',
                prompt           TEXT NOT NULL,
                user_id          TEXT NOT NULL,
                variants         JSONB NOT NULL DEFAULT '{}',
                winner_variant_id INT,
                winner_text      TEXT,
                judge_reasoning  TEXT,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)


def create_job(job_id: str, prompt: str, user_id: str) -> None:
    """
    Upsert a pending job row. ON CONFLICT DO NOTHING means it's safe to call
    from both the router (production) and the worker (local smoke tests) —
    whichever runs first wins, subsequent calls are no-ops.
    """
    with psycopg.connect(_db_uri()) as conn:
        conn.execute(
            """INSERT INTO story_jobs (job_id, prompt, user_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (job_id) DO NOTHING""",
            (job_id, prompt, user_id),
        )
        conn.commit()


def get_job(job_id: str) -> dict | None:
    with psycopg.connect(_db_uri()) as conn:
        row = conn.execute(
            """SELECT job_id, status, prompt, user_id, variants,
                      winner_variant_id, winner_text, judge_reasoning
               FROM story_jobs WHERE job_id = %s""",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    keys = ["job_id", "status", "prompt", "user_id", "variants",
            "winner_variant_id", "winner_text", "judge_reasoning"]
    return dict(zip(keys, row))


def write_variant_and_maybe_judge(
    job_id: str,
    variant_id: int,
    chapter_text: str,
    n_variants: int = 3,
) -> dict | None:
    """
    Write a completed variant and return all variants if this is the last one.

    Uses SELECT FOR UPDATE to ensure exactly one worker triggers the judge —
    the standard Postgres pattern for atomic conditional updates.

    Returns all variants dict if this was the last one, None otherwise.
    """
    with psycopg.connect(_db_uri()) as conn:
        with conn.transaction():
            # Lock the row so concurrent workers don't both think they're last
            row = conn.execute(
                "SELECT variants FROM story_jobs WHERE job_id = %s FOR UPDATE",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"job {job_id} not found")

            variants = row[0] or {}
            variants[str(variant_id)] = chapter_text

            conn.execute(
                "UPDATE story_jobs SET variants = %s WHERE job_id = %s",
                (json.dumps(variants), job_id),
            )

            if len(variants) >= n_variants:
                return variants  # caller runs judge and calls finalize_job()
    return None


def finalize_job(
    job_id: str,
    winner_variant_id: int,
    winner_text: str,
    judge_reasoning: str,
) -> None:
    with psycopg.connect(_db_uri()) as conn:
        conn.execute(
            """UPDATE story_jobs
               SET status = 'complete',
                   winner_variant_id = %s,
                   winner_text = %s,
                   judge_reasoning = %s
               WHERE job_id = %s""",
            (winner_variant_id, winner_text, judge_reasoning, job_id),
        )
        conn.commit()
