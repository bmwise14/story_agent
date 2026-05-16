"""
Render the LangGraph StoryAgent graph as both Mermaid source and PNG image
into the data/ folder.

Usage:
    python -m scripts.render_graph

Uses LangGraph's built-in renderers. These work correctly because the agent
declares explicit path_map dicts on every add_conditional_edges() call —
without that, LangGraph silently drops conditional edges and downstream
nodes appear as floating orphans.

The PNG is rendered via mermaid.ink (the same service mermaid.live uses) —
requires network access.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from src.agents.langgraph_agent import StoryAgent


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(repo_root, "data")
    os.makedirs(data_dir, exist_ok=True)

    db_uri = (
        f"postgresql://{os.environ['DB_USER']}:{os.environ.get('DB_PASSWORD', '')}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
    )
    agent = StoryAgent(db_uri=db_uri)

    try:
        graph = agent.graph.get_graph()

        mmd_path = os.path.join(data_dir, "graph.mmd")
        with open(mmd_path, "w") as f:
            f.write(graph.draw_mermaid())
        print(f"  wrote {mmd_path}")

        png_path = os.path.join(data_dir, "graph.png")
        with open(png_path, "wb") as f:
            f.write(graph.draw_mermaid_png())
        print(f"  wrote {png_path}")
    finally:
        agent._pool.close()


if __name__ == "__main__":
    main()
