import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/assistant-core/src"))
from assistant_core.models import ChatRequest, InteractionRequest
from assistant_core.services.feature_facts import PULSE_FACTS, feature_answer
from assistant_core.services.memory import InMemoryConversationStore
from assistant_core.services.orchestrator import AssistantOrchestrator


class FeatureFactsTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator = AssistantOrchestrator.__new__(AssistantOrchestrator)
        self.orchestrator.conversations = InMemoryConversationStore()
        self.orchestrator.llm = Mock()

    def test_pulse_description_is_verified_not_biometric(self):
        for question in ("what does your pulse thingie do?", "Explain Cleo Pulse", "Does your Pulse measure my heart rate?"):
            self.assertEqual(feature_answer(question), PULSE_FACTS)
        self.assertIn("does not measure your heart rate", PULSE_FACTS)

    def test_unrelated_health_questions_and_commands_are_not_intercepted(self):
        for message in ("What is my pulse?", "Explain pulse oximeters", "What does Cleo think of pulse oximeters?", "open Pulse", "open Music"):
            self.assertIsNone(feature_answer(message))

    def test_fast_and_reviewed_bypass_model_for_product_facts(self):
        for mode in ("fast", "reviewed"):
            result = self.orchestrator.interact(InteractionRequest(message="What does your Pulse do?", response_mode=mode, conversation_id=mode))
            self.assertEqual(result.response, PULSE_FACTS)
            self.assertEqual(result.provider, "verified-product-facts")
            history = self.orchestrator.conversations.get_history("local-user", mode)
            self.assertEqual(history.messages[-1].content, PULSE_FACTS)
        self.orchestrator.llm.chat.assert_not_called()

    def test_stream_delivers_the_expected_ui_contract(self):
        events = list(self.orchestrator.stream_interaction_events(InteractionRequest(message="what does your pulse thingie do?", response_mode="reviewed")))
        self.assertEqual([event["type"] for event in events], ["meta", "delta", "final"])
        self.assertEqual(events[1]["response"], PULSE_FACTS)
        self.assertEqual(events[2]["response"], PULSE_FACTS)
        self.orchestrator.llm.stream_chat.assert_not_called()

    def test_direct_chat_and_stream_use_the_same_feature_facts(self):
        request = ChatRequest(message="Explain Cleo Pulse")
        self.assertEqual(self.orchestrator.reply(request).reply, PULSE_FACTS)
        self.assertEqual("".join(self.orchestrator.stream_reply(request)), PULSE_FACTS)
