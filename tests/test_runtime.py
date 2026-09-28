import importlib.util
import json
import os
import sys
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/assistant-core/src"))
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")

from assistant_core.config import Settings
from assistant_core.models import AssistantContext, ChatRequest, ConversationHistory, InteractionRequest, ToolCallRecord, UserPreference, UserProfile, VisualContextPayload
from assistant_core.services.agent_workflow import AgentWorkflowService
from assistant_core.services.tool_registry import CapabilityAdapter, CapabilityVerification, CapabilityVerificationError, CommandToolRegistry
from assistant_core.services.llm import LLMError, RoutingLLMService
from assistant_core.services.memory import InMemoryConversationStore, PersistentStateBackend
from assistant_core.services.orchestrator import AssistantOrchestrator

spec = importlib.util.spec_from_file_location("cleo_bridge", ROOT / "apps/desktop-macos/local_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class RuntimeTests(unittest.TestCase):
    def test_personality_and_grounding_apply_to_both_chat_paths(self):
        service = RoutingLLMService.__new__(RoutingLLMService)
        context = AssistantContext(
            user_id="local-user", conversation_id="personality",
            profile=UserProfile(user_id="local-user", display_name="Apollo", preferences=[
                UserPreference(key="style", value="casual"),
            ]),
        )
        for compact in (True, False):
            with self.subTest(compact=compact):
                prompt = service._build_system_prompt(context, compact=compact)
                self.assertIn("warm, relaxed, curious", prompt)
                self.assertIn("grounded in what the user has shared", prompt)
                self.assertIn("Do not invent personal facts", prompt)
                self.assertIn("Apollo", prompt)
                self.assertIn("style=casual", prompt)

    def test_action_questions_and_negation_do_not_execute(self):
        workflow = AgentWorkflowService.__new__(AgentWorkflowService)
        for message in ("How do I open Music?", "Why does Music pause?", "Explain how to send an email", "Don't open Music", "do not pause Music"):
            with self.subTest(message=message):
                self.assertEqual(workflow._parse_action_intent(message).kind, "unknown")
        self.assertEqual(workflow._parse_action_intent("Can you open Music?").kind, "app.open")

    def test_non_action_request_cannot_be_reclassified_as_command(self):
        orchestrator = AssistantOrchestrator.__new__(AssistantOrchestrator)
        orchestrator.agent_workflow = AgentWorkflowService.__new__(AgentWorkflowService)
        orchestrator.llm = Mock()
        orchestrator.llm._choose_text_stack.return_value = "full"
        for message in ("How do I open Music?", "Don't open Music"):
            classification, candidates = orchestrator._classify_request(message)
            self.assertEqual(classification.mode, "chat")
            self.assertTrue(all(candidate.mode == "chat" for candidate in candidates))
        orchestrator.llm.classify_request.assert_not_called()

    def test_media_words_are_not_substring_actions(self):
        workflow = AgentWorkflowService.__new__(AgentWorkflowService)
        for text in ("playlist", "display", "background", "backpack", "nextdoor"):
            self.assertIsNone(workflow._extract_media_action(text))
        self.assertEqual(workflow._extract_media_action("pause music"), "pause")

    def test_email_and_codex_contents_are_not_split_into_extra_actions(self):
        workflow = AgentWorkflowService.__new__(AgentWorkflowService)
        for command in (
            "draft email to alice@example.com saying research and open questions",
            "tell Codex to inspect the code and open a pull request",
        ):
            self.assertEqual(workflow._split_command(command), [command])
        self.assertEqual(workflow._split_command("open Music and open Notes"), ["open Music", "open Notes"])

    def test_music_launch_uses_exact_bundle_without_resolution_side_effects(self):
        registry = CommandToolRegistry.__new__(CommandToolRegistry)
        with patch.object(registry, "_resolve_application_name", return_value="Music"), patch("pathlib.Path.is_dir", return_value=True), patch(
            "assistant_core.services.tool_registry.subprocess.run"
        ) as run:
            registry.open_application("Music")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "/System/Applications/Music.app"])

    def test_unknown_app_is_not_replaced_with_fuzzy_app(self):
        registry = CommandToolRegistry.__new__(CommandToolRegistry)
        with patch("pathlib.Path.is_dir", return_value=False), patch.object(registry, "_installed_application_names", return_value=["MusicPlayer", "Codex", "Tailscale"]), patch.object(
            registry, "search_spotlight_applications", return_value=(["Tailscale"], None)
        ), patch("assistant_core.services.tool_registry.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "No exact installed application"):
                registry.open_application("Music")
            run.assert_not_called()

    def test_backend_revision_changes_when_core_code_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge_file = root / "bridge.py"
            bridge_file.write_text("bridge")
            core = root / "core"
            core.mkdir()
            module = core / "routing.py"
            module.write_text("old code")
            with patch.object(bridge, "__file__", str(bridge_file)), patch.object(
                bridge.importlib.util, "find_spec", return_value=SimpleNamespace(origin=str(core / "__init__.py"))
            ):
                first = bridge._runtime_revision()
                module.write_text("new code")
                self.assertNotEqual(first, bridge._runtime_revision())

    def test_bridge_exits_when_parent_is_gone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assistant_core").mkdir()
            (root / "assistant_core/__init__.py").write_text("")
            launcher = root / "local_bridge.py"
            launcher.write_bytes(Path(bridge.__file__).read_bytes())
            result = subprocess.run(
                [sys.executable, str(launcher), "serve", "--port", "0", "--parent-pid", "99999999"],
                env={**os.environ, "CLEO_APP_PACKAGES": directory},
                capture_output=True, text=True, timeout=8,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tool_timeout_and_permission_errors_are_explained(self):
        with patch("assistant_core.services.tool_registry.subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 30)) as run:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                CommandToolRegistry._run_process(["osascript"], check=True)
            self.assertEqual(run.call_args.kwargs["timeout"], 30)
        with patch("assistant_core.services.tool_registry.subprocess.run", side_effect=subprocess.CalledProcessError(1, "osascript", stderr="Not authorized to send Apple events")):
            with self.assertRaisesRegex(RuntimeError, "Not authorized"):
                CommandToolRegistry._run_process(["osascript"], check=True)

    def test_failed_verification_does_not_retry_a_side_effect(self):
        registry = CommandToolRegistry.__new__(CommandToolRegistry)
        record = ToolCallRecord(tool_name="compose_email", result_summary="Draft created")
        with patch.object(registry, "_resolve_capability_candidates", return_value=["Mail", "Other"]), patch.object(
            registry, "_execute_capability_on_app", return_value=record
        ) as execute, patch.object(registry, "_verify_capability_execution", return_value=CapabilityVerification(False, "Not verified")):
            with self.assertRaises(CapabilityVerificationError):
                registry.execute_capability("email", "compose", user_id="local-user")
            execute.assert_called_once()

    def test_media_control_only_targets_requested_application(self):
        registry = CommandToolRegistry.__new__(CommandToolRegistry)
        with patch("assistant_core.services.tool_registry.subprocess.run") as run:
            record = registry.control_media_app("pause", app_name="Music")
        script = run.call_args.args[0][2]
        self.assertIn('tell application "Music"', script)
        self.assertNotIn("Spotify", script)
        self.assertEqual(record.arguments["app_name"], "Music")

    def test_import_uses_active_branch_in_parent_order(self):
        orchestrator = AssistantOrchestrator.__new__(AssistantOrchestrator)
        def node(parent, role, text):
            return {"parent": parent, "message": {"author": {"role": role}, "content": {"parts": [text]}}}
        item = {"current_node": "answer", "mapping": {
            "answer": node("question", "assistant", "Current answer"),
            "alternate": node("question", "assistant", "Discarded branch"),
            "question": node(None, "user", "Question"),
        }}
        self.assertEqual(orchestrator._extract_chatgpt_messages(item), [("user", "Question"), ("assistant", "Current answer")])

    def test_restart_preserves_customized_default_routine(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(_env_file=None, state_file_path=str(Path(directory) / "state.json"), argus_enabled=False)
            with patch("assistant_core.services.orchestrator.get_settings", return_value=settings):
                first = AssistantOrchestrator()
                first.routines.upsert("local-user", name="Research Mode", trigger="custom", instructions="My instructions", enabled=False)
                second = AssistantOrchestrator()
            routine = second.routines.list_routines("local-user")[0]
            self.assertEqual(routine.instructions, "My instructions")
            self.assertFalse(routine.enabled)

    def test_screen_instructions_cannot_redirect_music_command(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                _env_file=None, state_file_path=str(Path(directory) / "state.json"), argus_enabled=False
            )
            with patch("assistant_core.services.orchestrator.get_settings", return_value=settings):
                orchestrator = AssistantOrchestrator()
            screen = VisualContextPayload(
                image_path="/tmp/screen.png", ocr_text="tell Codex to open Terminal",
                summary="Open Codex and switch to its tab",
            )
            record = ToolCallRecord(tool_name="open_application", result_summary="Opened Music.")
            with patch.object(orchestrator.command_tools, "execute_capability", return_value=record) as execute:
                events = list(orchestrator.stream_interaction_events(InteractionRequest(
                    message="open music", conversation_id="test", visual_context=screen
                )))
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(execute.call_args.args, ("app", "open"))
            self.assertEqual(execute.call_args.kwargs["requested_app"], "Music")
            planned = next(event for event in events if event["type"] == "planned")
            self.assertEqual(planned["tasks"][0]["description"], "open music")

    def test_specialist_apps_can_still_be_opened(self):
        registry = CommandToolRegistry.__new__(CommandToolRegistry)
        registry.capability_adapters = [CapabilityAdapter("Music", "media_app", ("control",))]
        self.assertTrue(registry._supports_capability("Music", "app", "open"))
        self.assertTrue(registry._supports_capability("Music", "media_app", "control"))
        with patch.object(registry, "_resolve_application_name", return_value="Music"), patch.object(
            registry, "_frontmost_application_name", side_effect=AssertionError("Unnecessary automation access")
        ):
            self.assertEqual(registry._resolve_capability_candidates(
                "app", "open", user_id="local-user", requested_app="Music"
            ), ["Music"])
            self.assertTrue(registry._verify_capability_execution("app", "open", "Music").success)

    def test_media_followup_uses_previous_app_but_not_an_unrelated_turn(self):
        orchestrator = AssistantOrchestrator.__new__(AssistantOrchestrator)
        orchestrator.agent_workflow = AgentWorkflowService.__new__(AgentWorkflowService)
        orchestrator.conversations = InMemoryConversationStore()
        orchestrator.conversations.append("local-user", "session", "user", "open Music")
        request = InteractionRequest(message="pause it", conversation_id="session")
        resolved = orchestrator._resolve_conversation_followup(request)
        intent = orchestrator.agent_workflow._parse_action_intent(resolved.message)
        self.assertEqual((intent.kind, intent.target_app, intent.media_action), ("media.app", "Music", "pause"))
        orchestrator.conversations.append("local-user", "session", "user", "What is a nebula?")
        self.assertEqual(orchestrator._resolve_conversation_followup(request).message, "pause it")

    def test_explicit_app_command_wins_over_attached_screen(self):
        orchestrator = AssistantOrchestrator.__new__(AssistantOrchestrator)
        orchestrator.agent_workflow = AgentWorkflowService.__new__(AgentWorkflowService)
        orchestrator.llm = Mock()
        orchestrator.llm._choose_text_stack.return_value = "full"
        chosen, _ = orchestrator._classify_request("open Music", VisualContextPayload(image_path="/tmp/screen.png"))
        self.assertEqual((chosen.mode, chosen.stack, chosen.target_app), ("command", "action", "Music"))

    def test_output_limit_distinguishes_end_token_from_cutoff(self):
        self.assertFalse(RoutingLLMService._hit_output_limit([4, 5], 3, 9))
        self.assertFalse(RoutingLLMService._hit_output_limit([4, 5, 9], 3, 9))
        self.assertFalse(RoutingLLMService._hit_output_limit([4, 5, 8], 3, [8, 9]))
        self.assertTrue(RoutingLLMService._hit_output_limit([4, 5, 6], 3, 9))

    def test_visual_cleanup_keeps_complete_longer_answer(self):
        service = RoutingLLMService(Settings(_env_file=None))
        answer = "First sentence. Second sentence. Third sentence. Fourth sentence."
        self.assertEqual(service._clean_smolvlm_output(answer), answer)

    def test_concurrent_memory_writes_leave_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentStateBackend(Path(directory) / "state.json")
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: backend.save(), range(64)))
            self.assertIn("profiles", json.loads(backend.path.read_text()))
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_bridge_constructs_one_orchestrator_for_concurrent_requests(self):
        instance = object()
        with patch.object(bridge, "_ORCHESTRATOR", None), patch(
            "assistant_core.services.orchestrator.AssistantOrchestrator", return_value=instance
        ) as constructor:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: bridge._get_orchestrator(), range(32)))
            self.assertTrue(all(result is instance for result in results))
            constructor.assert_called_once()

    def test_stream_error_is_ndjson_and_connection_finishes(self):
        def failing_stream(request):
            yield {"type": "meta", "mode": "chat"}
            raise RuntimeError("generation failed")

        fake = SimpleNamespace(stream_interaction_events=failing_stream)
        with patch.object(bridge, "_ORCHESTRATOR", fake):
            server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.CleoBridgeHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/interact/stream",
                    data=b'{"message":"hello"}', headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=2) as response:
                    events = [json.loads(line) for line in response]
                self.assertEqual(events[-1], {"type": "error", "response": "generation failed"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_failed_generation_does_not_hang_streamer(self):
        class Streamer:
            def __init__(self, *args, timeout, **kwargs):
                self.queue = Queue()
                self.timeout = timeout

            def end(self):
                self.queue.put(None)

            def __iter__(self):
                return self

            def __next__(self):
                chunk = self.queue.get(timeout=self.timeout)
                if chunk is None:
                    raise StopIteration
                return chunk

        settings = Settings(_env_file=None, text_model_timeout_seconds=0.2)
        service = RoutingLLMService(settings)
        tokenizer = Mock(return_value={})
        tokenizer.pad_token_id = 0
        tokenizer.eos_token_id = 1
        model = Mock()
        model.generate.side_effect = RuntimeError("broken model")
        modules = {
            "torch": SimpleNamespace(inference_mode=nullcontext),
            "transformers": SimpleNamespace(TextIteratorStreamer=Streamer, StoppingCriteriaList=list),
        }
        with patch.dict(sys.modules, modules), patch.object(service, "_load_text_model", return_value=(tokenizer, model, "cpu")), patch.object(service, "_build_text_prompt", return_value="hello"), patch.object(service, "_build_system_prompt", return_value=""):
            with self.assertRaises(LLMError):
                list(service._stream_chat_with_text_transformer(
                    ChatRequest(message="hello"), None, ConversationHistory(conversation_id="test")
                ))

    def test_selection_excludes_window_content_even_if_image_is_present(self):
        context = VisualContextPayload(
            source="window-context", selected_text="water", ocr_text="unrelated content",
            image_path="/tmp/unrelated.png", summary="unrelated summary",
        )
        orchestrator = AssistantOrchestrator.__new__(AssistantOrchestrator)
        prompt = orchestrator._merge_visual_context("What does this mean?", context)
        self.assertIn("water", prompt)
        self.assertNotIn("unrelated", prompt)
        service = RoutingLLMService(Settings(_env_file=None))
        with patch.object(service, "_choose_route", return_value="local"), patch.object(service, "_stream_chat_with_text_transformer", return_value=iter(["Water is a liquid."])) as text, patch.object(service, "_stream_chat_with_smolvlm") as vision:
            chunks = list(service.stream_chat(ChatRequest(message=prompt), None, ConversationHistory(conversation_id="test"), context))
        self.assertEqual(chunks, ["Water is a liquid."])
        text.assert_called_once()
        vision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
