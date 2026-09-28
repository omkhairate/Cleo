import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/assistant-core/src"))
from assistant_core.services.proactivity import ProactivityService
from assistant_core.services.context_builder import ContextBuilder
from assistant_core.models import BrainGraph, ConversationHistory, UserProfile


class ProactivityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1000
        self.path = Path(self.directory.name) / "pulse.sqlite3"
        self.service = ProactivityService(self.path, clock=lambda: self.now)

    def send(self, operation, **kwargs):
        return self.service.handle(dict(operation=operation, **kwargs))

    def test_paused_by_default_and_no_event_collection(self):
        state = self.send("event", app="Mail", url="https://example.com")
        self.assertFalse(state["enabled"])
        self.assertEqual(state["activity"], [])
        self.assertEqual(state["active_app"], "")

    def test_goals_persist_and_complete_explicitly(self):
        state = self.send("goal", title="Finish Cleo")
        reloaded = ProactivityService(self.path).handle({})
        self.assertEqual(reloaded["goals"], state["goals"])
        state = self.send("complete", item_id=state["goals"][0]["id"])
        self.assertEqual(state["goals"], [])

    def test_goals_are_available_to_local_chat_context_only(self):
        self.send("goal", title="Finish Cleo")
        builder = ContextBuilder(self.service)
        for user, expected in (("local-user", ["Finish Cleo"]), ("other-user", [])):
            context = builder.build(user_id=user, conversation_id="test", profile=UserProfile(user_id=user),
                                    history=ConversationHistory(conversation_id="test"), graph=BrainGraph())
            self.assertEqual(context.active_goals, expected)

    def test_research_is_opt_in_deduplicated_and_sanitized(self):
        self.send("settings", enabled=True)
        self.assertEqual(self.send("event", app="Arc", url="https://example.com/page")["links"], [])
        self.send("settings", research=True)
        for url in ("https://example.com/page?token=secret#fragment", "https://example.com/page", "file:///secret", "https://user:pass@example.com"):
            state = self.send("event", app="Arc", url=url)
        self.assertEqual([link["url"] for link in state["links"]], ["https://example.com/page"])

    def test_suggestion_cooldown_and_feedback(self):
        self.send("settings", enabled=True, review_minutes=5)
        self.send("goal", title="Ship Cleo")
        self.now += 301
        state = self.send("tick")
        self.assertEqual(len(state["suggestions"]), 1)
        self.assertEqual(len(self.send("tick")["suggestions"]), 1)
        self.send("feedback", item_id=state["suggestions"][0]["id"], accepted=False)
        self.now += 301
        self.assertEqual(self.send("tick")["suggestions"], [])
        self.now += 301
        self.assertEqual(len(self.send("tick")["suggestions"]), 1)

    def test_pause_and_clear_preserve_goals(self):
        self.send("goal", title="Work")
        self.send("settings", enabled=True, research=True)
        self.send("event", app="Arc", url="https://example.com")
        self.send("settings", enabled=False)
        self.now += 99999
        self.assertEqual(self.send("tick")["suggestions"], [])
        state = self.send("clear")
        self.assertEqual(state["activity"], [])
        self.assertEqual(state["links"], [])
        self.assertEqual(len(state["goals"]), 1)

    def test_unsupported_action_and_invalid_goal_rejected(self):
        with self.assertRaises(ValueError):
            self.send("send_mail")
        with self.assertRaises(ValueError):
            self.send("goal", title="   ")

    def test_separate_service_instances_do_not_lose_concurrent_updates(self):
        services = [ProactivityService(self.path) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: services[i % 4].handle({"operation": "goal", "title": f"Goal {i}"}), range(20)))
        self.assertEqual(len(self.send("snapshot")["goals"]), 20)
