from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from cleo.graph import Edge, Node


class GraphStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                label TEXT NOT NULL,
                properties TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                type TEXT NOT NULL,
                weight REAL NOT NULL,
                properties TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES nodes(id),
                FOREIGN KEY(target_id) REFERENCES nodes(id)
            )
            """
        )
        self.connection.commit()

    def upsert_node(self, node: Node) -> None:
        self.connection.execute(
            """
            INSERT INTO nodes (id, type, label, properties)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type=excluded.type,
                label=excluded.label,
                properties=excluded.properties
            """,
            (node.node_id, node.node_type, node.label, json.dumps(node.properties)),
        )

    def add_edge(self, edge: Edge) -> None:
        self.connection.execute(
            """
            INSERT INTO edges (source_id, target_id, type, weight, properties)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                edge.source_id,
                edge.target_id,
                edge.edge_type,
                edge.weight,
                json.dumps(edge.properties),
            ),
        )

    def add_nodes(self, nodes: Iterable[Node]) -> None:
        for node in nodes:
            self.upsert_node(node)
        self.connection.commit()

    def add_edges(self, edges: Iterable[Edge]) -> None:
        for edge in edges:
            self.add_edge(edge)
        self.connection.commit()

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()
