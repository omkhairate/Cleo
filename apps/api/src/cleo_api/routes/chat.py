import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from assistant_core.models import (
    BrainGraph,
    ChatReply,
    ChatRequest,
    ChatGPTImportReply,
    ChatGPTImportRequest,
    CommandReply,
    CommandRequest,
    ConnectorSummary,
    ConversationHistory,
    ImportHistoryEntry,
    InteractionReply,
    InteractionRequest,
    UserProfile,
    UserProfileUpdate,
)
from assistant_core.services.orchestrator import AssistantOrchestrator


router = APIRouter()
orchestrator = AssistantOrchestrator()


@router.post("/chat", response_model=ChatReply)
def chat(request: ChatRequest) -> ChatReply:
    return orchestrator.reply(request)


@router.post("/interact", response_model=InteractionReply)
def interact(request: InteractionRequest) -> InteractionReply:
    return orchestrator.interact(request)


@router.post("/interact/stream")
def interact_stream(request: InteractionRequest) -> StreamingResponse:
    def event_stream():
        for event in orchestrator.stream_interaction_events(request):
            yield json.dumps(event) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson; charset=utf-8",
    )


@router.post("/command", response_model=CommandReply)
def command(request: CommandRequest) -> CommandReply:
    return orchestrator.run_command(request)


@router.post("/chat/stream")
def stream_chat(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        orchestrator.stream_reply(request),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/connectors", response_model=list[ConnectorSummary])
def list_connectors() -> list[ConnectorSummary]:
    return orchestrator.list_connectors()


@router.get("/brain-graph", response_model=BrainGraph)
def brain_graph() -> BrainGraph:
    return orchestrator.get_brain_graph()


@router.get("/model-status")
def model_status() -> dict[str, str]:
    return orchestrator.get_model_status()


@router.get("/conversations/{conversation_id}", response_model=ConversationHistory)
def get_conversation(
    conversation_id: str,
    user_id: str = "local-user",
) -> ConversationHistory:
    return orchestrator.get_conversation_history(user_id, conversation_id)


@router.delete("/conversations/{conversation_id}")
def clear_conversation(
    conversation_id: str,
    user_id: str = "local-user",
) -> dict[str, str]:
    orchestrator.clear_conversation_history(user_id, conversation_id)
    return {"status": "cleared", "conversation_id": conversation_id}


@router.get("/profile", response_model=UserProfile)
def get_profile(user_id: str = "local-user") -> UserProfile:
    return orchestrator.get_profile(user_id)


@router.patch("/profile", response_model=UserProfile)
def update_profile(
    update: UserProfileUpdate,
    user_id: str = "local-user",
) -> UserProfile:
    return orchestrator.update_profile(user_id, update)


@router.post("/imports/chatgpt", response_model=ChatGPTImportReply)
def import_chatgpt_export(request: ChatGPTImportRequest) -> ChatGPTImportReply:
    return orchestrator.import_chatgpt_export(request)


@router.get("/imports/chatgpt/history", response_model=list[ImportHistoryEntry])
def import_chatgpt_history() -> list[ImportHistoryEntry]:
    return orchestrator.get_import_history()
