import re

from assistant_core.models import UserProfile
from assistant_core.services.memory import InMemoryBrainGraphStore, InMemoryProfileStore


class MemoryExtractor:
    """Lightweight heuristics to turn user messages into durable preferences."""

    def __init__(
        self,
        profile_store: InMemoryProfileStore,
        brain_graph_store: InMemoryBrainGraphStore,
    ) -> None:
        self.profile_store = profile_store
        self.brain_graph_store = brain_graph_store

    def ingest_user_message(self, user_id: str, message: str) -> UserProfile:
        lowered = message.lower().strip()

        if "app-first" in lowered:
            self.profile_store.set_preference(
                user_id,
                "interface_style",
                "app-first",
                source="inferred",
            )

        if "concise" in lowered:
            self.profile_store.set_preference(
                user_id,
                "response_style",
                "concise",
                source="inferred",
            )

        if "notion" in lowered and "github" in lowered:
            self.profile_store.add_workflow(
                user_id,
                "planning-to-build",
                "Use Notion for planning and GitHub for implementation.",
                source="inferred",
            )

        name_match = re.search(r"\bcall me ([a-z0-9 _-]+)", lowered)
        if name_match:
            self.profile_store.set_display_name(user_id, name_match.group(1).strip().title())

        prefer_match = re.search(r"\bi prefer ([^.!\n]+)", message, re.IGNORECASE)
        if prefer_match:
            preference_text = prefer_match.group(1).strip()
            self.profile_store.set_preference(
                user_id,
                "stated_preference",
                preference_text,
                source="inferred",
            )

        profile = self.profile_store.get_profile(user_id)
        self.brain_graph_store.sync_profile(profile)
        return profile
