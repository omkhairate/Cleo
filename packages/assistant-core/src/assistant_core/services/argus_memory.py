from typing import Any

import httpx

from assistant_core.config import Settings
from assistant_core.models import BrainGraph, ToolCallRecord
from assistant_core.services.memory import InMemoryBrainGraphStore


class ArgusMemoryClient:
    """HTTP client for Cleo's local Argus episodic-memory add-on."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.argus_base_url.rstrip("/")

    def available(self) -> bool:
        if not self.settings.argus_enabled:
            return False
        try:
            response = httpx.get(
                f"{self.base_url}/health",
                timeout=self.settings.argus_timeout_seconds,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def query_memory(self, query: str, limit: int = 8) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/addon/query",
            json={"query": query, "limit": limit},
            timeout=self.settings.argus_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def ingest_context(
        self,
        text: str,
        *,
        source: str = "cleo",
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/addon/context",
            json={"text": text, "source": source, "metadata": metadata or {}},
            timeout=self.settings.argus_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def graph(self) -> dict[str, list[dict[str, Any]]]:
        response = httpx.get(
            f"{self.base_url}/graph",
            timeout=self.settings.argus_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()


class ArgusGraphSync:
    """Copies Argus graph nodes into Cleo's brain graph as an external memory layer."""

    def __init__(
        self,
        client: ArgusMemoryClient,
        brain_graph_store: InMemoryBrainGraphStore,
    ) -> None:
        self.client = client
        self.brain_graph_store = brain_graph_store

    def sync(self) -> tuple[BrainGraph, ToolCallRecord]:
        payload = self.client.graph()
        self.brain_graph_store.ensure_node(
            node_id="connector:argus",
            label="Argus",
            kind="connector",
            group="integrations",
            metadata={"scope": "episodic memory"},
        )
        self.brain_graph_store.ensure_edge(
            source="assistant:cleo",
            target="connector:argus",
            relation="queries_memory",
            strength=0.9,
        )

        node_count = 0
        for node in payload.get("nodes", []):
            node_id = f"argus:{node['id']}"
            self.brain_graph_store.ensure_node(
                node_id=node_id,
                label=node.get("label", node["id"]),
                kind=node.get("type", "entity"),
                group="argus",
                metadata={str(key): str(value) for key, value in node.get("metadata", {}).items()},
            )
            self.brain_graph_store.ensure_edge(
                source="connector:argus",
                target=node_id,
                relation="contains",
                strength=0.75,
            )
            node_count += 1

        edge_count = 0
        for edge in payload.get("edges", []):
            self.brain_graph_store.ensure_edge(
                source=f"argus:{edge['source_id']}",
                target=f"argus:{edge['target_id']}",
                relation=edge.get("relation", "related_to"),
                strength=0.7,
            )
            edge_count += 1

        graph = self.brain_graph_store.get_graph()
        return graph, ToolCallRecord(
            tool_name="argus_sync_graph",
            arguments={"nodes": str(node_count), "edges": str(edge_count)},
            result_summary=f"Synced {node_count} Argus nodes and {edge_count} Argus edges.",
        )
