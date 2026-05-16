"""
Render the LangGraph StoryAgent graph as both Mermaid source and PNG image
into the data/ folder.

Usage:
    python -m scripts.render_graph

We write a hand-built mermaid here instead of using LangGraph's draw_mermaid()
because the built-in renderer drops conditional edges entirely — nodes like
increment_retry and increment_chapter end up as floating orphans. The graph
below is a faithful representation of the wiring in src/agents/langgraph_agent.py:

    START → make_outline → generate_chapter_beats → generate_chapter_content
          → check_content →[pass]→ summarize_chapter → increment_chapter
                                                                →[continue]→ generate_chapter_beats
                                                                →[done]→ END
                          →[fail]→ increment_retry → generate_chapter_content
                          →[max_retries]→ END
"""

import os
import subprocess
import sys

MERMAID = """---
config:
  flowchart:
    curve: linear
---
graph TD;
    __start__([__start__])
    make_outline[make_outline]
    generate_chapter_beats[generate_chapter_beats]
    generate_chapter_content[generate_chapter_content]
    check_content{{check_content}}
    increment_retry[increment_retry]
    summarize_chapter[summarize_chapter]
    increment_chapter{{increment_chapter}}
    __end__([__end__])

    __start__ --> make_outline
    make_outline --> generate_chapter_beats
    generate_chapter_beats --> generate_chapter_content
    generate_chapter_content --> check_content

    check_content -->|"pass"| summarize_chapter
    check_content -->|"fail"| increment_retry
    check_content -->|"max_retries"| __end__

    increment_retry --> generate_chapter_content
    summarize_chapter --> increment_chapter

    increment_chapter -->|"more chapters"| generate_chapter_beats
    increment_chapter -->|"all done"| __end__
"""


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(repo_root, "data")
    os.makedirs(data_dir, exist_ok=True)

    mmd_path = os.path.join(data_dir, "graph.mmd")
    png_path = os.path.join(data_dir, "graph.png")

    with open(mmd_path, "w") as f:
        f.write(MERMAID)
    print(f"  wrote {mmd_path}")

    # Render PNG via the mermaid CLI (mmdc).
    # Install: npm install -g @mermaid-js/mermaid-cli
    try:
        subprocess.run(
            ["mmdc", "-i", mmd_path, "-o", png_path, "-b", "transparent", "-s", "2"],
            check=True,
        )
        print(f"  wrote {png_path}")
    except FileNotFoundError:
        print("  mmdc not found — install with: npm install -g @mermaid-js/mermaid-cli")
        print(f"  skipped {png_path} (you can paste {mmd_path} into mermaid.live to render)")
        sys.exit(1)


if __name__ == "__main__":
    main()
