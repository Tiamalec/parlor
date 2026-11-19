"""Shared profile-card presentation for contacts, members, and messages."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from client.profile import ProfileViewModel
from client.service import ChatService

from .widgets import UserAvatar


def profile_role(profile: ProfileViewModel) -> str:
    if profile.kind == "agent":
        return "AI Agent"
    if profile.group_role == "owner":
        return "群主"
    if profile.global_role == "admin":
        return "管理员"
    return "联系人"


class UserProfileCard(QFrame):
    action_requested = Signal(str, object)

    def __init__(self, service: ChatService, *, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.profile = service.profile(None, {"kind": "agent", "username": "未选择"})
        self.compact = compact
        self.setObjectName("ProfileCard" if compact else "ProfilePane")
        root = QVBoxLayout(self)
        root.setContentsMargins(18 if compact else 28, 20 if compact else 32, 18 if compact else 28, 18 if compact else 28)
        root.setSpacing(10)
        self.avatar_host = QHBoxLayout()
        root.addLayout(self.avatar_host)
        self.avatar: UserAvatar | None = None
        self.name = QLabel("选择联系人或群聊")
        self.name.setObjectName("ProfileName")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.role = QLabel("")
        self.role.setObjectName("RoleBadge")
        self.role.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        root.addWidget(self.name)
        root.addWidget(self.role, alignment=Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.status)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        root.addLayout(self.actions)
        root.addStretch()

    def set_profile(
        self,
        profile: ProfileViewModel,
        *,
        primary_action: str = "",
        primary_label: str = "",
    ) -> None:
        self.profile = profile
        while self.avatar_host.count():
            item = self.avatar_host.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.avatar = UserAvatar(self.service, profile, 64 if not self.compact else 54)
        self.avatar_host.addStretch()
        self.avatar_host.addWidget(self.avatar)
        self.avatar_host.addStretch()
        self.name.setText(profile.display_name)
        self.role.setText(profile_role(profile))
        if profile.muted:
            state = "已禁言"
        elif profile.online is None:
            state = "状态未知"
        else:
            state = "在线" if profile.online else "离线"
        if profile.kind == "agent" and profile.agent_model:
            state += f" · {profile.agent_model}"
        self.status.setText(state)
        self._clear_actions()
        if not self.compact:
            self.actions.addStretch()
        if primary_action and primary_label:
            button = QPushButton(primary_label)
            button.setObjectName("Primary")
            if not self.compact:
                button.setMaximumWidth(240)
            button.clicked.connect(lambda: self.action_requested.emit(primary_action, self.profile))
            self.actions.addWidget(button)
        for action in profile.available_actions:
            label = "解除禁言" if action == "unmute" else "禁言" if action == "mute" else action
            button = QPushButton(label)
            if action == "mute":
                button.setObjectName("DangerButton")
            button.clicked.connect(lambda _checked=False, value=action: self.action_requested.emit(value, self.profile))
            self.actions.addWidget(button)
        if not self.compact:
            self.actions.addStretch()

    def _clear_actions(self) -> None:
        while self.actions.count():
            item = self.actions.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()


class ProfilePopover(QFrame):
    action_requested = Signal(str, object)

    def __init__(self, service: ChatService, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("ProfilePopover")
        self.setFixedWidth(300)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.card = UserProfileCard(service, compact=True)
        self.card.action_requested.connect(self.action_requested)
        root.addWidget(self.card)
        self._identity: tuple[str, int | None] | None = None

    def show_for(
        self,
        anchor: QWidget,
        profile: ProfileViewModel,
        *,
        primary_action: str = "",
        primary_label: str = "",
    ) -> None:
        identity = (profile.kind, profile.user_id)
        if self.isVisible() and self._identity == identity:
            self.hide()
            return
        self._identity = identity
        self.card.set_profile(profile, primary_action=primary_action, primary_label=primary_label)
        self.adjustSize()
        origin = anchor.mapToGlobal(QPoint(anchor.width() + 8, 0))
        screen = anchor.screen().availableGeometry()
        x = min(origin.x(), screen.right() - self.width())
        y = min(origin.y(), screen.bottom() - self.height())
        self.move(max(screen.left(), x), max(screen.top(), y))
        self.show()
        self.raise_()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)
