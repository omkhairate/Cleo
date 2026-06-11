from assistant_core.connectors.base import ConnectorDefinition


class ConnectorRegistry:
    """Central place to define and discover supported integrations."""

    def __init__(self) -> None:
        self._connectors = [
            ConnectorDefinition(
                key="google",
                name="Google Workspace",
                description="Gmail, Calendar, Drive, and Docs access.",
            ),
            ConnectorDefinition(
                key="notion",
                name="Notion",
                description="Pages, databases, notes, and project context.",
            ),
            ConnectorDefinition(
                key="github",
                name="GitHub",
                description="Repositories, issues, pull requests, and CI status.",
            ),
            ConnectorDefinition(
                key="slack",
                name="Slack",
                description="Messages, channels, reminders, and summaries.",
            ),
            ConnectorDefinition(
                key="filesystem",
                name="Local Filesystem",
                description="Files, folders, notes, and generated outputs on your machine.",
                auth_required=False,
            ),
            ConnectorDefinition(
                key="argus",
                name="Argus",
                description="Local episodic memory from wearable camera, timeline context, and graph sync.",
                auth_required=False,
            ),
            ConnectorDefinition(
                key="mcp",
                name="MCP Tool Servers",
                description="External tools exposed through the Model Context Protocol.",
                auth_required=False,
            ),
        ]

    def list(self) -> list[ConnectorDefinition]:
        return list(self._connectors)
