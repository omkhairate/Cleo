from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from cleo.graph import Edge, Node


def _safe_slug(value: str) -> str:
    return "_".join(value.lower().split())


def parse_watch_history(path: str | Path) -> tuple[list[Node], list[Edge]]:
    """Parse YouTube watch history export JSON into graph nodes/edges.

    Expected input is the YouTube Takeout JSON file (e.g., watch-history.json).
    """
    history_path = Path(path)
    payload = json.loads(history_path.read_text(encoding="utf-8"))

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    user_node = Node(node_id="user:self", node_type="user", label="You")
    nodes[user_node.node_id] = user_node

    for entry in payload:
        title = entry.get("title") or "Unknown video"
        title_url = entry.get("titleUrl") or ""
        time_str = entry.get("time")
        watched_at = None
        if time_str:
            try:
                watched_at = datetime.fromisoformat(time_str.replace("Z", "+00:00")).isoformat()
            except ValueError:
                watched_at = time_str

        video_id = entry.get("details")
        video_key = title_url or title
        video_node_id = f"video:{_safe_slug(video_key)}"
        nodes.setdefault(
            video_node_id,
            Node(
                node_id=video_node_id,
                node_type="video",
                label=title,
                properties={"url": title_url, "raw_id": video_id},
            ),
        )

        subtitles = entry.get("subtitles") or []
        if subtitles:
            channel_name = subtitles[0].get("name") or "Unknown channel"
            channel_url = subtitles[0].get("url") or ""
        else:
            channel_name = "Unknown channel"
            channel_url = ""
        channel_node_id = f"channel:{_safe_slug(channel_name)}"
        nodes.setdefault(
            channel_node_id,
            Node(
                node_id=channel_node_id,
                node_type="channel",
                label=channel_name,
                properties={"url": channel_url},
            ),
        )

        edges.append(
            Edge(
                source_id=user_node.node_id,
                target_id=video_node_id,
                edge_type="watched",
                properties={"watched_at": watched_at},
            )
        )
        edges.append(
            Edge(
                source_id=channel_node_id,
                target_id=video_node_id,
                edge_type="published",
                properties={"observed_at": watched_at},
            )
        )

    return list(nodes.values()), edges


def iter_watch_entries(path: str | Path) -> Iterable[dict]:
    history_path = Path(path)
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    yield from payload
