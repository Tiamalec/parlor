"""Typed client-side models shared by the legacy and V2 interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ConversationKey:
    kind: str
    identifier: str

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
    messages: dict[ConversationKey, list[dict[str, Any]]] = field(default_factory=dict)
    search_results: dict[ConversationKey, list[dict[str, Any]]] = field(default_factory=dict)
    users: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    members_by_group: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    group_permissions: dict[str, dict[str, bool]] = field(default_factory=dict)
    schedules_by_group: dict[str, dict[str, Any]] = field(default_factory=dict)
    avatar_cache: dict[int, dict[str, Any]] = field(default_factory=dict)
    activities: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    system_status: dict[str, Any] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)
    last_error: str = ""

