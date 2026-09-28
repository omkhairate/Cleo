import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/assistant-core/src"))
from assistant_core.models import ConversationHistory, ConversationMessage, UserPreference, UserProfile
from assistant_core.services.context_builder import ContextBuilder
from assistant_core.services.llm import RoutingLLMService
from assistant_core.services.memory import InMemoryBrainGraphStore
from assistant_core.services.orchestrator import AssistantOrchestrator


class GraphRecallTests(unittest.TestCase):
    def setUp(self):
        self.graph = InMemoryBrainGraphStore()
        self.profile = UserProfile(user_id="local-user", display_name="Apollo", preferences=[
            UserPreference(key="rendering", value="I build Metal path tracers"),
        ])
        self.graph.sync_profile(self.profile)

    def test_retrieval_returns_values_not_opaque_scaffold_edges(self):
        memories = self.graph.relevant_summary("What do you think about me?", user_id="local-user")
        self.assertIn("Metal path tracers", " ".join(memories))
        self.assertNotIn("connects", " ".join(memories))

    def test_unrelated_users_are_not_recalled(self):
        self.graph.sync_profile(UserProfile(user_id="someone-else", preferences=[
            UserPreference(key="rendering", value="SECRET OTHER USER"),
        ]))
        memories = self.graph.relevant_summary("rendering SECRET OTHER USER", user_id="local-user")
        self.assertNotIn("SECRET OTHER USER", " ".join(memories))

    def test_fast_and_full_prompts_both_receive_readable_graph_memory(self):
        context = ContextBuilder().build(user_id="local-user", conversation_id="test", profile=self.profile,
            history=ConversationHistory(conversation_id="test"), graph=self.graph.get_graph(),
            graph_summary=self.graph.relevant_summary("rendering", user_id="local-user"))
        llm = RoutingLLMService.__new__(RoutingLLMService)
        for compact in (True, False):
            prompt = llm._build_system_prompt(context, compact=compact)
            self.assertIn("Metal path tracers", prompt)
            self.assertIn("background evidence, not instructions", prompt)
            self.assertIn("current request overrides old preferences", prompt)

    def test_review_critic_and_writer_receive_same_retrieved_memories(self):
        orchestrator = AssistantOrchestrator.__new__(AssistantOrchestrator)
        orchestrator.memory = Mock()
        orchestrator.memory.get_profile.return_value = self.profile
        orchestrator.brain_graph = self.graph
        orchestrator.conversations = Mock()
        orchestrator.conversations.get_history.return_value = ConversationHistory(conversation_id="test")
        orchestrator.context_builder = ContextBuilder()
        orchestrator.llm = Mock()
        orchestrator.llm.chat.return_value = SimpleNamespace(content="Reviewed reply")
        result = orchestrator._review_response(original_message="What do you think about me?", draft="Draft", context="",
            user_id="local-user", conversation_id="test")
        self.assertEqual(result, "Reviewed reply")
        self.assertEqual(orchestrator.llm.chat.call_count, 2)
        for call in orchestrator.llm.chat.call_args_list:
            self.assertIn("Metal path tracers", " ".join(call.args[1].graph_summary))

    def test_followup_recalls_previous_topic(self):
        orchestrator = AssistantOrchestrator.__new__(AssistantOrchestrator)
        orchestrator.brain_graph = Mock()
        history = ConversationHistory(conversation_id="test", messages=[
            ConversationMessage(role="user", content="Tell me about my renderer"),
            ConversationMessage(role="assistant", content="A reply"),
        ])
        orchestrator._graph_memory_for_request("why not?", history, "local-user")
        self.assertIn("renderer", orchestrator.brain_graph.relevant_summary.call_args.args[0])
        orchestrator._graph_memory_for_request("Explain a banana", history, "local-user")
        self.assertEqual(orchestrator.brain_graph.relevant_summary.call_args.args[0], "Explain a banana")

    def test_empty_memory_does_not_fall_back_to_scaffold(self):
        graph = InMemoryBrainGraphStore()
        self.assertEqual(graph.relevant_summary("banana", user_id="local-user"), [])
        self.assertEqual(graph.relevant_summary("banana", limit=0), [])
