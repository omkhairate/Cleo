from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectorDefinition:
    key: str
    name: str
    description: str
    auth_required: bool = True
