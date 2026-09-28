import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from assistant_core.models import (
    BrainGraph,
    ChatReply,
    ChatRequest,
    ContextPack,
    ContextPackBuildRequest,
    ChatGPTImportReply,
    ChatGPTImportRequest,
    CommandReply,
    CommandRequest,
    ConnectorSummary,
    ConversationHistory,
    DeviceUpsertRequest,
    ImportHistoryEntry,
    InteractionReply,
    InteractionRequest,
    LANDevice,
    PersistentRoutine,
    RoutineUpsertRequest,
    SessionMemory,
    TimelineEvent,
    UserProfile,
    UserProfileUpdate,
    WorkspaceMemorySnapshot,
    AppAdapter,
)
from assistant_core.services.orchestrator import AssistantOrchestrator


router = APIRouter()
orchestrator = AssistantOrchestrator()


class ArgusQueryRequest(BaseModel):
    query: str
    limit: int = 8


class ArgusContextRequest(BaseModel):
    text: str
    source: str = "cleo"


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


@router.post("/argus/query")
def argus_query(request: ArgusQueryRequest) -> dict:
    return orchestrator.query_argus_memory(request.query, limit=request.limit)


@router.post("/argus/context")
def argus_context(request: ArgusContextRequest) -> dict:
    return orchestrator.ingest_argus_context(request.text, source=request.source)


@router.post("/argus/sync-graph", response_model=BrainGraph)
def argus_sync_graph() -> BrainGraph:
    return orchestrator.sync_argus_graph()


@router.get("/model-status")
def model_status() -> dict[str, str]:
    return orchestrator.get_model_status()


@router.get("/memory-snapshot", response_model=WorkspaceMemorySnapshot)
def memory_snapshot(user_id: str = "local-user") -> WorkspaceMemorySnapshot:
    return orchestrator.get_workspace_memory_snapshot(user_id)


@router.get("/adapters", response_model=list[AppAdapter])
def list_adapters() -> list[AppAdapter]:
    return orchestrator.list_app_adapters()


@router.get("/devices", response_model=list[LANDevice])
def list_devices(user_id: str = "local-user") -> list[LANDevice]:
    return orchestrator.list_devices(user_id)


@router.post("/devices", response_model=LANDevice)
def upsert_device(request: DeviceUpsertRequest) -> LANDevice:
    return orchestrator.upsert_device(request)


@router.get("/routines", response_model=list[PersistentRoutine])
def list_routines(user_id: str = "local-user") -> list[PersistentRoutine]:
    return orchestrator.list_routines(user_id)


@router.post("/routines", response_model=PersistentRoutine)
def upsert_routine(request: RoutineUpsertRequest) -> PersistentRoutine:
    return orchestrator.upsert_routine(request)


@router.get("/timeline", response_model=list[TimelineEvent])
def timeline(user_id: str = "local-user", limit: int = 40) -> list[TimelineEvent]:
    return orchestrator.get_timeline(user_id, limit=limit)


@router.get("/session", response_model=SessionMemory)
def session_memory(user_id: str = "local-user") -> SessionMemory:
    return orchestrator.get_session_memory(user_id)


@router.delete("/session", response_model=SessionMemory)
def clear_session(user_id: str = "local-user") -> SessionMemory:
    return orchestrator.clear_session_memory(user_id)


@router.get("/context-packs", response_model=list[ContextPack])
def list_context_packs(user_id: str = "local-user") -> list[ContextPack]:
    return orchestrator.list_context_packs(user_id)


@router.post("/context-packs", response_model=ContextPack)
def build_context_pack(request: ContextPackBuildRequest) -> ContextPack:
    return orchestrator.build_context_pack(request)


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
