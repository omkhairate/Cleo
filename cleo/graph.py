from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class Node:
    node_id: str
    node_type: str
    label: str
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    source_id: str
    target_id: str
    edge_type: str
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)
