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
