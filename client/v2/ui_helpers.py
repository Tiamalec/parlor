"""Pure helpers shared by V2 pages."""

from __future__ import annotations

from typing import Any
from PySide6.QtWidgets import QVBoxLayout
from .widgets import message_card_kind


def clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def _message_timestamp(message: dict[str, Any]) -> float:
    stamp = float(message.get("created_at", message.get("timestamp", 0)) or 0)
    return stamp / 1000 if stamp > 10_000_000_000 else stamp


def _message_preview(message: dict[str, Any], limit: int = 80) -> str:
    kind = message_card_kind(message)
    if kind == "summary":
        return "[群聊总结]"
    if kind == "todo":
        return "[待办事项]"
    if message.get("message_type") == "sticker":
        return f"[表情包] {message.get('sticker_name') or ''}".strip()
    if message.get("message_type") == "image":
        return "[图片]"
    if message.get("recalled"):
        return "消息已撤回"
    return str(message.get("content") or "")[:limit]


def _message_sender_key(message: dict[str, Any]) -> tuple[str, str]:
    if message.get("sender_kind") == "agent" or message.get("message_type") == "agent":
        return "agent", str(message.get("sender_agent_id") or message.get("sender_name") or "agent")
    return "user", str(message.get("sender_id", ""))


def _messages_are_grouped(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return False
    if previous.get("message_type") == "system" or current.get("message_type") == "system":
        return False
    if message_card_kind(previous) or message_card_kind(current):
        return False
    if _message_sender_key(previous) != _message_sender_key(current):
        return False
    previous_stamp = _message_timestamp(previous)
    current_stamp = _message_timestamp(current)
    return not previous_stamp or not current_stamp or 0 <= current_stamp - previous_stamp <= 300
