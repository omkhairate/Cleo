#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from cleo.report import generate_html_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an HTML graph report from the graph store.")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("cleo_graph.db"),
        help="Path to the SQLite database file to read.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("graph_report.html"),
        help="Path to the HTML report file to write.",
    )
    args = parser.parse_args()

    report_path = generate_html_report(args.db, args.out)
    print(f"Report generated at {report_path.resolve()}")


if __name__ == "__main__":
    main()
