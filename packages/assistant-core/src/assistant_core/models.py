from __future__ import annotations

from pydantic import BaseModel, Field
from datetime import datetime


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="User message for the assistant.")
    user_id: str | None = Field(default="local-user")
    conversation_id: str | None = Field(default="default")


class ChatReply(BaseModel):
    reply: str
    conversation_id: str
    provider: str | None = None
    model: str | None = None
    used_connectors: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class ConnectorSummary(BaseModel):
    key: str
    name: str
    description: str
    auth_required: bool = True


class BrainGraphNode(BaseModel):
    id: str
    label: str
    kind: str
    group: str
    metadata: dict[str, str] = Field(default_factory=dict)


class BrainGraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    strength: float = 1.0


class BrainGraph(BaseModel):
    nodes: list[BrainGraphNode] = Field(default_factory=list)
    edges: list[BrainGraphEdge] = Field(default_factory=list)


class ConversationMessage(BaseModel):
    role: str
    content: str
    local_only: bool = False


class ConversationHistory(BaseModel):
    conversation_id: str
    messages: list[ConversationMessage] = Field(default_factory=list)


class UserPreference(BaseModel):
    key: str
    value: str
    source: str = "manual"


class UserWorkflow(BaseModel):
    name: str
    pattern: str
    source: str = "inferred"


class UserProfile(BaseModel):
    user_id: str
    display_name: str | None = None
    preferences: list[UserPreference] = Field(default_factory=list)
    workflows: list[UserWorkflow] = Field(default_factory=list)


class UserProfileUpdate(BaseModel):
    display_name: str | None = None
    preferences: dict[str, str] = Field(default_factory=dict)


class AssistantContext(BaseModel):
    user_id: str
    conversation_id: str
    profile: UserProfile
    recent_messages: list[ConversationMessage] = Field(default_factory=list)
    relevant_connectors: list[str] = Field(default_factory=list)
    graph_summary: list[str] = Field(default_factory=list)
    active_goals: list[str] = Field(default_factory=list)
    file_evidence: list[dict[str, str]] = Field(default_factory=list)


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, description="High-level command for Cleo.")
    user_id: str | None = Field(default="local-user")
    conversation_id: str | None = Field(default="command")


class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict[str, str] = Field(default_factory=dict)
    result_summary: str


class AgentTask(BaseModel):
    task_id: str
    title: str
    description: str
    specialist: str
    tool_names: list[str] = Field(default_factory=list)
    status: str = "pending"
    output: str | None = None


class AgentExecution(BaseModel):
    task_id: str
    specialist: str
    status: str
    reasoning: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    output: str


class CommandReply(BaseModel):
    command: str
    conversation_id: str
    summary: str
    final_response: str
    provider: str | None = None
    model: str | None = None
    tasks: list[AgentTask] = Field(default_factory=list)
    executions: list[AgentExecution] = Field(default_factory=list)


class VisualContextPayload(BaseModel):
    source: str = "window-context"
    summary: str | None = None
    selected_text: str | None = None
    ocr_text: str | None = None
    image_path: str | None = None
    region_description: str | None = None


class InteractionRequest(BaseModel):
    message: str = Field(min_length=1, description="User input for Cleo to classify and handle.")
    user_id: str | None = Field(default="local-user")
    conversation_id: str | None = Field(default="auto")
    visual_context: VisualContextPayload | None = None
    response_mode: str = Field(default="fast")


class InteractionReply(BaseModel):
    mode: str
    conversation_id: str
    response: str
    provider: str | None = None
    model: str | None = None
    summary: str | None = None
    tasks: list[AgentTask] = Field(default_factory=list)
    executions: list[AgentExecution] = Field(default_factory=list)
    classification: RequestClassification | None = None
    route_candidates: list[RequestRouteCandidate] = Field(default_factory=list)


class RequestClassification(BaseModel):
    mode: str
    stack: str
    intent: str | None = None
    target_app: str | None = None
    confidence: float = 0.0
    reason: str = ""


class RequestRouteCandidate(BaseModel):
    mode: str
    stack: str
    intent: str | None = None
    target_app: str | None = None
    confidence: float = 0.0
    reason: str = ""


class ChatGPTImportRequest(BaseModel):
    file_path: str = Field(min_length=1, description="Absolute path to a ChatGPT export JSON file.")
    user_id: str | None = Field(default="local-user")


class ChatGPTImportReply(BaseModel):
    file_path: str
    imported_conversations: int
    imported_messages: int
    imported_user_messages: int
    profile_preferences: int
    profile_workflows: int


class ImportHistoryEntry(BaseModel):
    source: str = "chatgpt"
    file_path: str
    imported_at: datetime
    imported_conversations: int
    imported_messages: int
    imported_user_messages: int


class RoutineUpsertRequest(BaseModel):
    name: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    enabled: bool = True
    source: str = "manual"
    user_id: str | None = Field(default="local-user")


class ContextPackBuildRequest(BaseModel):
    file_path: str = Field(min_length=1)
    title: str | None = None
    user_id: str | None = Field(default="local-user")


class AppAdapter(BaseModel):
    key: str
    app_name: str
    status: str = "available"
    description: str
    actions: list[str] = Field(default_factory=list)


class DeviceCapability(BaseModel):
    family: str
    operations: list[str] = Field(default_factory=list)
    transport: str = "http"


class LANDevice(BaseModel):
    device_id: str
    name: str
    device_type: str
    hostname: str | None = None
    ip_address: str | None = None
    status: str = "available"
    trust_state: str = "trusted"
    agent_version: str | None = None
    protocols: list[str] = Field(default_factory=list)
    capabilities: list[DeviceCapability] = Field(default_factory=list)
    notes: str | None = None
    last_seen_at: datetime | None = None
    updated_at: datetime


class DeviceUpsertRequest(BaseModel):
    name: str = Field(min_length=1)
    device_type: str = Field(min_length=1)
    device_id: str | None = None
    hostname: str | None = None
    ip_address: str | None = None
    status: str = "available"
    trust_state: str = "trusted"
    agent_version: str | None = None
    protocols: list[str] = Field(default_factory=list)
    capabilities: list[DeviceCapability] = Field(default_factory=list)
    notes: str | None = None
    user_id: str | None = Field(default="local-user")


class PersistentRoutine(BaseModel):
    routine_id: str
    name: str
    trigger: str
    instructions: str
    enabled: bool = True
    source: str = "manual"
    updated_at: datetime


class TimelineEvent(BaseModel):
    event_id: str
    event_type: str
    title: str
    detail: str | None = None
    app_name: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    recorded_at: datetime


class ContextPack(BaseModel):
    pack_id: str
    title: str
    source_type: str
    root_path: str
    summary: str
    file_count: int = 0
    file_paths: list[str] = Field(default_factory=list)
    created_at: datetime


class SessionMemory(BaseModel):
    user_id: str
    active_goal: str | None = None
    active_app: str | None = None
    active_files: list[str] = Field(default_factory=list)
    active_tasks: list[str] = Field(default_factory=list)
    last_context_pack_id: str | None = None
    updated_at: datetime | None = None


class WorkspaceMemorySnapshot(BaseModel):
    profile: UserProfile
    graph: BrainGraph
    imports: list[ImportHistoryEntry] = Field(default_factory=list)
    adapters: list[AppAdapter] = Field(default_factory=list)
    devices: list[LANDevice] = Field(default_factory=list)
    routines: list[PersistentRoutine] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    context_packs: list[ContextPack] = Field(default_factory=list)
    session: SessionMemory
