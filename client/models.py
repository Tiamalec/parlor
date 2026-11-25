"""Typed client-side models shared by the V2 interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ConversationKey:
    kind: str
    identifier: str

    @property
    def storage_id(self) -> str:
        """Stable local identifier shared by drafts and view caches."""
        return f"{self.kind}:{self.identifier}"

    def peer_user_id(self, current_user_id: int | str | None) -> int | None:
        """Resolve the other participant of a private conversation."""

        if self.kind != "private":
            return None
        parts = self.identifier.split(":")
        try:
            if len(parts) == 3 and parts[0] == "dm" and current_user_id is not None:
                own_id = int(current_user_id)
                first, second = int(parts[1]), int(parts[2])
                return second if first == own_id else first
            return int(self.identifier)
        except (TypeError, ValueError):
            return None

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], current_user_id: int | str | None = None
    ) -> "ConversationKey | None":
        scope = payload.get("scope")
        if isinstance(scope, dict):
            kind = scope.get("type", scope.get("conversation_type"))
            identifier = scope.get("id", scope.get("conversation_id"))
            if kind is not None and identifier is not None:
                return cls(str(kind), str(identifier))
        kind = payload.get("conversation_type", payload.get("scope_type"))
        identifier = payload.get("conversation_id", payload.get("scope_id"))
        if kind is not None and identifier is not None:
            return cls(str(kind), str(identifier))
        if payload.get("group_id") is not None:
            return cls("group", str(payload["group_id"]))
        sender_id = payload.get("sender_id")
        recipient_id = payload.get("recipient_id")
        if sender_id is not None and recipient_id is not None and current_user_id is not None:
            my_id = str(current_user_id)
            peer = recipient_id if str(sender_id) == my_id else sender_id
            return cls("private", str(peer))
        return None


@dataclass(slots=True)
class ConversationSummary:
    key: ConversationKey
    title: str
    preview: str = ""
    timestamp: float = 0
    unread_count: int = 0
    member_role: str = ""
    online: bool | None = None
    can_send: bool = True
    blocked_by_me: bool = False
    peer_last_read_message_id: int = 0


@dataclass(slots=True)
class AppState:
    connected: bool = False
    authenticated: bool = False
    connection_text: str = "正在连接…"
    agent_online: bool = False
    agent_model: str = ""
    current_user: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)
    current_conversation: ConversationKey | None = None
    conversations: list[ConversationSummary] = field(default_factory=list)
    todos: list[dict[str, Any]] = field(default_factory=list)
    todo_revision: int = 0
    todos_loaded: bool = False
    todo_error: str = ""
    todo_needs_refresh: bool = False
    todo_target: dict[str, Any] | None = None
    todo_import_results: dict[int, dict[str, Any]] = field(default_factory=dict)
    messages: dict[ConversationKey, list[dict[str, Any]]] = field(default_factory=dict)
    history_loaded_at: dict[ConversationKey, float] = field(default_factory=dict)
    search_results: dict[ConversationKey, list[dict[str, Any]]] = field(default_factory=dict)
    search_has_more: dict[ConversationKey, bool] = field(default_factory=dict)
    event_cursor: int = 0
    events_has_more: bool = False
    users: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    members_by_group: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    group_permissions: dict[str, dict[str, bool]] = field(default_factory=dict)
    schedules_by_group: dict[str, dict[str, Any]] = field(default_factory=dict)
    avatar_cache: dict[int, dict[str, Any]] = field(default_factory=dict)
    activities: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    agent_runs: dict[int, dict[str, Any]] = field(default_factory=dict)
    agent_notices: dict[ConversationKey, str] = field(default_factory=dict)
    agents: list[dict[str, Any]] = field(default_factory=list)
    agent_models: list[dict[str, Any]] = field(default_factory=list)
    agent_presets: list[dict[str, Any]] = field(default_factory=list)
    agent_tools: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    group_agent_tools: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)
    group_agents: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    memories: dict[tuple[str, str, str], list[dict[str, Any]]] = field(default_factory=dict)
    memory_settings: dict[tuple[str, str, str], bool] = field(default_factory=dict)
    agent_avatar_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    system_status: dict[str, Any] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)
    pending_sends: dict[str, tuple[ConversationKey, str, list[dict[str, Any]], int | None]] = field(default_factory=dict)
    failed_send: tuple[ConversationKey, str, list[dict[str, Any]], int | None] | None = None
    private_opened: dict[ConversationKey, ConversationSummary] = field(default_factory=dict)
    private_states: dict[ConversationKey, dict[str, Any]] = field(default_factory=dict)
    private_typing: dict[ConversationKey, bool] = field(default_factory=dict)
    private_outbox: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_error: str = ""

