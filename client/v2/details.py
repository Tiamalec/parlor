"""Conversation details widgets for the PySide6 client."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from client.models import AppState, ConversationKey
from client.service import ChatService

from .profile_widgets import ProfilePopover, build_profile_menu
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
        name = QLabel(self.profile.display_name)
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
    mention_requested = Signal(object)

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
                str(member.get("nickname") or member.get("display_name") or member.get("username", "")).casefold(),
            ),
        )
        for member in ordered:
            row = MemberRow(member, self.service)
            row.activated.connect(self._open_member)
            row.avatar.profile_hovered.connect(
                lambda _profile, anchor, value=dict(member): self._hover_member(value, anchor)
            )
            row.avatar.hover_left.connect(self._hover_left)
            row.avatar.context_requested.connect(
                lambda _profile, anchor, value=dict(member): self._member_menu(value, anchor)
            )
            self.rows.addWidget(row)

    def _profile_for(self, member: dict[str, Any]):
        is_agent = member.get("kind") == "agent"
        enriched = dict(member)
        if is_agent:
            enriched["agent_model"] = self.agent_model
        user_id = None if is_agent else int(member.get("id", 0) or 0)
        return self.service.profile(user_id, enriched)

    def _hover_member(self, member: dict[str, Any], anchor: QWidget) -> None:
        self.popover.show_for(anchor, self._profile_for(member))

    def _hover_left(self, _profile: object) -> None:
        self.popover.schedule_hide()

    def _member_menu(self, member: dict[str, Any], anchor: QWidget) -> None:
        self.popover.hide()
        profile = self._profile_for(member)
        menu = build_profile_menu(self, profile, can_mention=True)
        selected = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if selected is None:
            return
        kind, value = selected.data() or ("", "")
        if kind == "mention":
            self.mention_requested.emit(profile)
        elif kind == "view":
            self.popover.show_for(anchor, profile, pinned=True)
        elif kind == "copy" and value:
            QApplication.clipboard().setText(str(value))
        elif kind == "moderate":
            self._profile_action(str(value), profile)

    def _open_member(self, member: dict[str, Any], anchor: QWidget) -> None:
        self.popover.show_for(anchor, self._profile_for(member), pinned=True)

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


class GroupAgentSettings(QWidget):
    manage_requested = Signal(str)
    memory_requested = Signal(str, str)

    def __init__(self, service: ChatService) -> None:
        super().__init__()
        self.service = service
        self.group_id = ""
        self._updating = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)
        root.addWidget(SectionTitle("群内 AI Agent"))
        helper = QLabel("预设角色可直接添加；角色卡、模型和工具权限在角色库中编辑，共享记忆在这里进入。")
        helper.setObjectName("Muted")
        helper.setWordWrap(True)
        root.addWidget(helper)
        self.rows = QVBoxLayout()
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(5)
        root.addLayout(self.rows)
        add_row = QHBoxLayout()
        self.available = QComboBox()
        self.available.setMinimumWidth(120)
        self.add_button = QPushButton("添加")
        add_row.addWidget(self.available, 1)
        add_row.addWidget(self.add_button)
        root.addLayout(add_row)
        self.manage = QPushButton("管理 Agent 角色库")
        self.manage.setObjectName("Ghost")
        root.addWidget(self.manage)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.add_button.clicked.connect(self._add_selected)
        self.manage.clicked.connect(lambda: self.manage_requested.emit(self.group_id))

    def set_group(self, group_id: str, state: AppState, topic: str = "") -> None:
        self.group_id = group_id
        permissions = state.group_permissions.get(group_id, {})
        can_manage = bool(
            permissions.get("can_manage_members")
            or permissions.get("can_manage_schedule")
        )
        agents = state.group_agents.get(group_id)
        if agents is None:
            agents = [
                member for member in state.members_by_group.get(group_id, state.members)
                if member.get("kind") == "agent"
            ]
        self._clear_rows()
        if not agents:
            empty = QLabel("本群还没有启用 Agent。")
            empty.setObjectName("Muted")
            self.rows.addWidget(empty)
        bound = {str(agent.get("id")) for agent in agents}
        for agent in agents:
            self.rows.addWidget(self._agent_row(agent, can_manage))
        self._updating = True
        self.available.clear()
        for agent in state.agents:
            agent_id = str(agent.get("id", ""))
            if not agent.get("enabled", True) or agent_id in bound:
                continue
            label = str(agent.get("name") or "Agent")
            if agent.get("builtin"):
                label += " · 预设"
            self.available.addItem(label, agent_id)
        self._updating = False
        has_available = self.available.count() > 0
        self.available.setEnabled(can_manage and has_available)
        self.add_button.setEnabled(can_manage and has_available)
        self.manage.setEnabled(bool(group_id))
        if not can_manage:
            self.status.setText("仅群主或管理员可以调整群内 Agent")
        elif not has_available:
            self.status.setText("所有角色都已加入本群，可在“新建 / 编辑 Agent”中创建新角色")
        elif topic == "error" and state.last_error:
            self.status.setText(f"操作失败：{state.last_error}")
        else:
            self.status.setText(f"已启用 {len(agents)} 个 Agent")

    def _agent_row(self, agent: dict[str, Any], can_manage: bool) -> QWidget:
        row = QFrame()
        row.setObjectName("SettingsRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 6, 8, 6)
        avatar = UserAvatar(self.service, self.service.profile(None, agent), 30)
        labels = QVBoxLayout()
        labels.setSpacing(1)
        name = QLabel(str(agent.get("name") or "Agent"))
        name.setObjectName("MemberName")
        meta = QLabel(
            ("预设角色" if agent.get("builtin") else "自定义角色")
            + (" · 已停用" if not agent.get("enabled", True) else "")
        )
        meta.setObjectName("Muted")
        labels.addWidget(name)
        labels.addWidget(meta)
        toggle = ToggleSwitch()
        toggle.setChecked(bool(agent.get("enabled", True)))
        toggle.setEnabled(can_manage)
        toggle.toggled.connect(
            lambda checked, agent_id=str(agent.get("id", "")): self._toggle(agent_id, checked)
        )
        memory = QPushButton("记忆")
        memory.setObjectName("Ghost")
        memory.setEnabled(bool(agent.get("enabled", True)))
        memory.clicked.connect(
            lambda _checked=False, agent_id=str(agent.get("id", "")): self.memory_requested.emit(
                self.group_id, agent_id
            )
        )
        layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(labels, 1)
        layout.addWidget(memory)
        layout.addWidget(toggle)
        return row

    def _clear_rows(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _toggle(self, agent_id: str, enabled: bool) -> None:
        if self._updating or not self.group_id:
            return
        request_id = self.service.set_group_agent(self.group_id, agent_id, enabled)
        self.status.setText("保存中…" if request_id else self.service.store.state.last_error)

    def _add_selected(self) -> None:
        agent_id = str(self.available.currentData() or "")
        if not agent_id or not self.group_id:
            return
        request_id = self.service.set_group_agent(self.group_id, agent_id, True)
        self.status.setText("正在加入群组…" if request_id else self.service.store.state.last_error)


class ConversationDetailsPanel(QFrame):
    close_requested = Signal()
    agent_manager_requested = Signal(str)
    agent_memory_requested = Signal(str, str)
    mention_requested = Signal(object)

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
        nickname_row = QHBoxLayout()
        self.nickname = QLineEdit()
        self.nickname.setPlaceholderText("群内昵称（留空使用昵称）")
        self.nickname.setMaxLength(30)
        self.nickname.setAccessibleName("群内昵称")
        self.save_nickname = QPushButton("保存")
        nickname_row.addWidget(self.nickname, 1)
        nickname_row.addWidget(self.save_nickname)
        self.nickname_section = QWidget()
        self.nickname_section.setLayout(nickname_row)
        self.nickname_feedback = QLabel("")
        self.nickname_feedback.setObjectName("Muted")
        self._nickname_request_id: str | None = None
        self.schedule = ScheduledSummarySetting()
        self.agents = GroupAgentSettings(service)
        self.private_section = QWidget()
        private_layout = QVBoxLayout(self.private_section)
        private_layout.setContentsMargins(0, 0, 0, 0)
        self.private_status = QLabel("")
        self.private_status.setObjectName("Muted")
        self.private_block = QPushButton("屏蔽用户")
        self.private_block.setObjectName("DangerButton")
        self.private_block.setAccessibleName("屏蔽私聊用户")
        private_layout.addWidget(self.private_status)
        private_layout.addWidget(self.private_block)
        self.content_layout.addWidget(self.overview)
        self.content_layout.addWidget(self.agents)
        self.content_layout.addWidget(self.members)
        self.content_layout.addWidget(self.nickname_section)
        self.content_layout.addWidget(self.nickname_feedback)
        self.content_layout.addWidget(self.schedule)
        self.content_layout.addWidget(self.private_section)
        self.content_layout.addStretch()
        configure_scroll_surface(scroll, self.scroll_content)
        scroll.setWidget(self.scroll_content)
        root.addWidget(scroll, 1)
        self.close_button.clicked.connect(self.close_requested)
        self.members.moderation_requested.connect(self._moderate)
        self.members.mention_requested.connect(self.mention_requested)
        self.schedule.changed.connect(self._set_schedule)
        self.agents.manage_requested.connect(self.agent_manager_requested.emit)
        self.agents.memory_requested.connect(self.agent_memory_requested.emit)
        self.save_nickname.clicked.connect(self._set_nickname)
        self.private_block.clicked.connect(self._set_private_block)

    def set_context(self, key: ConversationKey | None, title: str, state: AppState, topic: str = "") -> None:
        changed_group = key != self.key
        self.key = key
        self.title_text = title
        if not key:
            return
        if changed_group:
            self.nickname.clear()
            self.nickname_feedback.clear()
            self._nickname_request_id = None
        is_group = key.kind == "group"
        self.name.setText(title)
        self.avatar.set_avatar(title, group=is_group)
        self.members.setVisible(is_group)
        self.nickname_section.setVisible(is_group)
        self.nickname_feedback.setVisible(is_group)
        self.agents.setVisible(is_group)
        self.schedule.setVisible(is_group)
        self.private_section.setVisible(not is_group and bool(state.capabilities.get("private_chat_v2")))
        if is_group:
            members = state.members_by_group.get(key.identifier, state.members)
            humans = sum(member.get("kind") != "agent" for member in members)
            self.meta.setText(f"群聊 · {humans} 位成员")
            self.members.set_members(members, state)
            own_id = str(state.current_user.get("id", ""))
            own = next((member for member in members if str(member.get("id")) == own_id), None)
            if own and (changed_group or not self.nickname.hasFocus()):
                self.nickname.setText(str(own.get("nickname") or ""))
            if self._nickname_request_id and self._nickname_request_id not in state.pending:
                if topic == "error":
                    self.nickname_feedback.setText(state.last_error)
                    self._nickname_request_id = None
                elif topic == "details" and own and str(own.get("nickname") or "") == self.nickname.text().strip():
                    self.nickname_feedback.setText("已保存")
                    self._nickname_request_id = None
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
            self.agents.set_group(key.identifier, state, topic)
        else:
            self.meta.setText("私聊")
            summary = next((item for item in state.conversations if item.key == key), None)
            private_state = state.private_states.get(key, {})
            blocked_by_me = bool(private_state.get("blocked_by_me", summary.blocked_by_me if summary else False))
            can_send = bool(private_state.get("can_send", summary.can_send if summary else True))
            self.private_status.setText("你已屏蔽该用户" if blocked_by_me else "当前私聊不可发送" if not can_send else "")
            self.private_block.setText("解除屏蔽" if blocked_by_me else "屏蔽用户")
            self.private_block.setAccessibleName(self.private_block.text())
            self.private_block.setEnabled(can_send or blocked_by_me)
            self.private_block.setProperty("blocked", blocked_by_me)

    def _set_private_block(self) -> None:
        if not self.key or self.key.kind != "private":
            return
        blocked = bool(self.private_block.property("blocked"))
        if not blocked and QMessageBox.question(
            self, "屏蔽用户", "屏蔽后双方都无法在这段私聊中发送新消息。旧消息会保留。",
        ) != QMessageBox.StandardButton.Yes:
            return
        request_id = self.service.set_private_block(self.key, not blocked)
        if request_id:
            self.private_block.setEnabled(False)
            self.private_status.setText("正在更新屏蔽状态…")
        else:
            self.private_status.setText(self.service.store.state.last_error or "无法更新屏蔽状态")

    def _moderate(self, user_id: int, muted: bool) -> None:
        if self.key and self.key.kind == "group":
            self.service.moderate(self.key.identifier, user_id, muted)

    def _set_nickname(self) -> None:
        if self.key and self.key.kind == "group":
            request_id = self.service.set_group_nickname(self.key.identifier, self.nickname.text())
            self._nickname_request_id = request_id
            self.nickname_feedback.setText("保存中…" if request_id else self.service.store.state.last_error)

    def _set_schedule(self, minutes: int, enabled: bool) -> None:
        if self.key and self.key.kind == "group":
            request_id = self.service.set_schedule(self.key.identifier, minutes, enabled)
            self.schedule.track_request(request_id)
            if request_id is None:
                self.set_context(self.key, self.title_text, self.service.store.state, "error")
