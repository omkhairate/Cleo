#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from cleo.storage import GraphStore
from cleo.youtube_parser import parse_watch_history


def main() -> None:
    parser = argparse.ArgumentParser(description="Import YouTube watch history into the graph store.")
    parser.add_argument(
        "history",
        type=Path,
        help="Path to watch-history.json from Google Takeout (YouTube watch history).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("cleo_graph.db"),
        help="Path to the SQLite database file to create/update.",
    )
    args = parser.parse_args()

    nodes, edges = parse_watch_history(args.history)
    store = GraphStore(args.db)
    store.add_nodes(nodes)
    store.add_edges(edges)
    store.close()

    print(f"Imported {len(nodes)} nodes and {len(edges)} edges into {args.db}.")


if __name__ == "__main__":
    main()
