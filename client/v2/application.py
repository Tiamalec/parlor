"""PySide6 application shell and V2 product pages."""

from __future__ import annotations

import base64
import mimetypes
import os
import queue
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.models import AppState, ConversationKey, ConversationSummary
from client.network import NetworkClient
from client.service import ChatService
from client.store import AppStore

from .theme import ThemeMode, ThemeSnapshot, active_theme_manager, install_theme_manager
from .details import ConversationDetailsPanel
from .profile_widgets import ProfilePopover, UserProfileCard
from .widgets import (
    Avatar,
    ConversationCard,
    EmptyState,
    MentionTextEdit,
    MessageBubble,
    UserAvatar,
    configure_scroll_surface,
    configure_surface,
)


MAX_IMAGE_SIZE = 2 * 1024 * 1024
MAX_AVATAR_SIZE = 2 * 1024 * 1024


def clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


class AuthPage(QWidget):
    submitted = Signal(str, str, str)

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setObjectName("Surface")
        card.setMaximumWidth(440)
        card.setMinimumWidth(380)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(38, 34, 38, 34)
        layout.setSpacing(14)
        mark = QLabel("✦")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setObjectName("AuthBrandMark")
        title = QLabel("AI Agent 智能聊天室")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle = QLabel("登录后继续你的协作与对话")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(mark)
        layout.addWidget(title)
        layout.addWidget(self.subtitle)
        mode_row = QHBoxLayout()
        self.login_mode = QPushButton("登录")
        self.register_mode = QPushButton("注册")
        self.login_mode.setCheckable(True)
        self.register_mode.setCheckable(True)
        self.login_mode.setChecked(True)
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.login_mode)
        modes.addButton(self.register_mode)
        mode_row.addWidget(self.login_mode)
        mode_row.addWidget(self.register_mode)
        layout.addLayout(mode_row)
        self.username = QLineEdit()
        self.username.setPlaceholderText("用户名")
        self.password = QLineEdit()
        self.password.setPlaceholderText("密码（至少 8 位）")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.error = QLabel("")
        self.error.setObjectName("Danger")
        self.error.setWordWrap(True)
        self.submit = QPushButton("登录")
        self.submit.setObjectName("Primary")
        self.status = QLabel(f"正在连接 {host}:{port}…")
        self.status.setObjectName("Muted")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.username)
        layout.addWidget(self.password)
        layout.addWidget(self.error)
        layout.addWidget(self.submit)
        layout.addWidget(self.status)
        outer.addWidget(card)
        self.login_mode.clicked.connect(self._update_mode)
        self.register_mode.clicked.connect(self._update_mode)
        self.submit.clicked.connect(self._submit)
        self.password.returnPressed.connect(self._submit)

    def _update_mode(self) -> None:
        registering = self.register_mode.isChecked()
        self.submit.setText("创建账号" if registering else "登录")
        self.subtitle.setText("创建账号并加入大厅" if registering else "登录后继续你的协作与对话")
        self.error.clear()

    def _submit(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not 3 <= len(username) <= 20:
            self.error.setText("用户名需要 3–20 个字符")
            return
        if len(password) < 8:
            self.error.setText("密码至少需要 8 个字符")
            return
        self.error.clear()
        self.submit.setEnabled(False)
        mode = "register" if self.register_mode.isChecked() else "login"
        self.submitted.emit(mode, username, password)

    def update_state(self, topic: str, state: AppState) -> None:
        self.status.setText(state.connection_text)
        self.submit.setEnabled(state.connected and not any(action.startswith("auth.") for action in state.pending.values()))
        if topic in {"auth", "error"} and state.last_error:
            self.error.setText(state.last_error)


class ConversationPane(QFrame):
    selected = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Surface")
        self.setMinimumWidth(280)
        self.setMaximumWidth(340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 12)
        title = QLabel("消息")
        title.setObjectName("PageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索会话")
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.cards = QVBoxLayout(self.content)
        self.cards.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(2)
        configure_scroll_surface(self.scroll, self.content)
        self.scroll.setWidget(self.content)
        layout.addWidget(title)
        layout.addWidget(self.search)
        layout.addWidget(self.scroll, 1)
        self.search.textChanged.connect(self._filter)
        self._summaries: list[ConversationSummary] = []

    def set_conversations(self, summaries: list[ConversationSummary]) -> None:
        self._summaries = summaries
        self._filter(self.search.text())

    def _filter(self, keyword: str) -> None:
        clear_layout(self.cards)
        clean = keyword.strip().lower()
        visible = [item for item in self._summaries if clean in item.title.lower() or clean in item.preview.lower()]
        if not visible:
            self.cards.addWidget(EmptyState("暂无会话", "从通讯录选择用户，或加入一个群聊"))
            return
        for summary in visible:
            card = ConversationCard(summary)
            card.activated.connect(self.selected)
            self.cards.addWidget(card)


class DetailsScrim(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("DetailsScrim")

    def mousePressEvent(self, event: Any) -> None:
        super().mousePressEvent(event)
        self.clicked.emit()


class ChatPane(QWidget):
    def __init__(self, service: ChatService, store: AppStore) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        self.key: ConversationKey | None = None
        self.title_text = ""
        self._focus_message_id: int | None = None
        self._message_rows: dict[int, QWidget] = {}
        self.root_layout = QHBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        main = QFrame()
        main.setObjectName("Surface")
        layout = QVBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(18, 12, 12, 12)
        self.avatar = Avatar("会话")
        labels = QVBoxLayout()
        self.title = QLabel("选择一个会话")
        self.title.setObjectName("SectionTitle")
        self.subtitle = QLabel("消息、成员与 Agent 协作都会显示在这里")
        self.subtitle.setObjectName("Muted")
        labels.addWidget(self.title)
        labels.addWidget(self.subtitle)
        self.search_button = QPushButton("搜索")
        self.search_button.setObjectName("Ghost")
        self.details_button = QPushButton("详情")
        self.details_button.setObjectName("Ghost")
        header_row.addWidget(self.avatar)
        header_row.addLayout(labels)
        header_row.addStretch()
        header_row.addWidget(self.search_button)
        header_row.addWidget(self.details_button)
        self.search_panel = QFrame()
        search_row = QHBoxLayout(self.search_panel)
        search_row.setContentsMargins(16, 8, 16, 8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索当前会话")
        search_action = QPushButton("查找")
        search_action.setObjectName("Primary")
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(search_action)
        self.search_panel.hide()
        self.search_results = QListWidget()
        self.search_results.setMaximumHeight(180)
        self.search_results.hide()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.message_content = QWidget()
        self.messages = QVBoxLayout(self.message_content)
        self.messages.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.messages.setContentsMargins(18, 16, 18, 16)
        self.messages.setSpacing(8)
        configure_scroll_surface(self.scroll, self.message_content)
        self.scroll.setWidget(self.message_content)
        composer = QWidget()
        composer.setObjectName("Composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 10, 14, 12)
        toolbar = QHBoxLayout()
        self.emoji = QPushButton("☺ 表情")
        self.image = QPushButton("图片")
        self.ai = QPushButton("✦ AI")
        for button in (self.emoji, self.image, self.ai):
            button.setObjectName("Ghost")
            toolbar.addWidget(button)
        toolbar.addStretch()
        input_row = QHBoxLayout()
        self.mention_list = QListWidget()
        self.mention_list.setObjectName("MentionCandidates")
        self.mention_list.setMaximumHeight(210)
        self.mention_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.mention_list.hide()
        self.compose = MentionTextEdit()
        self.compose.setPlaceholderText("输入 @ 选择群成员或 AI Agent")
        self.compose.setMaximumHeight(110)
        self.send = QPushButton("发送")
        self.send.setObjectName("Primary")
        self.send.setFixedWidth(88)
        input_row.addWidget(self.compose, 1)
        input_row.addWidget(self.send)
        composer_layout.addLayout(toolbar)
        composer_layout.addWidget(self.mention_list)
        composer_layout.addLayout(input_row)
        layout.addWidget(header)
        layout.addWidget(self.search_panel)
        layout.addWidget(self.search_results)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(composer)
        self.details = ConversationDetailsPanel(service)
        self.profile_popover = ProfilePopover(service, self)
        self.details_scrim = DetailsScrim(self)
        self.details_scrim.hide()
        self.details.hide()
        self.root_layout.addWidget(main, 1)
        self.root_layout.addWidget(self.details)
        self.send.clicked.connect(self._send)
        self.image.clicked.connect(self._send_image)
        self.search_button.clicked.connect(lambda: self.search_panel.setVisible(not self.search_panel.isVisible()))
        search_action.clicked.connect(self._search)
        self.search_input.returnPressed.connect(self._search)
        self.details_button.clicked.connect(self._toggle_details)
        self.details.close_requested.connect(self._hide_details)
        self.details_scrim.clicked.connect(self._hide_details)
        self.compose.mention_query.connect(self._show_mentions)
        self.compose.mention_closed.connect(self._hide_mentions)
        self.compose.mention_key.connect(self._mention_key)
        self.mention_list.itemClicked.connect(self._choose_mention)
        QShortcut(QKeySequence("Ctrl+Return"), self.compose, activated=self._send)
        QShortcut(QKeySequence("Meta+Return"), self.compose, activated=self._send)
        QShortcut(QKeySequence("Escape"), self, activated=self._hide_details)
        self._emoji_menu()
        self._ai_menu()

    def _emoji_menu(self) -> None:
        menu = QMenu(self)
        for emoji in ("😀", "😂", "😍", "👍", "🎉", "❤️", "🤔", "😢"):
            action = menu.addAction(emoji)
            action.triggered.connect(lambda _checked=False, value=emoji: self.compose.insertPlainText(value))
        self.emoji.setMenu(menu)

    def _ai_menu(self) -> None:
        menu = QMenu(self)
        actions = {
            "总结最近消息": "总结最近 50 条消息",
            "提取待办": "提取最近 50 条消息中的待办",
            "向 Agent 提问": "@AI Agent ",
        }
        for label, prompt in actions.items():
            action = menu.addAction(label)
            if prompt.startswith("@AI Agent"):
                action.triggered.connect(self._insert_agent_mention)
            else:
                action.triggered.connect(lambda _checked=False, value=prompt: self._invoke(value))
        self.ai.setMenu(menu)

    def set_conversation(self, key: ConversationKey, title: str) -> None:
        self.key = key
        self.title_text = title
        self.title.setText(title)
        self.subtitle.setText("群聊" if key.kind == "group" else "私聊")
        self.avatar.set_avatar(title)
        self._hide_mentions()
        self._hide_details()
        self.details.set_context(key, title, self.store.state)
        self.render_messages()

    def render_messages(self) -> None:
        clear_layout(self.messages)
        self._message_rows.clear()
        if not self.key:
            self.messages.addWidget(EmptyState("选择一个会话", "从会话列表或通讯录开始聊天"))
            return
        records = self.store.state.messages.get(self.key, [])
        if not records:
            self.messages.addWidget(EmptyState("还没有消息", "发一条消息开启这段对话"))
            return
        my_id = str(self.store.state.current_user.get("id", ""))
        for message in records:
            mine = str(message.get("sender_id", "")) == my_id
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            bubble = MessageBubble(message, mine=mine)
            bubble.recall_requested.connect(self.service.recall)
            bubble.media_requested.connect(self.service.fetch_media)
            is_agent = message.get("sender_kind") == "agent" or message.get("message_type") == "agent"
            sender_id = int(message.get("sender_id", 0) or 0)
            sender_name = "AI Agent" if is_agent else str(message.get("sender_name", "未知用户"))
            fallback = {
                "id": sender_id,
                "username": sender_name,
                "kind": "agent" if is_agent else "user",
            }
            profile = self.service.profile(None if is_agent else sender_id, fallback)
            avatar = UserAvatar(self.service, profile, 36, clickable=True)
            avatar.activated.connect(self._show_message_profile)
            if mine:
                row_layout.addStretch()
                row_layout.addWidget(bubble)
                row_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
            else:
                row_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
                row_layout.addWidget(bubble)
                row_layout.addStretch()
            self.messages.addWidget(row)
            message_id = int(message.get("id", message.get("message_id", 0)) or 0)
            if message_id:
                self._message_rows[message_id] = row
        focused = self._message_rows.get(self._focus_message_id or 0)
        if focused:
            QTimer.singleShot(0, lambda widget=focused: self.scroll.ensureWidgetVisible(widget, 0, 80))
            self._focus_message_id = None
        elif self._focus_message_id is None:
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def focus_message(self, message_id: int) -> None:
        self._focus_message_id = message_id
        self.render_messages()

    def update_state(self, topic: str, state: AppState) -> None:
        if topic in {"messages", "agent_result", "history", "media", "avatar", "profile"}:
            if topic in {"avatar", "profile"}:
                self.profile_popover.hide()
            self.render_messages()
        if topic in {"details", "avatar", "profile", "agent", "pending", "error"}:
            self.details.set_context(self.key, self.title_text, state, topic)
        if topic == "search" and self.key:
            self.search_results.clear()
            for item in state.search_results.get(self.key, []):
                content = str(item.get("content", ""))
                self.search_results.addItem(f"{item.get('sender_name', '')}: {content}")
            self.search_results.setVisible(self.search_results.count() > 0)
        self.subtitle.setText(("群聊" if self.key and self.key.kind == "group" else "私聊") + (" · Agent 在线" if state.agent_online else " · Agent 离线")) if self.key else None

    def _show_message_profile(self, profile: object, anchor: QWidget) -> None:
        self.profile_popover.show_for(anchor, profile)

    def _toggle_details(self) -> None:
        if self.details.isVisible():
            self._hide_details()
        else:
            self.details.show()
            self._layout_details()

    def _hide_details(self) -> None:
        self.details.hide()
        self.details_scrim.hide()

    def _layout_details(self) -> None:
        if not hasattr(self, "details"):
            return
        should_dock = self.width() >= 1000
        in_layout = self.root_layout.indexOf(self.details) >= 0
        was_visible = self.details.isVisible()
        if should_dock and not in_layout:
            self.details.hide()
            self.root_layout.addWidget(self.details)
        elif not should_dock and in_layout:
            self.root_layout.removeWidget(self.details)
            self.details.setParent(self)
        if should_dock:
            self.details_scrim.hide()
        else:
            width = min(320, max(300, self.width() - 24))
            if was_visible:
                self.details_scrim.setGeometry(self.rect())
                self.details_scrim.show()
                self.details_scrim.raise_()
            self.details.setGeometry(self.width() - width, 0, width, self.height())
            self.details.raise_()
        if was_visible:
            self.details.show()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._layout_details()

    def _send(self) -> None:
        if not self.key:
            return
        raw_text = self.compose.toPlainText()
        if not raw_text.strip():
            return
        self.service.send_text(self.key, raw_text, self.compose.mentions())
        self.compose.clear()
        self._hide_mentions()

    def _members(self) -> list[dict[str, Any]]:
        if not self.key or self.key.kind != "group":
            return []
        return self.store.state.members_by_group.get(self.key.identifier, self.store.state.members)

    def _show_mentions(self, query: str) -> None:
        if not self.key or self.key.kind != "group":
            self._hide_mentions()
            return
        folded = query.casefold()
        candidates = [member for member in self._members() if folded in str(member.get("username", "")).casefold()]
        self.mention_list.clear()
        for member in candidates:
            item = QListWidgetItem(str(member.get("username", "")))
            item.setSizeHint(QSize(0, 38))
            item.setData(Qt.ItemDataRole.UserRole, dict(member))
            is_agent = member.get("kind") == "agent"
            profile = self.service.profile(None if is_agent else int(member.get("id", 0) or 0), member)
            avatar = UserAvatar(self.service, profile, 30)
            item.setIcon(QIcon(avatar.pixmap()))
            if is_agent:
                item.setText("AI Agent  ·  智能助手")
            self.mention_list.addItem(item)
        visible = self.mention_list.count() > 0
        if visible:
            self.mention_list.setFixedHeight(min(210, self.mention_list.count() * 38 + 4))
        self.mention_list.setVisible(visible)
        self.compose.mention_popup_active = visible
        if visible:
            self.mention_list.setCurrentRow(0)

    def _hide_mentions(self) -> None:
        self.mention_list.hide()
        self.compose.mention_popup_active = False

    def _mention_key(self, action: int) -> None:
        if action == 2:
            self._hide_mentions()
            return
        if action == 0:
            item = self.mention_list.currentItem()
            if item:
                self._choose_mention(item)
            return
        count = self.mention_list.count()
        if count:
            self.mention_list.setCurrentRow((self.mention_list.currentRow() + action) % count)

    def _choose_mention(self, item: QListWidgetItem) -> None:
        member = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(member, dict):
            self.compose.insert_mention(member)
            self.compose.setFocus()

    def _insert_agent_mention(self) -> None:
        if not self.key or self.key.kind != "group":
            return
        self.compose.setFocus()
        self.compose.insertPlainText("@")
        self.compose._update_mention_query()
        agent = next((member for member in self._members() if member.get("kind") == "agent"), None)
        if agent:
            self.compose.insert_mention(agent)

    def _invoke(self, prompt: str) -> None:
        if self.key:
            self.service.invoke_agent(self.key, prompt)

    def _search(self) -> None:
        if self.key and self.search_input.text().strip():
            self.service.search(self.key, self.search_input.text().strip())

    def _send_image(self) -> None:
        if not self.key:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp)")
        if not path:
            return
        size = os.path.getsize(path)
        if size > MAX_IMAGE_SIZE:
            QMessageBox.warning(self, "图片过大", "图片不能超过 2 MiB")
            return
        raw = Path(path).read_bytes()
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.service.send_image(self.key, Path(path).name, mime, base64.b64encode(raw).decode("ascii"), len(raw))


class ContactsPage(QWidget):
    conversation_requested = Signal(object, str)

    def __init__(self, service: ChatService) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.current_user_id = 0
        self._selected_user: dict[str, Any] | None = None
        self._selected_group: dict[str, Any] | None = None
        root = QVBoxLayout(self)
        title = QLabel("通讯录")
        title.setObjectName("PageTitle")
        subtitle = QLabel("联系人、群聊与资料")
        subtitle.setObjectName("Muted")
        body = QHBoxLayout()
        body.setSpacing(12)
        directory = QFrame()
        directory.setObjectName("Surface")
        directory.setFixedWidth(330)
        directory_layout = QVBoxLayout(directory)
        directory_layout.setContentsMargins(12, 12, 12, 12)
        tabs = QHBoxLayout()
        self.people_tab = QPushButton("联系人")
        self.groups_tab = QPushButton("群聊")
        for button in (self.people_tab, self.groups_tab):
            button.setCheckable(True)
            tabs.addWidget(button)
        self.people_tab.setChecked(True)
        directory_layout.addLayout(tabs)
        self.directory_stack = QStackedWidget()
        self.users = QListWidget()
        self.users.setObjectName("DirectoryList")
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
        groups_layout.addLayout(group_heading)
        groups_layout.addWidget(self.groups)
        self.directory_stack.addWidget(self.users)
        self.directory_stack.addWidget(groups_page)
        directory_layout.addWidget(self.directory_stack, 1)

        self.details = QStackedWidget()
        self.empty_details = EmptyState("选择联系人或群聊", "查看资料并开始会话")
        self.user_profile = UserProfileCard(service)
        self.user_profile.action_requested.connect(self._profile_action)
        self.group_profile = QFrame()
        self.group_profile.setObjectName("ProfilePane")
        group_detail = QVBoxLayout(self.group_profile)
        group_detail.setContentsMargins(28, 32, 28, 28)
        self.group_avatar = Avatar("群聊", 68)
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
        body.addWidget(directory)
        body.addWidget(self.details, 1)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(body, 1)
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

    def update_state(self, topic: str, state: AppState) -> None:
        self.current_user_id = int(state.current_user.get("id", 0) or 0)
        if topic in {"auth", "contacts", "profile", "avatar"}:
            self.users.clear()
            my_id = str(state.current_user.get("id", ""))
            for user in state.users:
                if str(user.get("id")) == my_id:
                    continue
                profile = self.service.profile(int(user.get("id", 0) or 0), user)
                item = QListWidgetItem(str(user.get("username", "")))
                item.setData(Qt.ItemDataRole.UserRole, dict(user))
                avatar = UserAvatar(self.service, profile, 30)
                item.setIcon(QIcon(avatar.pixmap()))
                item.setToolTip("在线" if user.get("online") else "离线")
                self.users.addItem(item)
            if self._selected_user:
                current = next(
                    (user for user in state.users if str(user.get("id")) == str(self._selected_user.get("id"))),
                    self._selected_user,
                )
                self._selected_user = dict(current)
                profile = self.service.profile(int(current.get("id", 0) or 0), current)
                self.user_profile.set_profile(profile, primary_action="message", primary_label="发消息")
        if topic in {"auth", "groups"}:
            self.groups.clear()
            joined = [group for group in state.groups if group.get("joined")]
            available = [group for group in state.groups if not group.get("joined")]
            self._add_group_section("我的群聊", joined)
            self._add_group_section("可加入群聊", available)
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

    def _add_group_section(self, title: str, groups: list[dict[str, Any]]) -> None:
        header = QListWidgetItem(title)
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        header.setData(Qt.ItemDataRole.UserRole, None)
        self.groups.addItem(header)
        for group in groups:
            item = QListWidgetItem(str(group.get("name", "")))
            item.setIcon(QIcon(Avatar(str(group.get("name", "")), 30).pixmap()))
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

    def _select_group(self, item: QListWidgetItem) -> None:
        group = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(group, dict):
            return
        self._show_group_profile(group)

    def _show_group_profile(self, group: dict[str, Any]) -> None:
        self._selected_group = group
        self._selected_user = None
        self.group_avatar.set_avatar(str(group.get("name", "")))
        self.group_name.setText(str(group.get("name", "")))
        joined = bool(group.get("joined"))
        self.group_meta.setText("我的群聊" if joined else "可加入的群聊")
        self.group_primary.setText("进入群聊" if joined else "加入群聊")
        can_leave = joined and group.get("member_role") != "owner" and group.get("name") != "大厅"
        self.group_more.setVisible(can_leave)
        self.details.setCurrentWidget(self.group_profile)

    def _open_user(self, item: QListWidgetItem) -> None:
        user = item.data(Qt.ItemDataRole.UserRole)
        peer_id = int(user["id"])
        low, high = sorted((self.current_user_id, peer_id))
        self.conversation_requested.emit(
            ConversationKey("private", f"dm:{low}:{high}"), str(user["username"])
        )

    def _open_group(self, item: QListWidgetItem) -> None:
        group = item.data(Qt.ItemDataRole.UserRole)
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


TASK_TYPE_LABELS = {
    "summary": "总结会话",
    "todo": "提取待办",
    "question": "回答问题",
    "search": "搜索并整理会话",
    "identity": "Agent 信息",
}
TASK_STATUS_LABELS = {"running": "进行中", "succeeded": "已完成", "failed": "失败"}


class AgentTaskCard(QFrame):
    result_requested = Signal(object, int)

    def __init__(self, activity: dict[str, Any], title: str) -> None:
        super().__init__()
        self.setObjectName("TaskRow")
        key = ConversationKey(str(activity.get("conversation_type", "")), str(activity.get("conversation_id", "")))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        text = QVBoxLayout()
        heading = QLabel(TASK_TYPE_LABELS.get(str(activity.get("task_type", "")), "AI 任务"))
        heading.setObjectName("MemberName")
        started = float(activity.get("started_at", 0) or 0)
        requester = str(activity.get("requested_by_name") or "系统")
        meta = QLabel(f"{title} · {requester} · {time.strftime('%m-%d %H:%M', time.localtime(started)) if started else '时间未知'}")
        meta.setObjectName("Muted")
        text.addWidget(heading)
        text.addWidget(meta)
        error_lines = str(activity.get("error") or "").splitlines()
        error = error_lines[0][:160] if error_lines else ""
        if activity.get("status") == "failed" and error:
            failure = QLabel(error)
            failure.setObjectName("Danger")
            failure.setWordWrap(True)
            text.addWidget(failure)
        layout.addLayout(text, 1)
        status = QLabel(TASK_STATUS_LABELS.get(str(activity.get("status", "")), "状态未知"))
        status.setObjectName("TaskStatus")
        layout.addWidget(status)
        result_id = int(activity.get("result_message_id", 0) or 0)
        if activity.get("status") == "succeeded" and result_id:
            result = QPushButton("查看结果")
            result.setObjectName("Ghost")
            result.clicked.connect(lambda: self.result_requested.emit(key, result_id))
            layout.addWidget(result)


class NewAgentTaskDialog(QDialog):
    def __init__(self, conversations: list[ConversationSummary], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建 AI 任务")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("会话"))
        self.scope = QComboBox()
        for conversation in conversations:
            self.scope.addItem(conversation.title, conversation.key)
        root.addWidget(self.scope)
        root.addWidget(QLabel("任务类型"))
        self.task_type = QComboBox()
        self.task_type.addItem("总结会话", "summary")
        self.task_type.addItem("提取待办", "todo")
        self.task_type.addItem("回答问题", "question")
        root.addWidget(self.task_type)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("回答问题时请输入问题")
        root.addWidget(self.prompt)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def request(self) -> tuple[ConversationKey | None, str]:
        key = self.scope.currentData()
        task_type = str(self.task_type.currentData())
        prompts = {
            "summary": "总结最近 50 条消息",
            "todo": "提取最近 50 条消息中的待办",
            "question": self.prompt.text().strip(),
        }
        return (key if isinstance(key, ConversationKey) else None, prompts.get(task_type, ""))


class AICenterPage(QWidget):
    result_requested = Signal(object, int)

    def __init__(self, service: ChatService, store: AppStore) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        root = QVBoxLayout(self)
        title_row = QHBoxLayout()
        title = QLabel("AI 任务中心")
        title.setObjectName("PageTitle")
        self.new_task = QPushButton("新建 AI 任务")
        self.new_task.setObjectName("Primary")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.new_task)
        self.status = QLabel("Agent 状态未知")
        self.status.setObjectName("Muted")
        filters = QHBoxLayout()
        self.status_filter = QComboBox()
        for label, value in (("全部状态", ""), ("进行中", "running"), ("已完成", "succeeded"), ("失败", "failed")):
            self.status_filter.addItem(label, value)
        self.scope_filter = QComboBox()
        self.scope_filter.addItem("全部会话", None)
        filters.addWidget(self.status_filter)
        filters.addWidget(self.scope_filter, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.task_content = QWidget()
        self.task_rows = QVBoxLayout(self.task_content)
        self.task_rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.task_rows.setSpacing(8)
        configure_scroll_surface(self.scroll, self.task_content, "canvas")
        self.scroll.setWidget(self.task_content)
        root.addLayout(title_row)
        root.addWidget(self.status)
        root.addLayout(filters)
        root.addWidget(self.scroll, 1)
        self.new_task.clicked.connect(self._new_task)
        self.status_filter.currentIndexChanged.connect(self._render)
        self.scope_filter.currentIndexChanged.connect(self._render)

    def update_state(self, topic: str, state: AppState) -> None:
        self.status.setText(f"Agent {'在线' if state.agent_online else '离线'}" + (f" · {state.agent_model}" if state.agent_model else ""))
        if topic not in {"auth", "activities", "agent", "conversations"}:
            return
        if topic in {"auth", "conversations"}:
            selected = self.scope_filter.currentData()
            self.scope_filter.clear()
            self.scope_filter.addItem("全部会话", None)
            for conversation in state.conversations:
                self.scope_filter.addItem(conversation.title, conversation.key)
            if selected:
                index = self.scope_filter.findData(selected)
                if index >= 0:
                    self.scope_filter.setCurrentIndex(index)
        self._render()

    def _conversation_title(self, key: ConversationKey) -> str:
        summary = next((item for item in self.store.state.conversations if item.key == key), None)
        return summary.title if summary else "历史会话"

    def _render(self) -> None:
        clear_layout(self.task_rows)
        status = str(self.status_filter.currentData() or "")
        scope = self.scope_filter.currentData()
        activities = [
            item for item in self.store.state.activities
            if (not status or item.get("status") == status)
            and (
                not isinstance(scope, ConversationKey)
                or (str(item.get("conversation_type")) == scope.kind and str(item.get("conversation_id")) == scope.identifier)
            )
        ]
        if not activities:
            self.task_rows.addWidget(EmptyState("暂无任务记录", "在聊天中使用 AI，或新建一个任务"))
            return
        for activity in activities:
            key = ConversationKey(str(activity.get("conversation_type", "")), str(activity.get("conversation_id", "")))
            card = AgentTaskCard(activity, self._conversation_title(key))
            card.result_requested.connect(self.result_requested)
            self.task_rows.addWidget(card)

    def _new_task(self) -> None:
        dialog = NewAgentTaskDialog(self.store.state.conversations, self)
        if not dialog.exec():
            return
        key, prompt = dialog.request()
        if not key or not prompt:
            QMessageBox.warning(self, "任务信息不完整", "请选择会话；回答问题时还需要输入问题。")
            return
        self.service.invoke_agent(key, prompt)


class AdminPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        root = QVBoxLayout(self)
        title = QLabel("管理员控制台")
        title.setObjectName("PageTitle")
        subtitle = QLabel("仅显示安全的运行概览与已授权审计信息")
        subtitle.setObjectName("Muted")
        cards = QHBoxLayout()
        self.metrics: dict[str, QLabel] = {}
        for key, label in (("online_users", "在线用户"), ("total_users", "总用户"), ("groups", "群组"), ("uptime_seconds", "运行时长")):
            frame = QFrame()
            frame.setObjectName("Surface")
            box = QVBoxLayout(frame)
            value = QLabel("—")
            value.setObjectName("MetricValue")
            caption = QLabel(label)
            caption.setObjectName("Muted")
            box.addWidget(value)
            box.addWidget(caption)
            cards.addWidget(frame)
            self.metrics[key] = value
        audit_title = QLabel("Agent 工具审计")
        audit_title.setObjectName("SectionTitle")
        self.audit = QTableWidget(0, 5)
        self.audit.setHorizontalHeaderLabels(["工具", "调用者", "会话", "状态", "结果数"])
        self.audit.horizontalHeader().setStretchLastSection(True)
        directories = QHBoxLayout()
        self.users = QListWidget()
        self.groups = QListWidget()
        user_frame = QFrame()
        user_frame.setObjectName("Surface")
        user_layout = QVBoxLayout(user_frame)
        user_layout.addWidget(QLabel("用户（只读）"))
        user_layout.addWidget(self.users)
        group_frame = QFrame()
        group_frame.setObjectName("Surface")
        group_layout = QVBoxLayout(group_frame)
        group_layout.addWidget(QLabel("群组（只读）"))
        group_layout.addWidget(self.groups)
        directories.addWidget(user_frame)
        directories.addWidget(group_frame)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(cards)
        root.addLayout(directories)
        root.addWidget(audit_title)
        root.addWidget(self.audit, 1)

    def update_state(self, topic: str, state: AppState) -> None:
        if topic in {"auth", "system"}:
            for key, label in self.metrics.items():
                value = state.system_status.get(key)
                if key == "uptime_seconds" and value is not None:
                    value = f"{int(value) // 60} 分钟"
                label.setText(str(value if value is not None else "—"))
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


class SettingsPage(QWidget):
    logout_requested = Signal()

    def __init__(self, service: ChatService, store: AppStore, host: str, port: int) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        root = QVBoxLayout(self)
        title = QLabel("设置与诊断")
        title.setObjectName("PageTitle")
        card = QFrame()
        card.setObjectName("Surface")
        content = QVBoxLayout(card)
        self.user = QLabel("未登录")
        profile_row = QHBoxLayout()
        initial_profile = self.service.profile(
            int(self.store.state.current_user.get("id", 0) or 0), self.store.state.current_user
        )
        self.avatar = UserAvatar(self.service, initial_profile, 58)
        profile_actions = QVBoxLayout()
        self.change_avatar = QPushButton("上传 / 修改头像")
        self.reset_avatar = QPushButton("恢复默认头像")
        self.reset_avatar.setObjectName("Ghost")
        profile_actions.addWidget(self.change_avatar)
        profile_actions.addWidget(self.reset_avatar)
        profile_row.addWidget(self.avatar)
        profile_row.addLayout(profile_actions)
        profile_row.addStretch()
        appearance_row = QHBoxLayout()
        appearance_label = QLabel("外观主题")
        self.theme_mode = QComboBox()
        self.theme_mode.addItem("跟随系统", ThemeMode.SYSTEM.value)
        self.theme_mode.addItem("浅色", ThemeMode.LIGHT.value)
        self.theme_mode.addItem("深色", ThemeMode.DARK.value)
        appearance_row.addWidget(appearance_label)
        appearance_row.addStretch()
        appearance_row.addWidget(self.theme_mode)
        manager = active_theme_manager()
        if manager:
            index = self.theme_mode.findData(manager.mode.value)
            self.theme_mode.setCurrentIndex(max(0, index))
            manager.themeChanged.connect(self._theme_snapshot_changed)
        else:
            self.theme_mode.setEnabled(False)
        self.theme_mode.currentIndexChanged.connect(self._change_theme)
        self.connection = QLabel(f"服务器 {host}:{port}")
        self.connection.setObjectName("Muted")
        shortcuts = QLabel("发送快捷键：Ctrl+Enter（macOS 同时支持 Cmd+Enter）")
        shortcuts.setObjectName("Muted")
        privacy = QLabel("诊断信息不会包含密码、密钥、图片数据或完整消息正文。")
        privacy.setObjectName("Muted")
        logout = QPushButton("退出登录")
        logout.setObjectName("DangerButton")
        content.addWidget(self.user)
        content.addLayout(profile_row)
        content.addLayout(appearance_row)
        content.addWidget(self.connection)
        content.addWidget(shortcuts)
        content.addWidget(privacy)
        content.addWidget(logout)
        content.addStretch()
        root.addWidget(title)
        root.addWidget(card, 1)
        logout.clicked.connect(self.logout_requested)
        self.change_avatar.clicked.connect(self._change_avatar)
        self.reset_avatar.clicked.connect(lambda: self.service.set_avatar())

    def _change_theme(self, _index: int) -> None:
        manager = active_theme_manager()
        value = self.theme_mode.currentData()
        if manager and value:
            manager.set_mode(str(value))

    def _sync_theme_mode(self, mode: ThemeMode) -> None:
        index = self.theme_mode.findData(mode.value)
        if index >= 0 and index != self.theme_mode.currentIndex():
            self.theme_mode.blockSignals(True)
            self.theme_mode.setCurrentIndex(index)
            self.theme_mode.blockSignals(False)

    def _theme_snapshot_changed(self, snapshot: ThemeSnapshot) -> None:
        self._sync_theme_mode(snapshot.mode)

    def update_state(self, topic: str, state: AppState) -> None:
        if topic in {"auth", "connection", "session", "profile", "avatar"}:
            self.user.setText(f"{state.current_user.get('username', '未登录')} · {state.current_user.get('role', 'user')}")
            self.connection.setText(state.connection_text)
            user_id = int(state.current_user.get("id", 0) or 0)
            profile = self.service.profile(user_id, state.current_user)
            self.avatar.refresh(profile)

    def _change_avatar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择头像", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp)")
        if not path:
            return
        size = os.path.getsize(path)
        if size > MAX_AVATAR_SIZE:
            QMessageBox.warning(self, "头像过大", "头像不能超过 2 MiB")
            return
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        raw = Path(path).read_bytes()
        self.service.set_avatar(mime, base64.b64encode(raw).decode("ascii"))


class Workspace(QWidget):
    logout_requested = Signal()

    def __init__(self, service: ChatService, store: AppStore, host: str, port: int) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self.rail = QFrame()
        self.rail.setObjectName("Rail")
        self.rail.setFixedWidth(78)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(8, 12, 8, 12)
        logo = QLabel("✦")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setObjectName("BrandMark")
        rail_layout.addWidget(logo)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index, (icon, label) in enumerate((("💬", "消息"), ("👥", "通讯"), ("✦", "AI"), ("🛡", "管理"), ("⚙", "设置"))):
            button = QPushButton(f"{icon}\n{label}")
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=index: self._navigate(page))
            self.nav_group.addButton(button)
            self.nav_buttons.append(button)
            rail_layout.addWidget(button)
            if index == 2:
                rail_layout.addStretch()
        self.nav_buttons[0].setChecked(True)
        self.pages = QStackedWidget()
        chat_root = QWidget()
        configure_surface(chat_root, "canvas")
        chat_layout = QHBoxLayout(chat_root)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(10)
        self.conversations = ConversationPane()
        self.chat = ChatPane(service, store)
        chat_layout.addWidget(self.conversations)
        chat_layout.addWidget(self.chat, 1)
        self.contacts = ContactsPage(service)
        self.ai = AICenterPage(service, store)
        self.admin = AdminPage()
        self.settings = SettingsPage(service, store, host, port)
        for page in (chat_root, self.contacts, self.ai, self.admin, self.settings):
            self.pages.addWidget(page)
        root.addWidget(self.rail)
        root.addWidget(self.pages, 1)
        self.conversations.selected.connect(self._select_conversation)
        self.contacts.conversation_requested.connect(self._open_from_contacts)
        self.ai.result_requested.connect(self._open_ai_result)
        self.settings.logout_requested.connect(self.logout_requested)

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if 0 <= index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)

    def _select_conversation(self, key: ConversationKey) -> None:
        summary = next((item for item in self.store.state.conversations if item.key == key), None)
        self._open_from_contacts(key, summary.title if summary else "会话")

    def _open_from_contacts(self, key: ConversationKey, title: str) -> None:
        self._navigate(0)
        self.chat.set_conversation(key, title)
        self.service.open_conversation(key)

    def _open_ai_result(self, key: ConversationKey, message_id: int) -> None:
        summary = next((item for item in self.store.state.conversations if item.key == key), None)
        self._open_from_contacts(key, summary.title if summary else "历史会话")
        self.chat.focus_message(message_id)
        self.service.load_result(key, message_id)

    def update_state(self, topic: str, state: AppState) -> None:
        self.nav_buttons[3].setVisible(state.current_user.get("role") == "admin")
        if topic in {"auth", "conversations"}:
            self.conversations.set_conversations(state.conversations)
        for page in (self.chat, self.contacts, self.ai, self.admin, self.settings):
            page.update_state(topic, state)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.conversations.setVisible(self.width() >= 860 or self.pages.currentIndex() != 0)


class MainWindow(QMainWindow):
    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.setWindowTitle("AI Agent 智能聊天室")
        self.resize(1180, 760)
        self.setMinimumSize(720, 520)
        self.network = NetworkClient(host, port)
        self.store = AppStore()
        self.service = ChatService(self.network, self.store)
        self.credentials: tuple[str, str] | None = None
        self.was_authenticated = False
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        self.stack = QStackedWidget()
        self.auth = AuthPage(host, port)
        self.workspace = Workspace(self.service, self.store, host, port)
        self.stack.addWidget(self.auth)
        self.stack.addWidget(self.workspace)
        root_layout.addWidget(self.stack)
        self.setCentralWidget(root)
        self.auth.submitted.connect(self._authenticate)
        self.workspace.logout_requested.connect(self._logout)
        self.store.subscribe(self._on_state)
        self.poll = QTimer(self)
        self.poll.setInterval(50)
        self.poll.timeout.connect(self._poll_events)
        self.poll.start()
        self.network.start()

    def _authenticate(self, mode: str, username: str, password: str) -> None:
        self.credentials = (username, password)
        if mode == "register":
            self.service.register(username, password)
        else:
            self.service.login(username, password)

    def _logout(self) -> None:
        self.was_authenticated = False
        self.credentials = None
        self.service.logout()
        self.store.clear_session()
        self.auth.password.clear()
        self.stack.setCurrentWidget(self.auth)

    def _poll_events(self) -> None:
        processed = 0
        while processed < 100:
            try:
                event = self.network.events.get_nowait()
            except queue.Empty:
                break
            self.store.handle_event(event)
            processed += 1

    def _on_state(self, topic: str, state: AppState) -> None:
        self.auth.update_state(topic, state)
        self.workspace.update_state(topic, state)
        if topic == "auth" and state.authenticated:
            self.was_authenticated = True
            self.stack.setCurrentWidget(self.workspace)
            self.service.bootstrap_workspace()
        elif topic == "connection" and state.connected and self.was_authenticated and self.credentials and not state.authenticated:
            self.service.login(*self.credentials)
        elif topic == "agent_result" and state.authenticated:
            self.service.request("agent.activity.list", {"limit": 100})
        elif topic == "error" and state.last_error:
            self.statusBar().showMessage(state.last_error, 5000)
        if topic == "agent_result" and state.authenticated:
            self.service.request("conversation.list")
        if topic in {"messages", "history"} and state.current_conversation:
            records = state.messages.get(state.current_conversation, [])
            ids = [
                int(item.get("id", item.get("message_id")))
                for item in records
                if item.get("id", item.get("message_id")) is not None
            ]
            if ids:
                payload = self.service.scope(state.current_conversation)
                payload["message_id"] = max(ids)
                self.service.request("history.read", payload)
                self.service.request("conversation.list")
        elif topic == "messages" and state.authenticated:
            self.service.request("conversation.list")

    def closeEvent(self, event: Any) -> None:
        self.network.close()
        super().closeEvent(event)


def run(host: str, port: int) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("Chatroom")
    app.setApplicationName("AI Agent 智能聊天室")
    install_theme_manager(app)
    window = MainWindow(host, port)
    window.show()
    raise SystemExit(app.exec())

