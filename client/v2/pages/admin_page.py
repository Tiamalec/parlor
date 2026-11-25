"""Client administration page."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QHeaderView, QListWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from client.models import AppState
from ..widgets import configure_surface
from ..components import PageHeader, SectionHeader, StatTile


class AdminPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(14)
        root.addWidget(
            PageHeader(
                "管理员控制台",
                "仅显示安全的运行概览与已授权审计信息。",
                eyebrow="运行与审计",
            )
        )
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.metrics: dict[str, StatTile] = {}
        for key, label in (("online_users", "在线用户"), ("total_users", "总用户"), ("groups", "群组"), ("uptime_seconds", "运行时长")):
            tile = StatTile(label)
            cards.addWidget(tile, 1)
            self.metrics[key] = tile
        root.addLayout(cards)
        self.audit = QTableWidget(0, 5)
        self.audit.setHorizontalHeaderLabels(["工具", "调用者", "会话", "状态", "结果数"])
        self.audit.verticalHeader().setVisible(False)
        self.audit.horizontalHeader().setStretchLastSection(True)
        for column in range(4):
            self.audit.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        directories = QHBoxLayout()
        directories.setSpacing(14)
        self.users = QListWidget()
        self.users.setObjectName("AdminList")
        self.groups = QListWidget()
        self.groups.setObjectName("AdminList")
        user_frame = QFrame()
        user_frame.setObjectName("Surface")
        user_layout = QVBoxLayout(user_frame)
        user_layout.setContentsMargins(16, 14, 16, 14)
        user_layout.addWidget(SectionHeader("用户", "只读"))
        user_layout.addWidget(self.users)
        group_frame = QFrame()
        group_frame.setObjectName("Surface")
        group_layout = QVBoxLayout(group_frame)
        group_layout.setContentsMargins(16, 14, 16, 14)
        group_layout.addWidget(SectionHeader("群组", "只读"))
        group_layout.addWidget(self.groups)
        directories.addWidget(user_frame)
        directories.addWidget(group_frame)
        root.addLayout(directories)
        audit_frame = QFrame()
        audit_frame.setObjectName("Surface")
        audit_layout = QVBoxLayout(audit_frame)
        audit_layout.setContentsMargins(16, 14, 16, 14)
        audit_layout.addWidget(SectionHeader("Agent 工具审计", "最近调用"))
        audit_layout.addWidget(self.audit, 1)
        root.addWidget(audit_frame, 1)

    def update_state(self, topic: str, state: AppState) -> None:
        if topic in {"auth", "system"}:
            for key, label in self.metrics.items():
                value = state.system_status.get(key)
                if key == "uptime_seconds" and value is not None:
                    value = f"{int(value) // 60} 分钟"
                label.set_value(str(value if value is not None else "—"))
        if topic in {"auth", "contacts", "groups"}:
            self.users.clear()
            self.groups.clear()
            for user in state.users:
                self.users.addItem(f"{user.get('username', '')} · {user.get('role', 'user')}")
            for group in state.groups:
                self.groups.addItem(str(group.get("name", "")))
        if topic in {"auth", "tools"}:
            self.audit.setRowCount(len(state.tool_calls))
            for row, item in enumerate(state.tool_calls):
                values = (
                    str(item.get("tool_name", item.get("tool", ""))),
                    str(item.get("requested_by_name", "")),
                    f"{item.get('conversation_type', '')}:{item.get('conversation_id', '')}",
                    str(item.get("status", "")),
                    str(item.get("result_count", "")),
                )
                for column, value in enumerate(values):
                    self.audit.setItem(row, column, QTableWidgetItem(value))
