"""Render deterministic Parlor UI snapshots for visual review.

Usage:
    QT_QPA_PLATFORM=offscreen python tools/ui_snapshot.py

The script uses fake in-memory state and never starts the network client.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from client.models import AppState, ConversationKey, ConversationSummary
from client.network import NetworkClient
from client.service import ChatService
from client.store import AppStore
from client.v2.pages.auth_page import AuthPage
from client.v2.shell.workspace import Workspace
from client.v2.theme import ThemeMode, install_theme_manager, reset_theme_manager


SIZES = ((720, 520), (860, 600), (1180, 760), (1440, 900))
THEMES = ((ThemeMode.LIGHT, "light"), (ThemeMode.DARK, "dark"))


def _now() -> int:
    return int(time.time())


def build_state() -> AppState:
    group = ConversationKey("group", "7")
    private = ConversationKey("private", "dm:1:2")
    lobby = ConversationKey("group", "1")
    state = AppState()
    state.connected = True
    state.authenticated = True
    state.connection_text = "已连接"
    state.agent_online = True
    state.agent_model = "qwen3.5:4b"
    state.current_user = {
        "id": 1,
        "username": "linzhou",
        "display_name": "林舟",
        "role": "admin",
    }
    state.current_conversation = group
    state.conversations = [
        ConversationSummary(group, "项目组", "TCP 联调完成，服务端使用 asyncio。", _now() - 120, unread_count=3),
        ConversationSummary(private, "周宁", "明早十点一起过一遍演示脚本吗？", _now() - 900, unread_count=1),
        ConversationSummary(lobby, "大厅", "欢迎来到 Parlor。", _now() - 3600),
    ]
    state.messages[group] = [
        {
            "id": 1,
            "conversation_type": "group",
            "conversation_id": "7",
            "sender_id": 2,
            "sender_name": "周宁",
            "message_type": "text",
            "content": "TCP 联调完成，服务端已经切到 asyncio。",
            "created_at": _now() - 420,
        },
        {
            "id": 2,
            "conversation_type": "group",
            "conversation_id": "7",
            "sender_id": 3,
            "sender_name": "陈默",
            "message_type": "text",
            "content": "收到。界面这边今天会把会话切换和断线重连再走一遍。",
            "created_at": _now() - 360,
        },
        {
            "id": 3,
            "conversation_type": "group",
            "conversation_id": "7",
            "sender_id": 1,
            "sender_name": "林舟",
            "message_type": "text",
            "content": "好。先把主链路的视觉收干净，晚上一起看截图。",
            "created_at": _now() - 300,
        },
        {
            "id": 4,
            "conversation_type": "group",
            "conversation_id": "7",
            "sender_id": 0,
            "sender_name": "Parlor Agent",
            "sender_kind": "agent",
            "message_type": "agent",
            "content_format": "markdown",
            "content": "## 今日同步\n\n- 桌面端主链路已跑通\n- 还剩设置页与通讯录的窄屏检查\n- 演示前补一张干净的任务列表截图",
            "created_at": _now() - 240,
        },
    ]
    state.messages[private] = [
        {
            "id": 8,
            "conversation_type": "private",
            "conversation_id": "dm:1:2",
            "sender_id": 2,
            "sender_name": "周宁",
            "message_type": "text",
            "content": "明早十点一起过一遍演示脚本吗？",
            "created_at": _now() - 900,
        }
    ]
    state.members_by_group["7"] = [
        {"id": 1, "username": "林舟", "display_name": "林舟", "kind": "user", "group_role": "owner", "online": True},
        {"id": 2, "username": "周宁", "display_name": "周宁", "kind": "user", "group_role": "member", "online": True},
        {"id": 3, "username": "陈默", "display_name": "陈默", "kind": "user", "group_role": "member", "online": False},
        {"id": "agent", "username": "Parlor Agent", "display_name": "Parlor Agent", "kind": "agent", "group_role": "agent", "online": True},
    ]
    state.group_permissions["7"] = {"can_manage_members": True, "can_manage_schedule": True}
    state.schedules_by_group["7"] = {"group_id": 7, "interval_minutes": 120, "enabled": True}
    state.users = [
        {"id": 2, "username": "zhouning", "display_name": "周宁", "online": True, "role": "user"},
        {"id": 3, "username": "chenmo", "display_name": "陈默", "online": False, "role": "user"},
        {"id": 4, "username": "yue", "display_name": "月见", "online": True, "role": "admin"},
    ]
    state.groups = [
        {"id": 7, "name": "项目组", "member_count": 4, "joined": True, "member_role": "owner"},
        {"id": 1, "name": "大厅", "member_count": 12, "joined": True, "member_role": "member"},
        {"id": 9, "name": "设计评审", "member_count": 6, "joined": False},
    ]
    state.activities = [
        {
            "id": 1,
            "task_type": "summary",
            "conversation_type": "group",
            "conversation_id": "7",
            "requested_by": 1,
            "requested_by_name": "林舟",
            "status": "succeeded",
            "started_at": _now() - 600,
            "finished_at": _now() - 540,
            "result_message_id": 4,
        },
        {
            "id": 2,
            "task_type": "todo",
            "conversation_type": "group",
            "conversation_id": "7",
            "requested_by": 1,
            "requested_by_name": "林舟",
            "status": "failed",
            "started_at": _now() - 300,
            "error": "模型暂时不可用",
        },
    ]
    state.todos = [
        {"id": 1, "title": "补齐窄屏截图", "description": "覆盖 720 与 860 宽度", "completed": False, "priority": "high", "due_at": _now() + 3600, "source_type": "manual", "created_at": _now() - 800, "version": 1},
        {"id": 2, "title": "确认演示账号", "description": "三个客户端同时在线", "completed": False, "priority": "normal", "due_at": _now() + 86400, "source_type": "agent_extracted", "created_at": _now() - 400, "version": 1},
        {"id": 3, "title": "整理 Agent 工具日志", "completed": True, "priority": "low", "source_type": "manual", "created_at": _now() - 2000, "version": 2},
    ]
    state.todos_loaded = True
    state.todo_revision = 1
    state.system_status = {"online_users": 5, "total_users": 18, "groups": 4, "uptime_seconds": 7320}
    state.tool_calls = [
        {"tool_name": "conversation.read", "requested_by_name": "林舟", "conversation_type": "group", "conversation_id": "7", "status": "ok", "result_count": 50},
        {"tool_name": "todo.create", "requested_by_name": "林舟", "conversation_type": "group", "conversation_id": "7", "status": "ok", "result_count": 1},
    ]
    state.agents = [{"id": "agent", "name": "Parlor Agent", "enabled": True}]
    state.tool_definitions = [{"name": name} for name in ("conversation.read", "todo.create", "memory.write")]
    state.memory_settings[("group", "7", "agent")] = True
    return state


def _save(widget: Any, path: Path) -> None:
    widget.repaint()
    QApplication.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    widget.grab().save(str(path), "PNG")


def render(output: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings_dir = tempfile.TemporaryDirectory()
    settings = QSettings(os.path.join(settings_dir.name, "snapshot.ini"), QSettings.Format.IniFormat)
    manager = install_theme_manager(app, settings)
    state = build_state()
    for mode, theme_name in THEMES:
        manager.set_mode(mode)
        app.processEvents()
        for width, height in SIZES:
            size_name = f"{width}x{height}"
            auth = AuthPage("127.0.0.1", 8765)
            auth.resize(width, height)
            auth.update_state("connection", state)
            auth.show()
            app.processEvents()
            _save(auth, output / f"auth-{theme_name}-{size_name}.png")
            auth.close()

            store = AppStore()
            store.state = state
            workspace = Workspace(ChatService(NetworkClient("127.0.0.1", 9), store), store, "127.0.0.1", 8765)
            workspace.show()
            app.processEvents()
            workspace.resize(width, height)
            app.processEvents()
            workspace.update_state("auth", state)
            workspace.update_state("conversations", state)
            workspace.update_state("activities", state)
            workspace.update_state("todos", state)
            workspace.chat.set_conversation(state.current_conversation, "项目组")
            workspace.update_state("messages", state)
            app.processEvents()
            for index, name in ((0, "messages"), (1, "contacts"), (2, "ai-tasks"), (3, "todos"), (4, "admin"), (5, "settings")):
                workspace._navigate(index)
                app.processEvents()
                _save(workspace, output / f"{name}-{theme_name}-{size_name}.png")
            workspace.settings._select_section(1)
            app.processEvents()
            _save(workspace, output / f"settings-appearance-{theme_name}-{size_name}.png")
            workspace.settings._select_section(3)
            app.processEvents()
            _save(workspace, output / f"settings-notifications-{theme_name}-{size_name}.png")
            workspace._navigate(0)
            workspace.chat.details_button.click()
            app.processEvents()
            _save(workspace, output / f"messages-details-{theme_name}-{size_name}.png")
            workspace.close()
    reset_theme_manager()
    settings_dir.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/ui/screenshots/baseline-2026-11"),
    )
    args = parser.parse_args()
    render(args.output)
    print(f"Snapshots written to {args.output}")


if __name__ == "__main__":
    main()
