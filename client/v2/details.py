"""Conversation details widgets for the PySide6 client."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from client.models import AppState, ConversationKey
from client.service import ChatService

from .profile_widgets import ProfilePopover
from .widgets import Avatar, ToggleSwitch, UserAvatar, configure_scroll_surface


def role_label(member: dict[str, Any]) -> str:
    if member.get("kind") == "agent":
        return "AI Agent"
    if member.get("group_role") == "owner":
        return "群主"
    if member.get("global_role") == "admin":
        return "管理员"
    return "成员"


class SectionTitle(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName("DetailsSectionTitle")


class MemberRow(QFrame):
    activated = Signal(object, object)

    def __init__(self, member: dict[str, Any], service: ChatService) -> None:
        super().__init__()
        self.member = dict(member)
        self.setObjectName("MemberRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 7, 8, 7)
        row.setSpacing(10)
        is_agent = member.get("kind") == "agent"
        user_id = None if is_agent else int(member.get("id", 0) or 0)
        self.profile = service.profile(user_id, member)
        self.avatar = UserAvatar(service, self.profile, 36, clickable=True)
        self.avatar.activated.connect(lambda _profile, _anchor: self.activated.emit(self.member, self.avatar))
        row.addWidget(self.avatar)
        labels = QVBoxLayout()
        labels.setSpacing(1)
        name = QLabel(str(member.get("username", "")))
        name.setObjectName("MemberName")
        name.setWordWrap(True)
        name.setToolTip(name.text())
        state = "在线" if member.get("online") else "离线"
        if member.get("muted"):
            state = "已禁言"
        status = QLabel(state)
        status.setObjectName("Muted")
        labels.addWidget(name)
        labels.addWidget(status)
        row.addLayout(labels, 1)
        badge = QLabel(role_label(member))
        badge.setObjectName("AgentBadge" if is_agent else "RoleBadge")
        row.addWidget(badge)
        more = QLabel("›")
        more.setObjectName("Muted")
        row.addWidget(more)

    def mousePressEvent(self, event: Any) -> None:
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.member, self)


class MemberListSection(QWidget):
    moderation_requested = Signal(int, bool)

    def __init__(self, service: ChatService) -> None:
        super().__init__()
        self.service = service
        self.popover = ProfilePopover(service, self)
        self.popover.action_requested.connect(self._profile_action)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.title = SectionTitle("群成员")
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        heading = QHBoxLayout()
        heading.addWidget(self.title)
        heading.addStretch()
        heading.addWidget(self.count)
        layout.addLayout(heading)
        self.rows = QVBoxLayout()
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        layout.addLayout(self.rows)
        self.agent_model = ""

    def set_members(self, members: list[dict[str, Any]], state: AppState) -> None:
        self.popover.hide()
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.agent_model = state.agent_model
        humans = sum(member.get("kind") != "agent" for member in members)
        self.count.setText(f"{humans} 人")
        ordered = sorted(
            members,
            key=lambda member: (
                member.get("kind") == "agent",
                member.get("group_role") != "owner",
                member.get("global_role") != "admin",
                str(member.get("username", "")).casefold(),
            ),
        )
        for member in ordered:
            row = MemberRow(member, self.service)
            row.activated.connect(self._open_member)
            self.rows.addWidget(row)

    def _open_member(self, member: dict[str, Any], anchor: QWidget) -> None:
        is_agent = member.get("kind") == "agent"
        enriched = dict(member)
        if is_agent:
            enriched["agent_model"] = self.agent_model
        user_id = None if is_agent else int(member.get("id", 0) or 0)
        self.popover.show_for(anchor, self.service.profile(user_id, enriched))

    def _profile_action(self, action: str, profile: object) -> None:
        user_id = getattr(profile, "user_id", None)
        if action in {"mute", "unmute"} and user_id is not None:
            self.moderation_requested.emit(int(user_id), action == "mute")
            self.popover.hide()


class ScheduledSummarySetting(QWidget):
    changed = Signal(int, bool)

    OPTIONS = (
        ("15 分钟", 15),
        ("30 分钟", 30),
        ("1 小时", 60),
        ("2 小时", 120),
        ("6 小时", 360),
        ("12 小时", 720),
        ("24 小时", 1440),
    )

    def __init__(self) -> None:
        super().__init__()
        self._updating = False
        self._request_id: str | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)
        root.addWidget(SectionTitle("AI 功能"))
        switch_row = QHBoxLayout()
        labels = QVBoxLayout()
        title = QLabel("定时总结")
        title.setObjectName("MemberName")
        description = QLabel("按设定周期总结群内新增消息")
        description.setObjectName("Muted")
        labels.addWidget(title)
        labels.addWidget(description)
        self.toggle = ToggleSwitch()
        switch_row.addLayout(labels, 1)
        switch_row.addWidget(self.toggle)
        root.addLayout(switch_row)
        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("总结周期"))
        interval_row.addStretch()
        self.interval = QComboBox()
        self.interval.setEditable(True)
        self.interval.setMinimumWidth(120)
        for label, value in self.OPTIONS:
            self.interval.addItem(label, value)
        interval_row.addWidget(self.interval)
        root.addLayout(interval_row)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        root.addWidget(self.status)
        self.toggle.toggled.connect(self._submit_toggle)
        self.interval.activated.connect(lambda _index: self._submit_interval())
        if self.interval.lineEdit():
            self.interval.lineEdit().editingFinished.connect(self._submit_interval)

    def set_value(
        self,
        schedule: dict[str, Any],
        can_manage: bool,
        pending: dict[str, str],
        topic: str = "",
    ) -> None:
        busy = bool(self._request_id and self._request_id in pending)
        if busy:
            self.setEnabled(False)
            self.status.setText("保存中…")
            return
        failed = bool(self._request_id and topic == "error")
        self._request_id = None
        self._updating = True
        enabled = bool(schedule.get("enabled", False))
        minutes = int(schedule.get("interval_minutes", 60) or 60)
        self.toggle.setChecked(enabled)
        index = self.interval.findData(minutes)
        if index >= 0:
            self.interval.setCurrentIndex(index)
        else:
            self.interval.setEditText(f"{minutes} 分钟")
        self._updating = False
        self.setEnabled(can_manage)
        if failed:
            self.status.setText("保存失败，已恢复服务端设置")
        elif not can_manage:
            self.status.setText("仅群主或管理员可修改")
        else:
            self.status.setText("已启用" if enabled else "已停用")

    def track_request(self, request_id: str | None) -> None:
        self._request_id = request_id
        if request_id:
            self.setEnabled(False)
            self.status.setText("保存中…")

    def _minutes(self) -> int | None:
        data = self.interval.currentData()
        if data is not None and self.interval.currentText() == self.interval.itemText(self.interval.currentIndex()):
            return int(data)
        digits = "".join(character for character in self.interval.currentText() if character.isdigit())
        if not digits:
            return None
        value = int(digits)
        return value if 1 <= value <= 1440 else None

    def _submit_toggle(self, enabled: bool) -> None:
        if not self._updating:
            minutes = self._minutes() or 60
            self.changed.emit(minutes, enabled)

    def _submit_interval(self) -> None:
        if self._updating or not self.isEnabled():
            return
        minutes = self._minutes()
        if minutes is None:
            self.status.setText("周期需为 1–1440 分钟")
            return
        self.changed.emit(minutes, self.toggle.isChecked())


class ConversationDetailsPanel(QFrame):
    close_requested = Signal()

    def __init__(self, service: ChatService) -> None:
        super().__init__()
        self.service = service
        self.key: ConversationKey | None = None
        self.title_text = ""
        self.setObjectName("DetailsPanel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(320)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(16, 12, 10, 10)
        title = QLabel("会话详情")
        title.setObjectName("SectionTitle")
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("Ghost")
        self.close_button.setFixedWidth(38)
        header_row.addWidget(title)
        header_row.addStretch()
        header_row.addWidget(self.close_button)
        root.addWidget(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.content_layout = QVBoxLayout(self.scroll_content)
        self.content_layout.setContentsMargins(16, 8, 16, 18)
        self.content_layout.setSpacing(20)
        self.overview = QFrame()
        self.overview.setObjectName("DetailsOverview")
        overview_row = QHBoxLayout(self.overview)
        overview_row.setContentsMargins(10, 10, 10, 10)
        self.avatar = Avatar("会话", 48)
        overview_row.addWidget(self.avatar)
        overview_labels = QVBoxLayout()
        self.name = QLabel("选择一个会话")
        self.name.setObjectName("SectionTitle")
        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        overview_labels.addWidget(self.name)
        overview_labels.addWidget(self.meta)
        overview_row.addLayout(overview_labels, 1)
        self.members = MemberListSection(service)
        self.schedule = ScheduledSummarySetting()
        self.content_layout.addWidget(self.overview)
        self.content_layout.addWidget(self.members)
        self.content_layout.addWidget(self.schedule)
        self.content_layout.addStretch()
        configure_scroll_surface(scroll, self.scroll_content)
        scroll.setWidget(self.scroll_content)
        root.addWidget(scroll, 1)
        self.close_button.clicked.connect(self.close_requested)
        self.members.moderation_requested.connect(self._moderate)
        self.schedule.changed.connect(self._set_schedule)

    def set_context(self, key: ConversationKey | None, title: str, state: AppState, topic: str = "") -> None:
        self.key = key
        self.title_text = title
        if not key:
            return
        is_group = key.kind == "group"
        self.name.setText(title)
        self.avatar.set_avatar(title)
        self.members.setVisible(is_group)
        self.schedule.setVisible(is_group)
        if is_group:
            members = state.members_by_group.get(key.identifier, state.members)
            humans = sum(member.get("kind") != "agent" for member in members)
            self.meta.setText(f"群聊 · {humans} 位成员")
            self.members.set_members(members, state)
            permissions = state.group_permissions.get(key.identifier, {})
            schedule = state.schedules_by_group.get(
                key.identifier,
                {"group_id": key.identifier, "interval_minutes": 60, "enabled": False},
            )
            self.schedule.set_value(
                schedule,
                bool(permissions.get("can_manage_schedule", False)),
                state.pending,
                topic,
            )
        else:
            self.meta.setText("私聊")

    def _moderate(self, user_id: int, muted: bool) -> None:
        if self.key and self.key.kind == "group":
            self.service.moderate(self.key.identifier, user_id, muted)

    def _set_schedule(self, minutes: int, enabled: bool) -> None:
        if self.key and self.key.kind == "group":
            request_id = self.service.set_schedule(self.key.identifier, minutes, enabled)
            self.schedule.track_request(request_id)
            if request_id is None:
                self.set_context(self.key, self.title_text, self.service.store.state, "error")
