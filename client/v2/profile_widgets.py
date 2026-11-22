"""Shared profile-card presentation for contacts, members, and messages."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from client.profile import ProfileViewModel
from client.service import ChatService

from .widgets import UserAvatar


PROFILE_POPOVER_WIDTH = 320
PROFILE_POPOVER_MARGIN = 8
PROFILE_POPOVER_MAX_HEIGHT = 560
PROFILE_POPOVER_HIDE_DELAY_MS = 320


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
        self.username = QLabel("")
        self.username.setObjectName("Muted")
        self.username.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.username)
        root.addWidget(self.role, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.description = QLabel("")
        self.description.setObjectName("Muted")
        self.description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description.setWordWrap(True)
        root.addWidget(self.description)
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
        self.avatar = UserAvatar(
            self.service,
            profile,
            64 if not self.compact else 54,
            hoverable=False,
        )
        self.avatar_host.addStretch()
        self.avatar_host.addWidget(self.avatar)
        self.avatar_host.addStretch()
        self.name.setText(profile.display_name)
        self.username.setText(f"账号：{profile.username}" if profile.username else "")
        self.role.setText(profile_role(profile))
        detail = " · ".join(part for part in (profile.agent_description, profile.agent_personality) if part)
        self.description.setText(detail if profile.kind == "agent" else "")
        self.description.setVisible(bool(self.description.text()))
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
        self.setFixedWidth(PROFILE_POPOVER_WIDTH)
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("ProfileCardScroll")
        self.scroll.viewport().setObjectName("ProfileCardViewport")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.card = UserProfileCard(service, compact=True)
        self.card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.card.action_requested.connect(self.action_requested)
        self.scroll.setWidget(self.card)
        root.addWidget(self.scroll)
        self._identity: tuple[str, int | None, str] | None = None
        self._anchor: QWidget | None = None
        self._pinned = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(PROFILE_POPOVER_HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._hide_if_outside)

    def show_for(
        self,
        anchor: QWidget,
        profile: ProfileViewModel,
        *,
        primary_action: str = "",
        primary_label: str = "",
        pinned: bool = False,
    ) -> None:
        identity = (profile.kind, profile.user_id, profile.agent_id)
        if self.isVisible() and self._identity == identity:
            if pinned:
                if self._pinned:
                    self.hide()
                    return
                self._pinned = True
            self._hide_timer.stop()
            self._place(anchor)
            return
        self._hide_timer.stop()
        self._identity = identity
        self._anchor = anchor
        self._pinned = pinned
        self.card.set_profile(profile, primary_action=primary_action, primary_label=primary_label)
        self._resize_to_content(anchor)
        self.scroll.verticalScrollBar().setValue(0)
        self._place(anchor)
        self.show()
        self.raise_()

    def _resize_to_content(self, anchor: QWidget) -> None:
        screen = anchor.screen().availableGeometry()
        inner_width = PROFILE_POPOVER_WIDTH - 2 - self.scroll.verticalScrollBar().sizeHint().width()
        self.card.setFixedWidth(max(220, inner_width))
        self.card.layout().activate()
        self.card.adjustSize()
        desired = self.card.layout().heightForWidth(self.card.width())
        if desired <= 0:
            desired = self.card.sizeHint().height()
        desired = max(desired, self.card.sizeHint().height())
        max_height = min(
            PROFILE_POPOVER_MAX_HEIGHT,
            max(220, screen.height() - PROFILE_POPOVER_MARGIN * 2),
        )
        self.setFixedSize(PROFILE_POPOVER_WIDTH, max(180, min(desired + 2, max_height)))

    def _place(self, anchor: QWidget) -> None:
        origin = anchor.mapToGlobal(QPoint(anchor.width() + 10, -8))
        screen = anchor.screen().availableGeometry()
        if origin.x() + self.width() > screen.right() - PROFILE_POPOVER_MARGIN:
            origin.setX(anchor.mapToGlobal(QPoint(-self.width() - 10, -8)).x())
        x = max(
            screen.left() + PROFILE_POPOVER_MARGIN,
            min(origin.x(), screen.right() - self.width() - PROFILE_POPOVER_MARGIN),
        )
        y = max(
            screen.top() + PROFILE_POPOVER_MARGIN,
            min(origin.y(), screen.bottom() - self.height() - PROFILE_POPOVER_MARGIN),
        )
        self.move(x, y)

    def schedule_hide(self) -> None:
        """Hide a hover-opened card unless the pointer reaches the card in time."""
        if self.isVisible() and not self._pinned:
            self._hide_timer.start()

    def _hide_if_outside(self) -> None:
        if self._pinned:
            return
        cursor = QCursor.pos()
        for widget in (self._anchor, self):
            if widget is None:
                continue
            try:
                if widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(cursor)):
                    return
            except RuntimeError:
                continue
        self.hide()

    def enterEvent(self, event: Any) -> None:
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self.schedule_hide()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


def build_profile_menu(
    parent: QWidget,
    profile: ProfileViewModel,
    *,
    can_mention: bool = False,
) -> QMenu:
    """Build the shared avatar context menu with semantic, permission-aware actions."""
    menu = QMenu(parent)
    menu.setObjectName("ProfileActionsMenu")
    if can_mention:
        action = menu.addAction("提及")
        action.setData(("mention", ""))
    action = menu.addAction("查看资料")
    action.setData(("view", ""))
    if profile.username:
        action = menu.addAction("复制账号")
        action.setData(("copy", profile.username))
    if profile.available_actions:
        menu.addSeparator()
        for value in profile.available_actions:
            label = "解除禁言" if value == "unmute" else "禁言" if value == "mute" else value
            action = menu.addAction(label)
            action.setData(("moderate", value))
    return menu
