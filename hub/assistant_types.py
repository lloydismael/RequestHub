from dataclasses import dataclass
from typing import Literal, Mapping, TypeAlias


@dataclass(frozen=True)
class SourceRef:
    key: str
    title: str
    revision: str
    kind: Literal["guide", "department", "directory"]


@dataclass(frozen=True)
class DestinationCard:
    key: str
    title: str
    description: str
    url: str


@dataclass(frozen=True)
class MemberSummary:
    display_name: str
    department: str
    application_role: str


@dataclass(frozen=True)
class DepartmentSummary:
    key: str
    name: str
    summary: str | None
    responsibilities: tuple[str, ...]
    member_count: int


@dataclass(frozen=True)
class MemberPage:
    items: tuple[MemberSummary, ...]
    page: int
    page_size: int
    total: int
    department_key: str | None = None
    query: str = ""


@dataclass(frozen=True)
class Guide:
    key: str
    title: str
    purpose: str
    steps: tuple[str, ...]
    destination_keys: tuple[str, ...]
    source: SourceRef


@dataclass(frozen=True)
class AssistantReply:
    text: str
    destinations: tuple[DestinationCard, ...]
    departments: tuple[DepartmentSummary, ...]
    members: MemberPage | None
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ProviderTurn:
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    source_keys: tuple[str, ...]
    usage: dict[str, int] | None


@dataclass(frozen=True)
class ConversationState:
    id: str
    generation: int
    exchanges: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class SystemMessage:
    content: str
    role: Literal["system"] = "system"


@dataclass(frozen=True)
class UserMessage:
    content: str
    role: Literal["user"] = "user"


@dataclass(frozen=True)
class AssistantMessage:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    role: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class ToolMessage:
    tool_call_id: str
    content: str
    role: Literal["tool"] = "tool"


ProviderMessage: TypeAlias = (
    SystemMessage | UserMessage | AssistantMessage | ToolMessage
)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments_schema: Mapping[str, object]


@dataclass(frozen=True)
class ToolResult:
    text: str
    destinations: tuple[DestinationCard, ...] = ()
    departments: tuple[DepartmentSummary, ...] = ()
    members: MemberPage | None = None
    sources: tuple[SourceRef, ...] = ()
    directory_row_count: int = 0
