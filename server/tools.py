"""Server-side tool catalog shared by the Agent runtime and management APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    label: str
    description: str
    requires_confirmation: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "mode": "confirm" if self.requires_confirmation else "auto",
        }


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "read_conversation_messages": ToolDefinition(
        "read_conversation_messages",
        "读取会话消息",
        "读取当前用户有权访问的会话消息。",
    ),
    "search_conversation_messages": ToolDefinition(
        "search_conversation_messages",
        "搜索会话消息",
        "在当前授权会话内按关键词检索消息。",
    ),
    "search_agent_memory": ToolDefinition(
        "search_agent_memory",
        "检索 Agent 记忆",
        "检索当前用户、群组和 Agent 隔离范围内的长期记忆。",
    ),
    "save_agent_memory": ToolDefinition(
        "save_agent_memory",
        "保存 Agent 记忆",
        "仅在用户或群主明确开启记忆后保存授权范围内的长期记忆。",
        requires_confirmation=True,
    ),
    "save_agent_result": ToolDefinition(
        "save_agent_result",
        "发送 Agent 回复",
        "把 Agent 结果写回当前授权会话。",
    ),
    "send_agent_sticker": ToolDefinition(
        "send_agent_sticker",
        "发送表情包",
        "从管理员导入的本地表情包库向当前会话发送媒体消息。",
    ),
    "summarize_agent_context": ToolDefinition(
        "summarize_agent_context",
        "压缩历史上下文",
        "把较早的会话内容压缩为可复用的摘要，避免上下文无限增长。",
    ),
}

DEFAULT_AGENT_TOOL_NAMES = tuple(TOOL_DEFINITIONS)


def require_tool(name: str) -> ToolDefinition:
    definition = TOOL_DEFINITIONS.get(str(name))
    if not definition:
        raise ValueError("未知工具")
    return definition


def tool_catalog() -> list[dict[str, Any]]:
    return [definition.public_dict() for definition in TOOL_DEFINITIONS.values()]


def validate_tool_names(names: Any) -> list[str]:
    if not isinstance(names, (list, tuple, set)):
        raise ValueError("工具权限必须是名称列表")
    checked: list[str] = []
    for raw in names:
        name = str(raw)
        require_tool(name)
        if name not in checked:
            checked.append(name)
    return checked
