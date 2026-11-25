"""Contacts directory page."""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractItemView, QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QStackedWidget, QVBoxLayout, QWidget
from client.models import AppState, ConversationKey
from client.service import ChatService
from ..profile_widgets import UserProfileCard
from ..widgets import Avatar, EmptyState, UserAvatar, configure_surface
from ..components import PageHeader


class ContactsPage(QWidget):
    conversation_requested = Signal(object, str)
    COMPACT_BREAKPOINT = 800

    def __init__(self, service: ChatService) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.current_user_id = 0
        self._selected_user: dict[str, Any] | None = None
        self._selected_group: dict[str, Any] | None = None
        self._compact_detail_open = False
        self._users: list[dict[str, Any]] = []
        self._groups: list[dict[str, Any]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(14)
        self.header = PageHeader(
            "通讯录",
            "查找联系人与群聊，查看资料后开始协作。",
            eyebrow="人与房间",
        )
        body = QHBoxLayout()
        body.setSpacing(0)
        self.directory = QFrame()
        self.directory.setObjectName("ContactsDirectory")
        self.directory.setMinimumWidth(300)
        self.directory.setMaximumWidth(360)
        directory_layout = QVBoxLayout(self.directory)
        directory_layout.setContentsMargins(16, 16, 16, 16)
        directory_layout.setSpacing(10)
        self.search = QLineEdit()
        self.search.setObjectName("DirectorySearch")
        self.search.setPlaceholderText("搜索联系人")
        self.tabs_track = QFrame()
        self.tabs_track.setObjectName("SegmentedTabs")
        tabs = QHBoxLayout(self.tabs_track)
        tabs.setContentsMargins(3, 3, 3, 3)
        tabs.setSpacing(3)
        self.people_tab = QPushButton("联系人")
        self.groups_tab = QPushButton("群聊")
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        for button in (self.people_tab, self.groups_tab):
            button.setObjectName("SegmentedTab")
            button.setCheckable(True)
            self.tab_group.addButton(button)
            tabs.addWidget(button)
        self.people_tab.setChecked(True)
        self.directory_status = QLabel("")
        self.directory_status.setObjectName("DirectoryStatus")
        self.directory_status.hide()
        directory_layout.addWidget(self.search)
        directory_layout.addWidget(self.tabs_track)
        directory_layout.addWidget(self.directory_status)
        self.directory_stack = QStackedWidget()
        self.users = QListWidget()
        self.users.setObjectName("DirectoryList")
        self.users.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        groups_page = QWidget()
        groups_layout = QVBoxLayout(groups_page)
        groups_layout.setContentsMargins(0, 0, 0, 0)
        group_heading = QHBoxLayout()
        group_heading.addWidget(QLabel("群聊"))
        group_heading.addStretch()
        create = QPushButton("＋ 创建")
        create.setObjectName("Ghost")
        group_heading.addWidget(create)
        self.groups = QListWidget()
        self.groups.setObjectName("DirectoryList")
        self.groups.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        groups_layout.addLayout(group_heading)
        groups_layout.addWidget(self.groups)
        self.directory_stack.addWidget(self.users)
        self.directory_stack.addWidget(groups_page)
        directory_layout.addWidget(self.directory_stack, 1)

        self.details_host = QWidget()
        configure_surface(self.details_host, "canvas")
        details_layout = QVBoxLayout(self.details_host)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(6)
        self.directory_back = QPushButton("返回通讯录")
        self.directory_back.setObjectName("Ghost")
        self.directory_back.setAccessibleName("返回通讯录列表")
        self.directory_back.hide()
        self.details = QStackedWidget()
        self.details.setObjectName("ContactDetails")
        self.empty_details = EmptyState("选择联系人或群聊", "查看资料、在线状态与可用操作", "◇")
        self.user_profile = UserProfileCard(service)
        self.user_profile.action_requested.connect(self._profile_action)
        self.group_profile = QFrame()
        self.group_profile.setObjectName("ProfilePane")
        group_detail = QVBoxLayout(self.group_profile)
        group_detail.setContentsMargins(28, 32, 28, 28)
        self.group_avatar = Avatar("群聊", 68, group=True)
        self.group_name = QLabel("")
        self.group_name.setObjectName("ProfileName")
        self.group_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.group_meta = QLabel("")
        self.group_meta.setObjectName("Muted")
        self.group_meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        group_detail.addWidget(self.group_avatar, alignment=Qt.AlignmentFlag.AlignHCenter)
        group_detail.addWidget(self.group_name)
        group_detail.addWidget(self.group_meta)
        group_actions = QHBoxLayout()
        group_actions.addStretch()
        self.group_primary = QPushButton("进入群聊")
        self.group_primary.setObjectName("Primary")
        self.group_primary.setMaximumWidth(240)
        self.group_more = QPushButton("更多")
        self.group_more.setObjectName("Ghost")
        group_actions.addWidget(self.group_primary)
        group_actions.addWidget(self.group_more)
        group_actions.addStretch()
        group_detail.addLayout(group_actions)
        group_detail.addStretch()
        for page in (self.empty_details, self.user_profile, self.group_profile):
            self.details.addWidget(page)
        details_layout.addWidget(self.directory_back, alignment=Qt.AlignmentFlag.AlignLeft)
        details_layout.addWidget(self.details, 1)
        body.addWidget(self.directory)
        body.addWidget(self.details_host, 1)
        root.addWidget(self.header)
        root.addLayout(body, 1)
        self.search.textChanged.connect(self._filter_directory)
        self.directory_back.clicked.connect(self._show_compact_directory)
        self.people_tab.clicked.connect(lambda: self._set_tab(0))
        self.groups_tab.clicked.connect(lambda: self._set_tab(1))
        self.users.itemClicked.connect(self._select_user)
        self.users.itemDoubleClicked.connect(self._open_user)
        self.groups.itemClicked.connect(self._select_group)
        self.groups.itemDoubleClicked.connect(self._open_group)
        create.clicked.connect(self._create)
        self.group_primary.clicked.connect(self._group_primary)
        self.group_more.clicked.connect(self._group_menu)

    def _set_tab(self, index: int) -> None:
        self.directory_stack.setCurrentIndex(index)
        self.people_tab.setChecked(index == 0)
        self.groups_tab.setChecked(index == 1)
        self.search.setPlaceholderText("搜索联系人" if index == 0 else "搜索群聊")
        self._filter_directory(self.search.text())

    def update_state(self, topic: str, state: AppState) -> None:
        self.current_user_id = int(state.current_user.get("id", 0) or 0)
        if topic == "avatar" and not self.isVisible():
            return
        if topic in {"auth", "contacts", "profile", "avatar"}:
            self._users = [dict(user) for user in state.users]
            if topic == "avatar":
                self._refresh_user_avatars()
            else:
                self._populate_users(self.search.text())
            if self._selected_user:
                current = next(
                    (user for user in state.users if str(user.get("id")) == str(self._selected_user.get("id"))),
                    self._selected_user,
                )
                self._selected_user = dict(current)
                profile = self.service.profile(int(current.get("id", 0) or 0), current)
                self.user_profile.set_profile(profile, primary_action="message", primary_label="发消息")
        if topic in {"auth", "groups"}:
            self._groups = [dict(group) for group in state.groups]
            self._populate_groups(self.search.text())
            if self._selected_group:
                current = next(
                    (group for group in state.groups if str(group.get("id")) == str(self._selected_group.get("id"))),
                    None,
                )
                if current:
                    self._show_group_profile(dict(current))
                else:
                    self._selected_group = None
                    self.details.setCurrentWidget(self.empty_details)
        if topic in {"auth", "contacts", "groups", "pending", "error"}:
            directory_pending = any(
                action in {"presence.list", "group.list", "group.create", "group.join", "group.leave"}
                for action in state.pending.values()
            )
            if directory_pending:
                self.directory_status.setText("正在更新通讯录…")
            elif topic == "error" and state.last_error:
                self.directory_status.setText(f"更新失败：{state.last_error}")
            else:
                self.directory_status.clear()
            self.directory_status.setVisible(bool(self.directory_status.text()))

    def _refresh_user_avatars(self) -> None:
        for index in range(self.users.count()):
            item = self.users.item(index)
            user = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(user, dict):
                continue
            profile = self.service.profile(int(user.get("id", 0) or 0), user)
            avatar = UserAvatar(self.service, profile, 32, hoverable=False)
            item.setIcon(QIcon(avatar.pixmap()))

    def _filter_directory(self, keyword: str) -> None:
        if self.directory_stack.currentIndex() == 0:
            self._populate_users(keyword)
        else:
            self._populate_groups(keyword)

    def _populate_users(self, keyword: str) -> None:
        self.users.clear()
        clean = keyword.strip().casefold()
        my_id = str(self.current_user_id or "")
        visible = [
            user for user in self._users
            if str(user.get("id")) != my_id and clean in f"{user.get('username', '')} {user.get('display_name') or ''}".casefold()
        ]
        for user in visible:
            profile = self.service.profile(int(user.get("id", 0) or 0), user)
            status = "在线" if user.get("online") else "离线"
            item = QListWidgetItem(f"{profile.display_name}\n{status}")
            item.setSizeHint(QSize(0, 56))
            item.setData(Qt.ItemDataRole.UserRole, dict(user))
            avatar = UserAvatar(self.service, profile, 36, hoverable=False)
            item.setIcon(QIcon(avatar.pixmap()))
            item.setToolTip(status)
            self.users.addItem(item)
        if not visible:
            self._add_directory_empty(self.users, "未找到联系人" if clean else "暂无联系人")

    def _populate_groups(self, keyword: str) -> None:
        self.groups.clear()
        clean = keyword.strip().casefold()
        visible = [group for group in self._groups if clean in str(group.get("name", "")).casefold()]
        joined = [group for group in visible if group.get("joined")]
        available = [group for group in visible if not group.get("joined")]
        if joined:
            self._add_group_section("我的群聊", joined)
        if available:
            self._add_group_section("可加入群聊", available)
        if not visible:
            self._add_directory_empty(self.groups, "未找到群聊" if clean else "暂无群聊")

    @staticmethod
    def _add_directory_empty(widget: QListWidget, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(Qt.ItemDataRole.UserRole, None)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setSizeHint(QSize(0, 52))
        widget.addItem(item)

    def _add_group_section(self, title: str, groups: list[dict[str, Any]]) -> None:
        header = QListWidgetItem(title)
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        header.setData(Qt.ItemDataRole.UserRole, None)
        self.groups.addItem(header)
        for group in groups:
            member_count = int(group.get("member_count", 0) or 0)
            meta = f"{member_count} 位成员" if member_count else ("已加入" if group.get("joined") else "可加入")
            item = QListWidgetItem(f"{group.get('name', '')}\n{meta}")
            item.setSizeHint(QSize(0, 56))
            item.setIcon(QIcon(Avatar(str(group.get("name", "")), 36, group=True).pixmap()))
            item.setData(Qt.ItemDataRole.UserRole, dict(group))
            self.groups.addItem(item)

    def _select_user(self, item: QListWidgetItem) -> None:
        user = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(user, dict):
            return
        self._selected_user = user
        self._selected_group = None
        profile = self.service.profile(int(user.get("id", 0) or 0), user)
        self.user_profile.set_profile(profile, primary_action="message", primary_label="发消息")
        self.details.setCurrentWidget(self.user_profile)
        self._open_compact_detail()

    def _select_group(self, item: QListWidgetItem) -> None:
        group = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(group, dict):
            return
        self._show_group_profile(group)
        self._open_compact_detail()

    def _show_group_profile(self, group: dict[str, Any]) -> None:
        self._selected_group = group
        self._selected_user = None
        self.group_avatar.set_avatar(str(group.get("name", "")), group=True)
        self.group_name.setText(str(group.get("name", "")))
        joined = bool(group.get("joined"))
        self.group_meta.setText("我的群聊" if joined else "可加入的群聊")
        self.group_primary.setText("进入群聊" if joined else "加入群聊")
        can_leave = joined and group.get("member_role") != "owner" and group.get("name") != "大厅"
        self.group_more.setVisible(can_leave)
        self.details.setCurrentWidget(self.group_profile)

    def _open_compact_detail(self) -> None:
        if self.width() < self.COMPACT_BREAKPOINT:
            self._compact_detail_open = True
            self._apply_responsive_layout()

    def _show_compact_directory(self) -> None:
        self._compact_detail_open = False
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        compact = self.width() < self.COMPACT_BREAKPOINT
        self.directory_back.setVisible(compact and self._compact_detail_open)
        if not compact:
            self.directory.setMinimumWidth(300)
            self.directory.setMaximumWidth(360)
            self.directory.show()
            self.details_host.show()
            return
        if self._compact_detail_open:
            self.directory.hide()
            self.details_host.show()
        else:
            self.directory.setMinimumWidth(0)
            self.directory.setMaximumWidth(16_777_215)
            self.directory.show()
            self.details_host.hide()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._apply_responsive_layout()

    def _open_user(self, item: QListWidgetItem) -> None:
        user = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(user, dict):
            return
        peer_id = int(user["id"])
        low, high = sorted((self.current_user_id, peer_id))
        self.conversation_requested.emit(
            ConversationKey("private", f"dm:{low}:{high}"), str(user.get("display_name") or user["username"])
        )

    def _open_group(self, item: QListWidgetItem) -> None:
        group = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(group, dict):
            return
        if group.get("joined"):
            self.conversation_requested.emit(ConversationKey("group", str(group["id"])), str(group["name"]))
        else:
            self.service.join_group(int(group["id"]))

    def _profile_action(self, action: str, profile: object) -> None:
        if action != "message" or getattr(profile, "user_id", None) is None:
            return
        user_id = int(profile.user_id)
        low, high = sorted((self.current_user_id, user_id))
        self.conversation_requested.emit(
            ConversationKey("private", f"dm:{low}:{high}"), str(profile.display_name)
        )

    def _create(self) -> None:
        dialog = TextPrompt(self, "创建群组", "群名称")
        if dialog.exec() and dialog.value():
            self.service.create_group(dialog.value())

    def _group_primary(self) -> None:
        if not self._selected_group:
            return
        if self._selected_group.get("joined"):
            self.conversation_requested.emit(
                ConversationKey("group", str(self._selected_group["id"])),
                str(self._selected_group["name"]),
            )
        else:
            self.service.join_group(int(self._selected_group["id"]))

    def _group_menu(self) -> None:
        group = self._selected_group
        if not group or not group.get("joined") or group.get("member_role") == "owner" or group.get("name") == "大厅":
            return
        menu = QMenu(self)
        leave = menu.addAction("退出群聊")
        leave.setObjectName("DangerAction")
        if menu.exec(self.group_more.mapToGlobal(self.group_more.rect().bottomLeft())) == leave:
            self.service.leave_group(int(group["id"]))


class TextPrompt(QDialog):
    def __init__(self, parent: QWidget, title: str, placeholder: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(self.input)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self.input.text().strip()
