from dataclasses import dataclass
import base64
import importlib.util
import json
import re
from collections.abc import Iterator
from pathlib import Path
from mimetypes import guess_type

import httpx

from assistant_core.config import Settings
from assistant_core.models import (
    AssistantContext,
    ChatRequest,
    ConversationHistory,
    VisualContextPayload,
)


class LLMError(RuntimeError):
    """Raised when the configured local model runtime cannot fulfill a request."""


@dataclass
class LLMReply:
    content: str
    provider: str
    model: str


class RoutingLLMService:
    """Routes requests between local and optional online model runtimes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._smolvlm_processor = None
        self._smolvlm_model = None
        self._smolvlm_device = None

    def chat(
        self,
        request: ChatRequest,
        context: AssistantContext,
        history: ConversationHistory,
        visual_context: VisualContextPayload | None = None,
    ) -> LLMReply:
        route = self._choose_route(request)
        if visual_context and (visual_context.image_path or visual_context.selected_text) and self._online_ready():
            route = "online"
        if route == "online":
            return self._chat_with_online_provider(request, context, history, visual_context)
        if self.settings.local_model_provider == "ollama":
            return self._chat_with_ollama(request, context, history)
        if self.settings.local_model_provider == "transformers-smolvlm":
            return self._chat_with_smolvlm(request, context, history, visual_context)
        raise LLMError(
            f"Unsupported local model provider '{self.settings.local_model_provider}'. "
            "Use a supported local runtime such as Ollama."
        )

    def stream_chat(
        self,
        request: ChatRequest,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> Iterator[str]:
        route = self._choose_route(request)
        if route == "online":
            yield from self._stream_chat_with_openai_compatible(request, context, history)
            return
        if self.settings.local_model_provider == "ollama":
            yield from self._stream_chat_with_ollama(request, context, history)
            return
        if self.settings.local_model_provider == "transformers-smolvlm":
            yield from self._stream_chat_with_smolvlm(request, context, history)
            return
        raise LLMError(
            f"Unsupported local model provider '{self.settings.local_model_provider}'. "
            "Use a supported local runtime such as Ollama."
        )

    def _choose_route(self, request: ChatRequest) -> str:
        if self.settings.routing_mode == "local-only":
            if not self._local_ready():
                raise LLMError("Local-only routing is enabled, but the local model is not available.")
            return "local"
        if self.settings.routing_mode == "online-only":
            if self._online_ready():
                return "online"
            raise LLMError("Online-only routing is enabled, but the online model is not configured.")
        if self.settings.routing_mode == "hybrid":
            if self._online_ready() and not self._local_ready():
                return "online"
            if self._online_ready() and self._looks_complex(request.message):
                return "online"
            if self._local_ready():
                return "local"
            raise LLMError(
                "Hybrid routing is enabled, but neither a local model nor an online model is available."
            )
        return "local"

    def _looks_complex(self, message: str) -> bool:
        lowered = message.lower()
        complexity_markers = [
            "step-by-step",
            "compare",
            "analyze",
            "research",
            "plan",
            "architecture",
            "cross-app",
            "multi-step",
            "tradeoff",
            "deeply",
            "thorough",
        ]
        return len(message) > 280 or any(marker in lowered for marker in complexity_markers)

    def _online_ready(self) -> bool:
        return bool(self.settings.online_model_enabled and self.settings.online_model_api_key)

    def _local_ready(self) -> bool:
        if self.settings.local_model_provider == "transformers-smolvlm":
            return self._smolvlm_dependencies_ready()
        if self.settings.local_model_provider != "ollama":
            return False
        try:
            response = httpx.get(
                f"{self.settings.local_model_base_url}/api/tags",
                timeout=5.0,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def _chat_with_ollama(
        self,
        request: ChatRequest,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> LLMReply:
        system_prompt = self._build_system_prompt(context)
        payload = {
            "model": self.settings.local_model_id,
            "stream": False,
            "think": "low",
            "messages": self._build_messages(system_prompt, history, request.message),
            "options": {
                "temperature": 0.4,
            },
        }

        try:
            response = httpx.post(
                f"{self.settings.local_model_base_url}/api/chat",
                json=payload,
                timeout=self.settings.local_model_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(
                "Could not reach the local Ollama runtime. "
                "Make sure Ollama is running and that the model has been pulled."
            ) from exc

        data = response.json()
        content = data.get("message", {}).get("content", "").strip()
        if not content:
            raise LLMError("The local model runtime returned an empty response.")

        return LLMReply(
            content=content,
            provider=self.settings.local_model_provider,
            model=self.settings.local_model_id,
        )

    def _chat_with_smolvlm(
        self,
        request: ChatRequest,
        context: AssistantContext,
        history: ConversationHistory,
        visual_context: VisualContextPayload | None = None,
    ) -> LLMReply:
        processor, model, device = self._load_smolvlm()
        prompt = self._build_smolvlm_prompt(
            system_prompt=self._build_system_prompt(context),
            history=history,
            latest_user_message=request.message,
            visual_context=visual_context,
        )
        tokenizer = processor.tokenizer
        end_turn_token_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
        eos_token_id = end_turn_token_id if isinstance(end_turn_token_id, int) and end_turn_token_id >= 0 else tokenizer.eos_token_id
        pad_token_id = tokenizer.pad_token_id or eos_token_id

        try:
            if visual_context and visual_context.image_path:
                model_inputs = processor(
                    text=prompt,
                    images=[visual_context.image_path],
                    return_tensors="pt",
                )
            else:
                model_inputs = processor(
                    text=prompt,
                    return_tensors="pt",
                )
            model_inputs = {key: value.to(device) for key, value in model_inputs.items()}
            outputs = model.generate(
                **model_inputs,
                max_new_tokens=64,
                do_sample=False,
                repetition_penalty=1.15,
                no_repeat_ngram_size=4,
                eos_token_id=eos_token_id,
                pad_token_id=pad_token_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(
                "The local SmolVLM runtime failed while generating a response. "
                "Check that the model weights downloaded correctly and that Transformers, Torch, and Pillow are installed."
            ) from exc

        prompt_length = model_inputs["input_ids"].shape[-1]
        generated = outputs[0][prompt_length:]
        content = self._clean_smolvlm_output(
            processor.decode(generated, skip_special_tokens=True).strip()
        )
        if not content:
            raise LLMError("The local SmolVLM runtime returned an empty response.")

        return LLMReply(
            content=content,
            provider=self.settings.local_model_provider,
            model=self.settings.local_model_id,
        )

    def _stream_chat_with_ollama(
        self,
        request: ChatRequest,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> Iterator[str]:
        system_prompt = self._build_system_prompt(context)
        payload = {
            "model": self.settings.local_model_id,
            "stream": True,
            "think": "low",
            "messages": self._build_messages(system_prompt, history, request.message),
            "options": {
                "temperature": 0.4,
            },
        }

        timeout = httpx.Timeout(
            connect=10.0,
            read=None,
            write=self.settings.local_model_timeout_seconds,
            pool=10.0,
        )

        try:
            with httpx.stream(
                "POST",
                f"{self.settings.local_model_base_url}/api/chat",
                json=payload,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
        except httpx.HTTPError as exc:
            raise LLMError(
                "Could not reach the local Ollama runtime. "
                "Make sure Ollama is running and that the model has been pulled."
            ) from exc

    def _stream_chat_with_smolvlm(
        self,
        request: ChatRequest,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> Iterator[str]:
        reply = self._chat_with_smolvlm(request, context, history)
        words = reply.content.split()
        for index, word in enumerate(words):
            if index:
                yield " "
            yield word

    def _chat_with_online_provider(
        self,
        request: ChatRequest,
        context: AssistantContext,
        history: ConversationHistory,
        visual_context: VisualContextPayload | None = None,
    ) -> LLMReply:
        payload = {
            "model": self.settings.online_model_id,
            "messages": self._build_online_messages(
                self._build_system_prompt(context),
                history,
                request.message,
                visual_context,
            ),
            "temperature": 0.4,
        }
        try:
            response = httpx.post(
                f"{self.settings.online_model_base_url}/chat/completions",
                json=payload,
                headers=self._online_headers(),
                timeout=self.settings.online_model_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError("Could not reach the configured online model provider.") from exc

        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if not content:
            raise LLMError("The configured online model returned an empty response.")
        return LLMReply(
            content=content,
            provider=self.settings.online_model_provider,
            model=self.settings.online_model_id,
        )

    def _stream_chat_with_openai_compatible(
        self,
        request: ChatRequest,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> Iterator[str]:
        payload = {
            "model": self.settings.online_model_id,
            "messages": self._build_messages(
                self._build_system_prompt(context),
                history,
                request.message,
            ),
            "temperature": 0.4,
            "stream": True,
        }
        timeout = httpx.Timeout(
            connect=10.0,
            read=None,
            write=self.settings.online_model_timeout_seconds,
            pool=10.0,
        )
        try:
            with httpx.stream(
                "POST",
                f"{self.settings.online_model_base_url}/chat/completions",
                json=payload,
                headers=self._online_headers(),
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_chunk = line.removeprefix("data: ").strip()
                    if data_chunk == "[DONE]":
                        break
                    try:
                        data = json.loads(data_chunk)
                    except json.JSONDecodeError:
                        continue
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
        except httpx.HTTPError as exc:
            raise LLMError("Could not reach the configured online model provider.") from exc

    def check_health(self) -> dict[str, str]:
        if self.settings.local_model_provider == "transformers-smolvlm":
            local_status = "ok" if self._smolvlm_dependencies_ready() else "missing-dependencies"
            online_status = "configured" if self._online_ready() else "disabled"
            overall_status = "ok" if local_status == "ok" else "unavailable"
            return {
                "status": overall_status,
                "routing_mode": self.settings.routing_mode,
                "local_provider": self.settings.local_model_provider,
                "local_model": self.settings.local_model_id,
                "local_status": local_status,
                "online_provider": self.settings.online_model_provider,
                "online_model": self.settings.online_model_id,
                "online_status": online_status,
            }
        if self.settings.local_model_provider == "ollama":
            local_status = "ok" if self._local_ready() else "unavailable"
            online_status = "configured" if self._online_ready() else "disabled"
            overall_status = "ok" if (
                local_status == "ok"
                or online_status == "configured"
            ) else "unavailable"
            return {
                "status": overall_status,
                "routing_mode": self.settings.routing_mode,
                "local_provider": self.settings.local_model_provider,
                "local_model": self.settings.local_model_id,
                "local_status": local_status,
                "online_provider": self.settings.online_model_provider,
                "online_model": self.settings.online_model_id,
                "online_status": online_status,
            }
        return {
            "status": "unsupported",
            "routing_mode": self.settings.routing_mode,
            "local_provider": self.settings.local_model_provider,
            "local_model": self.settings.local_model_id,
        }

    def _online_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.online_model_api_key:
            headers["Authorization"] = f"Bearer {self.settings.online_model_api_key}"
        return headers

    def _build_online_messages(
        self,
        system_prompt: str,
        history: ConversationHistory,
        latest_user_message: str,
        visual_context: VisualContextPayload | None = None,
    ) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": system_prompt},
        ]
        for message in history.messages:
            messages.append({"role": message.role, "content": message.content})

        user_content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": self._build_visual_user_text(latest_user_message, visual_context),
            },
        ]
        image_url = self._image_data_url(visual_context.image_path) if visual_context else None
        if image_url:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_url,
                    },
                }
            )

        messages.append({"role": "user", "content": user_content})
        return messages

    def _build_visual_user_text(
        self,
        latest_user_message: str,
        visual_context: VisualContextPayload | None,
    ) -> str:
        if not visual_context:
            return latest_user_message

        instructions: list[str] = []
        if visual_context.selected_text:
            instructions.append(
                "The user explicitly selected this text before asking the question: "
                f"'{visual_context.selected_text}'. Answer about that exact selected text first."
            )
        elif visual_context.source == "pointer-context":
            instructions.append(
                "The user is asking about the content at the center of the attached image region."
            )

        instructions.append(f"User request: {latest_user_message}")
        return "\n".join(instructions)

    def _image_data_url(self, image_path: str | None) -> str | None:
        if not image_path:
            return None
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            return None

        mime_type = guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _smolvlm_dependencies_ready(self) -> bool:
        return all(
            importlib.util.find_spec(module_name) is not None
            for module_name in ["torch", "torchvision", "transformers", "PIL"]
        )

    def _load_smolvlm(self):
        if self._smolvlm_processor is not None and self._smolvlm_model is not None and self._smolvlm_device is not None:
            return self._smolvlm_processor, self._smolvlm_model, self._smolvlm_device

        try:
            import torch
            from transformers import (
                AutoTokenizer,
                Idefics3ForConditionalGeneration,
                Idefics3ImageProcessor,
                Idefics3Processor,
            )
        except ImportError as exc:  # pragma: no cover - dependency-driven
            raise LLMError(
                "SmolVLM support requires local Python packages that are not installed yet. "
                "Install torch, torchvision, transformers, and pillow in Cleo's virtualenv."
            ) from exc

        device = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
        dtype = torch.float16 if device == "mps" else torch.float32

        try:
            image_processor = Idefics3ImageProcessor.from_pretrained(self.settings.local_model_id)
            tokenizer = AutoTokenizer.from_pretrained(self.settings.local_model_id)
            processor = Idefics3Processor(
                image_processor=image_processor,
                tokenizer=tokenizer,
            )
            model = Idefics3ForConditionalGeneration.from_pretrained(
                self.settings.local_model_id,
                torch_dtype=dtype,
            )
            model.to(device)
            model.eval()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(
                f"Could not load the local SmolVLM model '{self.settings.local_model_id}'. "
                "Its Idefics3 processor stack may still be missing a runtime dependency such as torchvision, "
                "or the local Transformers install may still be incompatible."
            ) from exc

        self._smolvlm_processor = processor
        self._smolvlm_model = model
        self._smolvlm_device = device
        return processor, model, device

    def _build_smolvlm_prompt(
        self,
        system_prompt: str,
        history: ConversationHistory,
        latest_user_message: str,
        visual_context: VisualContextPayload | None = None,
    ) -> str:
        prompt_parts: list[str] = [
            "<|im_start|>system\n"
            f"{system_prompt}\n"
            "Keep responses short, direct, and do not repeat yourself.<|im_end|>"
        ]
        for message in history.messages:
            role = "assistant" if message.role == "assistant" else "user"
            prompt_parts.append(
                f"<|im_start|>{role}\n{message.content}<|im_end|>"
            )

        user_text = self._build_visual_user_text(latest_user_message, visual_context)
        prompt_parts.append(f"<|im_start|>user\n{user_text}<|im_end|>")
        prompt_parts.append("<|im_start|>assistant\n")
        return "\n".join(prompt_parts)

    def _clean_smolvlm_output(self, content: str) -> str:
        cleaned = content.strip()
        cleaned = re.sub(r"^(assistant|Assistant)\s*:?\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return cleaned

        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        deduped: list[str] = []
        seen_normalized: set[str] = set()
        for sentence in sentences:
            normalized = sentence.strip().lower()
            if not normalized:
                continue
            if normalized in seen_normalized:
                break
            deduped.append(sentence.strip())
            seen_normalized.add(normalized)
            if len(deduped) >= 3:
                break

        if deduped:
            cleaned = " ".join(deduped).strip()

        return cleaned


    def _build_system_prompt(self, context: AssistantContext) -> str:
        preference_lines = [
            f"{item.key}={item.value}" for item in context.profile.preferences
        ]
        workflow_lines = [
            item.pattern for item in context.profile.workflows
        ]
        display_name = context.profile.display_name or context.user_id
        return (
            "You are Cleo, a personal AI assistant that can eventually coordinate tools, memory, "
            "and connectors across many apps. Be concise, practical, and proactive. "
            "When you do not know something, say so. "
            f"User identity: {display_name}. "
            f"Known connector domains: {', '.join(context.relevant_connectors)}. "
            f"Known user preferences: {'; '.join(preference_lines) or 'none yet'}. "
            f"Known workflows: {'; '.join(workflow_lines) or 'none yet'}. "
            f"Graph hints: {'; '.join(context.graph_summary) or 'none yet'}."
        )

    def _build_messages(
        self,
        system_prompt: str,
        history: ConversationHistory,
        latest_user_message: str,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        for message in history.messages:
            messages.append({"role": message.role, "content": message.content})
        messages.append({"role": "user", "content": latest_user_message})
        return messages
