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
    permission: str = "read"
    parameters: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "mode": "confirm" if self.requires_confirmation else "auto",
            "permission": self.permission,
            "parameters": dict(self.parameters or {}),
        }


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "web_search": ToolDefinition(
        "web_search",
        "联网搜索",
        "按当前请求搜索公开网页，返回最多五条标题、摘要和来源。搜索词只取自当前请求。",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    ),
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
    "list_todos": ToolDefinition(
        "list_todos",
        "查询个人待办",
        "查询当前请求用户自己的待办。不得查询或推断其他用户的待办；返回结果属于私有数据，只有在当前用户明确要求时才可在回复中概括。",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["all", "open", "completed"]},
                "date_filter": {
                    "type": "string",
                    "enum": ["all", "today", "upcoming", "overdue", "undated"],
                },
                "priority": {"type": "string", "enum": ["all", "low", "normal", "high"]},
                "query": {"type": "string", "maxLength": 200},
                "sort": {"type": "string", "enum": ["due", "priority"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    ),
    "get_todo": ToolDefinition(
        "get_todo",
        "查看个人待办详情",
        "读取当前请求用户自己的一条待办详情。",
        parameters={
            "type": "object",
            "properties": {"todo_id": {"type": "integer", "minimum": 1}},
            "required": ["todo_id"],
            "additionalProperties": False,
        },
    ),
    "create_todo": ToolDefinition(
        "create_todo",
        "创建个人待办",
        "为当前请求用户创建待办。所有者由服务端绑定，模型不得指定其他用户。",
        permission="write",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {"type": "string", "maxLength": 4000},
                "due_at": {"type": ["number", "null"]},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                "source_message_id": {"type": ["integer", "null"]},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    ),
    "update_todo": ToolDefinition(
        "update_todo",
        "更新个人待办",
        "更新当前请求用户自己的一条待办。应先查询并用 expected_title 校验目标任务。",
        permission="write",
        parameters={
            "type": "object",
            "properties": {
                "todo_id": {"type": "integer", "minimum": 1},
                "expected_title": {"type": "string", "minLength": 1, "maxLength": 200},
                "expected_version": {"type": "integer", "minimum": 1},
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {"type": "string", "maxLength": 4000},
                "due_at": {"type": ["number", "null"]},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            "required": ["todo_id", "expected_title", "expected_version"],
            "additionalProperties": False,
        },
    ),
    "complete_todo": ToolDefinition(
        "complete_todo",
        "完成个人待办",
        "把当前用户的一条待办标记为完成。",
        permission="write",
        parameters={
            "type": "object",
            "properties": {
                "todo_id": {"type": "integer", "minimum": 1},
                "expected_title": {"type": "string", "minLength": 1, "maxLength": 200},
                "expected_version": {"type": "integer", "minimum": 1},
            },
            "required": ["todo_id", "expected_title", "expected_version"],
            "additionalProperties": False,
        },
    ),
    "reopen_todo": ToolDefinition(
        "reopen_todo",
        "恢复个人待办",
        "把当前用户的一条已完成待办恢复为未完成。",
        permission="write",
        parameters={
            "type": "object",
            "properties": {
                "todo_id": {"type": "integer", "minimum": 1},
                "expected_title": {"type": "string", "minLength": 1, "maxLength": 200},
                "expected_version": {"type": "integer", "minimum": 1},
            },
            "required": ["todo_id", "expected_title", "expected_version"],
            "additionalProperties": False,
        },
    ),
    "delete_todo": ToolDefinition(
        "delete_todo",
        "删除个人待办",
        "删除当前用户的一条待办。必须由当前用户明确确认后执行。",
        requires_confirmation=True,
        permission="write",
        parameters={
            "type": "object",
            "properties": {
                "todo_id": {"type": "integer", "minimum": 1},
                "expected_title": {"type": "string", "minLength": 1, "maxLength": 200},
                "expected_version": {"type": "integer", "minimum": 1},
            },
            "required": ["todo_id", "expected_title", "expected_version"],
            "additionalProperties": False,
        },
    ),
}

DEFAULT_AGENT_TOOL_NAMES = tuple(name for name in TOOL_DEFINITIONS if name != "web_search")
CONFIGURABLE_AGENT_TOOL_NAMES = tuple(TOOL_DEFINITIONS)


def require_tool(name: str) -> ToolDefinition:
    definition = TOOL_DEFINITIONS.get(str(name))
    if not definition:
        raise ValueError("未知工具")
    return definition


def tool_catalog() -> list[dict[str, Any]]:
    return [definition.public_dict() for definition in TOOL_DEFINITIONS.values()]


def model_tool_specs(names: Any) -> list[dict[str, Any]]:
    """Return native function-calling schemas for an authorized tool subset."""

    checked = validate_tool_names(names)
    result: list[dict[str, Any]] = []
    for name in checked:
        definition = TOOL_DEFINITIONS[name]
        if definition.parameters is None:
            continue
        result.append(
            {
                "name": name,
                "description": definition.description,
                "parameters": dict(definition.parameters),
            }
        )
    return result


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
