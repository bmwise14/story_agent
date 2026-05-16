"""
Gradio demo UI for the Story Agent.

Simulates the full Phase 3 pipeline locally:
  1. User fills in a StoryConfig
  2. UI generates a job_id and calls /internal/worker 3 times in parallel
     (simulating what Pub/Sub does in production)
  3. Progress log streams as each variant completes
  4. Winning chapter from the LLM judge is displayed

Run:
  python -m src.ui.gradio_app

Requires the FastAPI server to already be running:
  uvicorn src.api.main:app --reload --port 8000
"""

import base64
import json
import queue
import threading
import time
import uuid

import gradio as gr
import requests

API_BASE = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Worker call helpers
# ---------------------------------------------------------------------------

def _build_pubsub_payload(job_id: str, variant_id: int, config_dict: dict, user_id: str) -> dict:
    """Encode the message body exactly as Pub/Sub would."""
    inner = json.dumps({
        "job_id": job_id,
        "variant_id": variant_id,
        "config": config_dict,
        "user_id": user_id,
    }).encode()
    return {"message": {"data": base64.b64encode(inner).decode()}}


def _call_worker(job_id: str, variant_id: int, config_dict: dict, log_q: queue.Queue) -> None:
    """Run in a thread. Posts progress to log_q, then posts a sentinel when done."""
    log_q.put(f"[variant {variant_id}] agent starting...")
    try:
        payload = _build_pubsub_payload(job_id, variant_id, config_dict, "demo-user")
        resp = requests.post(
            f"{API_BASE}/internal/worker",
            json=payload,
            timeout=600,
        )
        if resp.ok:
            log_q.put(f"[variant {variant_id}] complete")
        else:
            log_q.put(f"[variant {variant_id}] error {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        log_q.put(f"[variant {variant_id}] exception: {e}")
    finally:
        log_q.put(f"__done__{variant_id}")


# ---------------------------------------------------------------------------
# Main generation function (Gradio generator)
# ---------------------------------------------------------------------------

def generate_story(
    premise: str,
    themes_raw: str,
    chapter_count: int,
    char_name: str,
    char_archetype: str,
    char_desc: str,
):
    """
    Gradio generator — yields (log, chapter_text) tuples.
    The UI updates both the log box and the chapter output as they arrive.
    """
    if not premise.strip():
        yield "Please enter a premise.", ""
        return

    themes = [t.strip() for t in themes_raw.split(",") if t.strip()] or ["redemption"]
    config_dict = {
        "premise": premise.strip(),
        "chapter_count": int(chapter_count),
        "themes": themes,
        "characters": [{
            "name": char_name.strip() or "The Protagonist",
            "archetype": char_archetype.strip() or "hero",
            "short_description": char_desc.strip() or "A person facing an impossible choice",
        }],
    }

    job_id = str(uuid.uuid4())
    log: list[str] = [
        f"Job: {job_id}",
        f"Premise: {premise[:80]}{'...' if len(premise) > 80 else ''}",
        f"Launching 3 parallel variants...",
        "",
    ]
    yield "\n".join(log), ""

    # Fan-out: 3 threads, one per variant (mirrors what Pub/Sub does in production)
    log_q: queue.Queue = queue.Queue()
    threads = [
        threading.Thread(target=_call_worker, args=(job_id, vid, config_dict, log_q), daemon=True)
        for vid in range(3)
    ]
    for t in threads:
        t.start()

    # Stream log messages until all 3 variants report done
    finished = 0
    while finished < 3:
        try:
            msg = log_q.get(timeout=1.0)
            if msg.startswith("__done__"):
                finished += 1
            else:
                log.append(msg)
                yield "\n".join(log), ""
        except queue.Empty:
            # Heartbeat so Gradio doesn't time out the generator
            yield "\n".join(log), ""

    for t in threads:
        t.join()

    log.append("")
    log.append("All variants done. Waiting for judge...")
    yield "\n".join(log), ""

    # Poll GET /story/{job_id} until status == "complete"
    for attempt in range(60):
        time.sleep(3)
        try:
            resp = requests.get(f"{API_BASE}/story/{job_id}", timeout=10)
            if resp.ok:
                job = resp.json()
                if job.get("status") == "complete":
                    winner_id = job.get("winner_variant_id", "?")
                    reasoning = job.get("judge_reasoning", "")
                    winner_text = job.get("winner_text", "")
                    log.append(f"Winner: variant {winner_id}")
                    log.append(f"Judge: {reasoning[:200]}{'...' if len(reasoning) > 200 else ''}")
                    yield "\n".join(log), winner_text
                    return
        except Exception as e:
            log.append(f"  poll error: {e}")
            yield "\n".join(log), ""

    log.append("Timed out waiting for result.")
    yield "\n".join(log), ""


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Story Agent") as demo:
        gr.Markdown("# Story Agent\nGenerate a story chapter with 3 parallel variants judged by an LLM.")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### Story Config")
                premise = gr.Textbox(
                    label="Premise",
                    placeholder="A retired detective solves one last case in a rain-soaked city",
                    lines=3,
                )
                themes = gr.Textbox(
                    label="Themes (comma-separated)",
                    placeholder="redemption, obsession, loss",
                    value="redemption, obsession",
                )
                chapter_count = gr.Slider(
                    label="Chapters",
                    minimum=1,
                    maximum=5,
                    step=1,
                    value=1,
                )

                gr.Markdown("### Character")
                char_name = gr.Textbox(label="Name", placeholder="Ray Malone")
                char_archetype = gr.Textbox(label="Archetype", placeholder="weary hero")
                char_desc = gr.Textbox(
                    label="Short description",
                    placeholder="A retired NYPD detective haunted by an unsolved case",
                    lines=2,
                )

                submit_btn = gr.Button("Generate Story", variant="primary")

            with gr.Column(scale=1):
                gr.Markdown("### Progress")
                log_box = gr.Textbox(
                    label="",
                    lines=14,
                    max_lines=14,
                    interactive=False,
                )

        gr.Markdown("### Winning Chapter")
        chapter_out = gr.Textbox(
            label="",
            lines=20,
            interactive=False,
        )

        submit_btn.click(
            fn=generate_story,
            inputs=[premise, themes, chapter_count, char_name, char_archetype, char_desc],
            outputs=[log_box, chapter_out],
        )

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_port=7860, share=False, theme=gr.themes.Soft())
