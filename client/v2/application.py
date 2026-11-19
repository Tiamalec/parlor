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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from client.models import AppState, ConversationKey, ConversationSummary
from client.network import NetworkClient
from client.service import ChatService
from client.store import AppStore

from .icons import NAVIGATION_ICONS, lucide_icon
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
        heading = QHBoxLayout()
        title = QLabel("消息")
        title.setObjectName("PageTitle")
        self.count = QLabel("0 个会话")
        self.count.setObjectName("Muted")
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(self.count)
        self.search = QLineEdit()
        self.search.setObjectName("ConversationSearch")
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
        layout.addLayout(heading)
        layout.addWidget(self.search)
        layout.addWidget(self.scroll, 1)
        self.search.textChanged.connect(self._filter)
        self._summaries: list[ConversationSummary] = []
        self._cards: dict[ConversationKey, ConversationCard] = {}
        self._selected_key: ConversationKey | None = None

    def set_conversations(self, summaries: list[ConversationSummary]) -> None:
        self._summaries = summaries
        self.count.setText(f"{len(summaries)} 个会话")
        self._filter(self.search.text())

    def set_selected(self, key: ConversationKey | None) -> None:
        self._selected_key = key
        for card_key, card in self._cards.items():
            card.set_selected(card_key == key)

    def _activate(self, key: ConversationKey) -> None:
        self.set_selected(key)
        self.selected.emit(key)

    def _filter(self, keyword: str) -> None:
        clear_layout(self.cards)
        self._cards.clear()
        clean = keyword.strip().lower()
        visible = [item for item in self._summaries if clean in item.title.lower() or clean in item.preview.lower()]
        if not visible:
            self.cards.addWidget(EmptyState("暂无会话", "从通讯录选择联系人，或加入一个群聊", "◇"))
            return
        for summary in visible:
            card = ConversationCard(summary)
            card.set_selected(summary.key == self._selected_key)
            card.activated.connect(self._activate)
            self._cards[summary.key] = card
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
    conversation_list_requested = Signal()

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
        header.setObjectName("ChatHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(18, 12, 12, 12)
        self.conversation_list_button = QPushButton("会话")
        self.conversation_list_button.setObjectName("Ghost")
        self.conversation_list_button.setAccessibleName("返回会话列表")
        self.conversation_list_button.hide()
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
        header_row.addWidget(self.conversation_list_button)
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
        self.search_results.setObjectName("SearchResults")
        self.search_results.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
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
        toolbar.setSpacing(4)
        self.emoji = QPushButton("☺ 表情")
        self.image = QPushButton("图片")
        self.ai = QPushButton("✦ AI")
        for button in (self.emoji, self.image, self.ai):
            button.setObjectName("ComposerTool")
            toolbar.addWidget(button)
        toolbar.addStretch()
        input_row = QHBoxLayout()
        self.mention_list = QListWidget()
        self.mention_list.setObjectName("MentionCandidates")
        self.mention_list.setMaximumHeight(210)
        self.mention_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.mention_list.hide()
        self.compose = MentionTextEdit()
        self.compose.setObjectName("ComposerInput")
        self.compose.setPlaceholderText("输入 @ 选择群成员或 AI Agent")
        self.compose.setMinimumHeight(68)
        self.compose.setMaximumHeight(104)
        self.send = QPushButton("发送")
        self.send.setObjectName("Primary")
        self.send.setFixedWidth(88)
        self.send.setMinimumHeight(42)
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
        self.conversation_list_button.clicked.connect(self.conversation_list_requested)
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
        self._refresh_subtitle()
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
            try:
                sender_id = 0 if is_agent else int(message.get("sender_id", 0) or 0)
            except (TypeError, ValueError):
                sender_id = 0
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
            if not self.search_results.count():
                self._set_search_status("未找到匹配消息")
            self.search_results.show()
        elif topic == "error" and self.search_panel.isVisible() and state.last_error:
            self._set_search_status(f"搜索失败：{state.last_error}")
            self.search_results.show()
        self._refresh_subtitle()

    def _refresh_subtitle(self) -> None:
        if not self.key:
            return
        parts = ["群聊" if self.key.kind == "group" else "私聊"]
        if self.key.kind == "group":
            member_count = len(self._members())
            if member_count:
                parts.append(f"{member_count} 位成员")
        parts.append("Agent 在线" if self.store.state.agent_online else "Agent 离线")
        self.subtitle.setText(" · ".join(parts))

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
        stick_to_bottom = False
        if hasattr(self, "scroll"):
            bar = self.scroll.verticalScrollBar()
            stick_to_bottom = bar.value() >= bar.maximum() - 24
        super().resizeEvent(event)
        self._layout_details()
        if stick_to_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

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
            item.setSizeHint(QSize(0, 44))
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
            self.mention_list.setFixedHeight(min(210, self.mention_list.count() * 44 + 4))
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
            self._set_search_status("正在搜索…")
            self.search_results.show()
            self.service.search(self.key, self.search_input.text().strip())

    def _set_search_status(self, text: str) -> None:
        self.search_results.clear()
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setSizeHint(QSize(0, 44))
        self.search_results.addItem(item)

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
    COMPACT_BREAKPOINT = 720

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
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(6)
        title = QLabel("通讯录")
        title.setObjectName("PageTitle")
        subtitle = QLabel("查找联系人与群聊，查看资料后开始协作")
        subtitle.setObjectName("Muted")
        body = QHBoxLayout()
        body.setSpacing(12)
        self.directory = QFrame()
        self.directory.setObjectName("Surface")
        self.directory.setMinimumWidth(280)
        self.directory.setMaximumWidth(340)
        directory_layout = QVBoxLayout(self.directory)
        directory_layout.setContentsMargins(12, 12, 12, 12)
        directory_layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("DirectorySearch")
        self.search.setPlaceholderText("搜索联系人")
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
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
        directory_layout.addLayout(tabs)
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
        details_layout.addWidget(self.directory_back, alignment=Qt.AlignmentFlag.AlignLeft)
        details_layout.addWidget(self.details, 1)
        body.addWidget(self.directory)
        body.addWidget(self.details_host, 1)
        root.addWidget(title)
        root.addWidget(subtitle)
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
        if topic in {"auth", "contacts", "profile", "avatar"}:
            self._users = [dict(user) for user in state.users]
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
            if str(user.get("id")) != my_id and clean in str(user.get("username", "")).casefold()
        ]
        for user in visible:
            profile = self.service.profile(int(user.get("id", 0) or 0), user)
            status = "在线" if user.get("online") else "离线"
            item = QListWidgetItem(f"{user.get('username', '')}\n{status}")
            item.setSizeHint(QSize(0, 52))
            item.setData(Qt.ItemDataRole.UserRole, dict(user))
            avatar = UserAvatar(self.service, profile, 32)
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
            item.setSizeHint(QSize(0, 52))
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
        self.group_avatar.set_avatar(str(group.get("name", "")))
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
            self.directory.setMinimumWidth(280)
            self.directory.setMaximumWidth(340)
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

    def _open_user(self, item: QListWidgetItem) -> None:
        user = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(user, dict):
            return
        peer_id = int(user["id"])
        low, high = sorted((self.current_user_id, peer_id))
        self.conversation_requested.emit(
            ConversationKey("private", f"dm:{low}:{high}"), str(user["username"])
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


TASK_TYPE_LABELS = {
    "summary": "总结会话",
    "todo": "提取待办",
    "question": "回答问题",
    "search": "搜索并整理会话",
    "identity": "Agent 信息",
}
TASK_STATUS_LABELS = {"running": "进行中", "succeeded": "已完成", "failed": "失败"}


def activity_time(value: object) -> str:
    try:
        stamp = float(value or 0)
    except (TypeError, ValueError):
        stamp = 0
    return time.strftime("%m-%d %H:%M", time.localtime(stamp)) if stamp else "时间未知"


class AgentTaskCard(QFrame):
    result_requested = Signal(object, int)
    details_requested = Signal(object, str)

    def __init__(self, activity: dict[str, Any], title: str) -> None:
        super().__init__()
        self.setObjectName("TaskRow")
        key = ConversationKey(str(activity.get("conversation_type", "")), str(activity.get("conversation_id", "")))
        self.setMinimumHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        heading = QLabel(TASK_TYPE_LABELS.get(str(activity.get("task_type", "")), "AI 任务"))
        heading.setObjectName("TaskTitle")
        requester = str(activity.get("requested_by_name") or "系统")
        meta = QLabel(f"{title} · {requester} · {activity_time(activity.get('started_at'))}")
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
        status_key = str(activity.get("status", ""))
        status = QLabel(TASK_STATUS_LABELS.get(status_key, "状态未知"))
        status.setObjectName("TaskStatus")
        status.setProperty("taskStatus", status_key)
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(status)
        details = QPushButton("详情")
        details.setObjectName("Ghost")
        details.setAccessibleName(f"查看{heading.text()}详情")
        details.clicked.connect(lambda: self.details_requested.emit(dict(activity), title))
        layout.addWidget(details)
        result_id = int(activity.get("result_message_id", 0) or 0)
        if activity.get("status") == "succeeded" and result_id:
            result = QPushButton("查看结果")
            result.setObjectName("Ghost")
            result.clicked.connect(lambda: self.result_requested.emit(key, result_id))
            layout.addWidget(result)


class AgentTaskDetailsDialog(QDialog):
    result_requested = Signal(object, int)

    def __init__(self, activity: dict[str, Any], title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("任务详情")
        self.setMinimumWidth(440)
        key = ConversationKey(str(activity.get("conversation_type", "")), str(activity.get("conversation_id", "")))
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        heading = QLabel(TASK_TYPE_LABELS.get(str(activity.get("task_type", "")), "AI 任务"))
        heading.setObjectName("SectionTitle")
        root.addWidget(heading)
        fields = (
            ("状态", TASK_STATUS_LABELS.get(str(activity.get("status", "")), "状态未知")),
            ("来源会话", title),
            ("发起者", str(activity.get("requested_by_name") or "系统")),
            ("开始时间", activity_time(activity.get("started_at"))),
            ("完成时间", activity_time(activity.get("finished_at")) if activity.get("finished_at") else "—"),
        )
        for label, value in fields:
            row = QHBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("Muted")
            content = QLabel(value)
            content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(caption)
            row.addStretch()
            row.addWidget(content)
            root.addLayout(row)
        error = str(activity.get("error") or "").strip()
        if error:
            failure = QLabel(error)
            failure.setObjectName("InlineNotice")
            failure.setProperty("noticeKind", "error")
            failure.setWordWrap(True)
            failure.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(failure)
        buttons = QHBoxLayout()
        buttons.addStretch()
        result_id = int(activity.get("result_message_id", 0) or 0)
        if activity.get("status") == "succeeded" and result_id:
            result = QPushButton("查看结果")
            result.setObjectName("Primary")
            result.clicked.connect(lambda: (self.result_requested.emit(key, result_id), self.accept()))
            buttons.addWidget(result)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        root.addLayout(buttons)


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
        self.prompt_label = QLabel("问题")
        self.prompt_label.setObjectName("Muted")
        root.addWidget(self.prompt_label)
        root.addWidget(self.prompt)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.task_type.currentIndexChanged.connect(self._sync_prompt)
        self._sync_prompt()

    def _sync_prompt(self) -> None:
        visible = self.task_type.currentData() == "question"
        self.prompt_label.setVisible(visible)
        self.prompt.setVisible(visible)

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
        self._task_action_pending = False
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(8)
        shell_row = QHBoxLayout()
        shell_row.addStretch()
        shell = QWidget()
        configure_surface(shell, "canvas")
        shell.setMaximumWidth(1100)
        content = QVBoxLayout(shell)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(8)
        title_row = QHBoxLayout()
        title = QLabel("AI 任务中心")
        title.setObjectName("PageTitle")
        self.new_task = QPushButton("新建 AI 任务")
        self.new_task.setObjectName("Primary")
        self.new_task.setEnabled(False)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.new_task)
        self.status = QLabel("Agent 状态未知")
        self.status.setObjectName("AgentBadge")
        self.notice = QLabel("")
        self.notice.setObjectName("InlineNotice")
        self.notice.hide()
        panel = QFrame()
        panel.setObjectName("Surface")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 12)
        panel_layout.setSpacing(8)
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.status_filter = QComboBox()
        for label, value in (("全部状态", ""), ("进行中", "running"), ("已完成", "succeeded"), ("失败", "failed")):
            self.status_filter.addItem(label, value)
        self.type_filter = QComboBox()
        self.type_filter.addItem("全部类型", "")
        for value, label in TASK_TYPE_LABELS.items():
            self.type_filter.addItem(label, value)
        self.scope_filter = QComboBox()
        self.scope_filter.addItem("全部会话", None)
        self.filter_summary = QLabel("0 个任务")
        self.filter_summary.setObjectName("Muted")
        filters.addWidget(self.status_filter)
        filters.addWidget(self.type_filter)
        filters.addWidget(self.scope_filter, 1)
        filters.addWidget(self.filter_summary)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.task_content = QWidget()
        self.task_rows = QVBoxLayout(self.task_content)
        self.task_rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.task_rows.setSpacing(0)
        configure_scroll_surface(self.scroll, self.task_content, "surface")
        self.scroll.setWidget(self.task_content)
        content.addLayout(title_row)
        content.addWidget(self.status, alignment=Qt.AlignmentFlag.AlignLeft)
        content.addWidget(self.notice)
        panel_layout.addLayout(filters)
        panel_layout.addWidget(self.scroll, 1)
        content.addWidget(panel, 1)
        shell_row.addWidget(shell, 1)
        shell_row.addStretch()
        root.addLayout(shell_row, 1)
        self.new_task.clicked.connect(self._new_task)
        self.status_filter.currentIndexChanged.connect(self._render)
        self.type_filter.currentIndexChanged.connect(self._render)
        self.scope_filter.currentIndexChanged.connect(self._render)

    def update_state(self, topic: str, state: AppState) -> None:
        self.status.setText(f"Agent {'在线' if state.agent_online else '离线'}" + (f" · {state.agent_model}" if state.agent_model else ""))
        self.status.setProperty("online", state.agent_online)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.new_task.setEnabled(state.agent_online and bool(state.conversations))
        self.new_task.setToolTip("" if self.new_task.isEnabled() else "需要 Agent 在线且至少存在一个会话")
        if topic in {"pending", "error", "activities", "agent", "auth", "ack"}:
            pending = any(action in {"agent.activity.list", "agent.invoke"} for action in state.pending.values())
            if pending:
                self.notice.setText("正在更新任务状态…")
                self.notice.setProperty("noticeKind", "info")
            elif topic == "error" and self._task_action_pending and state.last_error:
                self.notice.setText(f"任务操作失败：{state.last_error}")
                self.notice.setProperty("noticeKind", "error")
                self._task_action_pending = False
            elif topic == "ack" and self._task_action_pending:
                self.notice.setText("任务已提交，结果会显示在对应会话中。")
                self.notice.setProperty("noticeKind", "success")
                self._task_action_pending = False
            else:
                self.notice.clear()
            self.notice.setVisible(bool(self.notice.text()))
            self.notice.style().unpolish(self.notice)
            self.notice.style().polish(self.notice)
        if topic not in {"auth", "activities", "agent", "conversations", "pending", "error", "ack"}:
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
        task_type = str(self.type_filter.currentData() or "")
        scope = self.scope_filter.currentData()
        activities = [
            item for item in self.store.state.activities
            if (not status or item.get("status") == status)
            and (not task_type or item.get("task_type") == task_type)
            and (
                not isinstance(scope, ConversationKey)
                or (str(item.get("conversation_type")) == scope.kind and str(item.get("conversation_id")) == scope.identifier)
            )
        ]
        self.filter_summary.setText(f"{len(activities)} 个任务")
        if not activities:
            filtered = bool(status or task_type or isinstance(scope, ConversationKey))
            self.task_rows.addWidget(
                EmptyState(
                    "没有匹配的任务" if filtered else "暂无任务记录",
                    "调整筛选条件" if filtered else "在聊天中使用 AI，或新建一个任务",
                    "◇",
                )
            )
            return
        for activity in activities:
            key = ConversationKey(str(activity.get("conversation_type", "")), str(activity.get("conversation_id", "")))
            card = AgentTaskCard(activity, self._conversation_title(key))
            card.result_requested.connect(self.result_requested)
            card.details_requested.connect(self._show_details)
            self.task_rows.addWidget(card)

    def _show_details(self, activity: object, title: str) -> None:
        if not isinstance(activity, dict):
            return
        dialog = AgentTaskDetailsDialog(activity, title, self)
        dialog.result_requested.connect(self.result_requested)
        dialog.exec()

    def _new_task(self) -> None:
        dialog = NewAgentTaskDialog(self.store.state.conversations, self)
        if not dialog.exec():
            return
        key, prompt = dialog.request()
        if not key or not prompt:
            QMessageBox.warning(self, "任务信息不完整", "请选择会话；回答问题时还需要输入问题。")
            return
        self._task_action_pending = True
        self.notice.setText("正在提交任务…")
        self.notice.setProperty("noticeKind", "info")
        self.notice.show()
        if not self.service.invoke_agent(key, prompt):
            self._task_action_pending = False
            if self.store.state.last_error:
                self.notice.setText(f"任务操作失败：{self.store.state.last_error}")
                self.notice.setProperty("noticeKind", "error")


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
    COMPACT_BREAKPOINT = 760

    def __init__(self, service: ChatService, store: AppStore, host: str, port: int) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        self._profile_action_pending = False
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(6)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        subtitle = QLabel("管理账号、外观与当前可用的客户端选项")
        subtitle.setObjectName("Muted")
        self.compact_nav = QComboBox()
        self.compact_nav.setObjectName("SettingsCompactNav")
        self.compact_nav.hide()
        body = QHBoxLayout()
        body.setSpacing(12)
        sidebar = QFrame()
        sidebar.setObjectName("Surface")
        sidebar.setFixedWidth(190)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 8, 8, 8)
        self.settings_nav = QListWidget()
        self.settings_nav.setObjectName("SettingsNav")
        self.settings_nav.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        sidebar_layout.addWidget(self.settings_nav)
        self.settings_sidebar = sidebar
        self.settings_stack = QStackedWidget()
        self.settings_stack.setObjectName("SettingsContent")
        self.settings_stack.setMaximumWidth(760)
        categories = ("账号资料", "外观主题", "聊天偏好", "消息通知", "AI 助手", "连接与诊断")
        for category in categories:
            item = QListWidgetItem(category)
            item.setSizeHint(QSize(0, 44))
            self.settings_nav.addItem(item)
            self.compact_nav.addItem(category)

        account = self._add_settings_page("账号资料", "头像和账号操作会同步到当前服务端账号。")
        self.user = QLabel("未登录")
        self.user.setObjectName("SectionTitle")
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
        profile_section, profile_content = self._settings_section("个人资料")
        profile_content.addWidget(self.user)
        profile_content.addLayout(profile_row)
        self.profile_feedback = QLabel("")
        self.profile_feedback.setObjectName("InlineNotice")
        self.profile_feedback.setProperty("noticeKind", "info")
        self.profile_feedback.hide()
        profile_content.addWidget(self.profile_feedback)
        account.addWidget(profile_section)
        account_action, account_action_content = self._settings_section("账户操作", "退出后会返回登录页面，不会删除服务端数据。")
        self.logout = QPushButton("退出登录")
        self.logout.setObjectName("DangerButton")
        self.logout.setMaximumWidth(160)
        account_action_content.addWidget(self.logout, alignment=Qt.AlignmentFlag.AlignLeft)
        account.addWidget(account_action)
        account.addStretch()

        appearance = self._add_settings_page("外观主题", "主题偏好保存在本机，并可随系统外观自动切换。")
        appearance_row = QHBoxLayout()
        appearance_label = QLabel("外观主题")
        self.theme_mode = QComboBox()
        self.theme_mode.addItem("跟随系统", ThemeMode.SYSTEM.value)
        self.theme_mode.addItem("浅色", ThemeMode.LIGHT.value)
        self.theme_mode.addItem("深色", ThemeMode.DARK.value)
        appearance_row.addWidget(appearance_label)
        appearance_row.addStretch()
        appearance_row.addWidget(self.theme_mode)
        appearance_section, appearance_content = self._settings_section("主题")
        appearance_content.addLayout(appearance_row)
        self.theme_feedback = QLabel("")
        self.theme_feedback.setObjectName("InlineNotice")
        self.theme_feedback.setProperty("noticeKind", "success")
        self.theme_feedback.hide()
        appearance_content.addWidget(self.theme_feedback)
        appearance.addWidget(appearance_section)
        appearance.addStretch()
        manager = active_theme_manager()
        if manager:
            index = self.theme_mode.findData(manager.mode.value)
            self.theme_mode.setCurrentIndex(max(0, index))
            manager.themeChanged.connect(self._theme_snapshot_changed)
        else:
            self.theme_mode.setEnabled(False)
        self.theme_mode.currentIndexChanged.connect(self._change_theme)

        chat = self._add_settings_page("聊天偏好", "当前聊天行为由客户端统一定义。")
        chat_section, chat_content = self._settings_section("输入与显示")
        chat_content.addWidget(self._read_only_setting("发送快捷键", "Ctrl+Enter；macOS 同时支持 Cmd+Enter", "当前固定"))
        chat_content.addWidget(self._planned_setting("Enter 发送方式", "需要新增本地偏好和编辑器行为测试"))
        chat_content.addWidget(self._planned_setting("消息显示密度", "需要完成全局密度规范后再开放"))
        chat.addWidget(chat_section)
        chat.addStretch()

        notifications = self._add_settings_page("消息通知", "当前版本尚未接入桌面通知权限与持久化偏好。")
        notification_section, notification_content = self._settings_section("桌面通知")
        notification_content.addWidget(self._planned_setting("新消息通知", "需要系统通知权限、前后台状态与免打扰策略"))
        notification_content.addWidget(self._planned_setting("声音提醒", "需要跨平台声音资源和独立偏好"))
        notifications.addWidget(notification_section)
        notifications.addStretch()

        ai_page = self._add_settings_page("AI 助手", "展示当前 Agent 能力；任务创建仍在 AI 任务中心完成。")
        ai_section, ai_content = self._settings_section("当前 Agent")
        self.agent_setting_status = QLabel("状态未知")
        self.agent_setting_status.setObjectName("AgentBadge")
        self.agent_setting_model = QLabel("模型信息尚未加载")
        self.agent_setting_model.setObjectName("Muted")
        ai_content.addWidget(self.agent_setting_status, alignment=Qt.AlignmentFlag.AlignLeft)
        ai_content.addWidget(self.agent_setting_model)
        ai_content.addWidget(self._read_only_setting("群聊定时任务", "在对应群聊的详情面板中管理", "按群配置"))
        ai_content.addWidget(self._planned_setting("默认任务范围", "服务端尚未提供用户级 AI 偏好"))
        ai_page.addWidget(ai_section)
        ai_page.addStretch()

        connection_page = self._add_settings_page("连接与诊断", "连接参数来自启动配置，当前页面仅提供安全的只读状态。")
        connection_section, connection_content = self._settings_section("连接")
        self.connection = QLabel(self.store.state.connection_text)
        self.connection.setObjectName("SectionTitle")
        self.endpoint = QLabel(f"服务器 {host}:{port}")
        self.endpoint.setObjectName("Muted")
        connection_content.addWidget(self.connection)
        connection_content.addWidget(self.endpoint)
        connection_page.addWidget(connection_section)
        diagnostics_section, diagnostics_content = self._settings_section("诊断与隐私")
        privacy = QLabel("诊断信息不会包含密码、密钥、图片数据或完整消息正文。")
        privacy.setObjectName("Muted")
        privacy.setWordWrap(True)
        diagnostics_content.addWidget(privacy)
        diagnostics_content.addWidget(self._planned_setting("导出诊断报告", "需要先定义脱敏字段、文件格式与保存流程"))
        connection_page.addWidget(diagnostics_section)
        connection_page.addStretch()

        body.addWidget(sidebar)
        body.addWidget(self.settings_stack, 1)
        self.settings_gutter = QWidget()
        configure_surface(self.settings_gutter, "canvas")
        body.addWidget(self.settings_gutter)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(self.compact_nav)
        root.addLayout(body, 1)
        self.settings_nav.setCurrentRow(0)
        self.settings_nav.currentRowChanged.connect(self._select_section)
        self.compact_nav.currentIndexChanged.connect(self._select_section)
        self.logout.clicked.connect(self.logout_requested)
        self.change_avatar.clicked.connect(self._change_avatar)
        self.reset_avatar.clicked.connect(self._reset_avatar)

    def _add_settings_page(self, title: str, description: str) -> QVBoxLayout:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        helper = QLabel(description)
        helper.setObjectName("Muted")
        helper.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(helper)
        configure_scroll_surface(scroll, content, "canvas")
        scroll.setWidget(content)
        self.settings_stack.addWidget(scroll)
        return layout

    @staticmethod
    def _settings_section(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
        section = QFrame()
        section.setObjectName("SettingsSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 4, 0, 16)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        if description:
            helper = QLabel(description)
            helper.setObjectName("Muted")
            helper.setWordWrap(True)
            layout.addWidget(helper)
        return section, layout

    @staticmethod
    def _read_only_setting(title: str, description: str, value: str) -> QFrame:
        row = QFrame()
        row.setObjectName("SettingsRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 8, 0, 8)
        text = QVBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("MemberName")
        helper = QLabel(description)
        helper.setObjectName("Muted")
        helper.setWordWrap(True)
        text.addWidget(heading)
        text.addWidget(helper)
        badge = QLabel(value)
        badge.setObjectName("RoleBadge")
        layout.addLayout(text, 1)
        layout.addWidget(badge)
        return row

    @classmethod
    def _planned_setting(cls, title: str, description: str) -> QFrame:
        row = cls._read_only_setting(title, description, "待实现")
        badge = next((label for label in row.findChildren(QLabel) if label.text() == "待实现"), None)
        if badge:
            badge.setObjectName("PlannedBadge")
        return row

    def _select_section(self, index: int) -> None:
        if index < 0 or index >= self.settings_stack.count():
            return
        self.settings_stack.setCurrentIndex(index)
        if self.settings_nav.currentRow() != index:
            self.settings_nav.blockSignals(True)
            self.settings_nav.setCurrentRow(index)
            self.settings_nav.blockSignals(False)
        if self.compact_nav.currentIndex() != index:
            self.compact_nav.blockSignals(True)
            self.compact_nav.setCurrentIndex(index)
            self.compact_nav.blockSignals(False)

    def _change_theme(self, _index: int) -> None:
        manager = active_theme_manager()
        value = self.theme_mode.currentData()
        if manager and value:
            manager.set_mode(str(value))
            self.theme_feedback.setText("主题偏好已保存")
            self.theme_feedback.show()

    def _sync_theme_mode(self, mode: ThemeMode) -> None:
        index = self.theme_mode.findData(mode.value)
        if index >= 0 and index != self.theme_mode.currentIndex():
            self.theme_mode.blockSignals(True)
            self.theme_mode.setCurrentIndex(index)
            self.theme_mode.blockSignals(False)

    def _theme_snapshot_changed(self, snapshot: ThemeSnapshot) -> None:
        self._sync_theme_mode(snapshot.mode)

    def update_state(self, topic: str, state: AppState) -> None:
        if topic in {"auth", "connection", "session", "profile", "avatar", "agent", "error", "ack"}:
            self.user.setText(f"{state.current_user.get('username', '未登录')} · {state.current_user.get('role', 'user')}")
            self.connection.setText(state.connection_text)
            user_id = int(state.current_user.get("id", 0) or 0)
            profile = self.service.profile(user_id, state.current_user)
            self.avatar.refresh(profile)
            self.agent_setting_status.setText("Agent 在线" if state.agent_online else "Agent 离线")
            self.agent_setting_status.setProperty("online", state.agent_online)
            self.agent_setting_status.style().unpolish(self.agent_setting_status)
            self.agent_setting_status.style().polish(self.agent_setting_status)
            self.agent_setting_model.setText(f"当前模型：{state.agent_model}" if state.agent_model else "模型信息尚未加载")
        if topic == "avatar":
            self._profile_action_pending = False
            self.profile_feedback.setText("头像已更新")
            self.profile_feedback.setProperty("noticeKind", "success")
            self.profile_feedback.style().unpolish(self.profile_feedback)
            self.profile_feedback.style().polish(self.profile_feedback)
            self.profile_feedback.show()
        elif topic == "ack" and self._profile_action_pending:
            self.profile_feedback.setText("头像设置已提交")
            self.profile_feedback.setProperty("noticeKind", "success")
            self.profile_feedback.style().unpolish(self.profile_feedback)
            self.profile_feedback.style().polish(self.profile_feedback)
            self.profile_feedback.show()
            self._profile_action_pending = False
        elif topic == "error" and self._profile_action_pending and state.last_error:
            self.profile_feedback.setText(f"操作失败：{state.last_error}")
            self.profile_feedback.setProperty("noticeKind", "error")
            self.profile_feedback.style().unpolish(self.profile_feedback)
            self.profile_feedback.style().polish(self.profile_feedback)
            self.profile_feedback.show()
            self._profile_action_pending = False

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
        self._save_avatar(mime, base64.b64encode(raw).decode("ascii"))

    def _reset_avatar(self) -> None:
        self._save_avatar(None, "")

    def _save_avatar(self, mime: str | None, encoded: str) -> None:
        self._profile_action_pending = True
        self.profile_feedback.setText("正在保存头像…")
        self.profile_feedback.setProperty("noticeKind", "info")
        self.profile_feedback.show()
        if not self.service.set_avatar(mime, encoded):
            self._profile_action_pending = False
            if self.store.state.last_error:
                self.profile_feedback.setText(f"操作失败：{self.store.state.last_error}")
                self.profile_feedback.setProperty("noticeKind", "error")

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        compact = self.width() < self.COMPACT_BREAKPOINT
        self.settings_sidebar.setVisible(not compact)
        self.compact_nav.setVisible(compact)
        self.settings_gutter.setVisible(not compact)
        self.settings_stack.setMaximumWidth(16_777_215 if compact else 760)


class NavigationButton(QToolButton):
    """Compact rail item with a themeable icon and optional unread badge."""

    def __init__(self, label: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.setObjectName("NavItem")
        self.setText(label)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(20, 20))
        self.setFixedHeight(56)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.badge = QLabel(self)
        self.badge.setObjectName("NavBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.badge.hide()
        self.refresh_icon()

    def refresh_icon(self) -> None:
        self.setIcon(lucide_icon(self.icon_name, 20))

    def set_badge(self, count: int) -> None:
        count = max(0, int(count))
        self.badge.setText("99+" if count > 99 else str(count))
        self.badge.setVisible(count > 0)
        self.setAccessibleDescription(f"{count} 条未读消息" if count else "")
        self.badge.adjustSize()
        self._position_badge()

    def _position_badge(self) -> None:
        self.badge.move(self.width() - self.badge.width() - 5, 3)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._position_badge()


class Workspace(QWidget):
    logout_requested = Signal()
    CHAT_SPLIT_BREAKPOINT = 1040

    def __init__(self, service: ChatService, store: AppStore, host: str, port: int) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        self._compact_list_open = False
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.rail = QFrame()
        self.rail.setObjectName("Rail")
        self.rail.setFixedWidth(76)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(6, 12, 6, 12)
        rail_layout.setSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[NavigationButton] = []
        navigation = (
            ("messages", "消息"),
            ("contacts", "通讯"),
            ("ai", "AI"),
            ("admin", "管理"),
            ("settings", "设置"),
        )
        for index, (icon_key, label) in enumerate(navigation):
            if index == 4:
                rail_layout.addStretch(1)
            button = NavigationButton(label, NAVIGATION_ICONS[icon_key], self.rail)
            button.clicked.connect(lambda _checked=False, page=index: self._navigate(page))
            self.nav_group.addButton(button)
            self.nav_buttons.append(button)
            rail_layout.addWidget(button)
        self.nav_buttons[0].setChecked(True)
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(self._refresh_navigation_icons)
        self.pages = QStackedWidget()
        self.chat_root = QWidget()
        configure_surface(self.chat_root, "canvas")
        self.chat_layout = QHBoxLayout(self.chat_root)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(10)
        self.conversations = ConversationPane()
        self.chat = ChatPane(service, store)
        self.chat_layout.addWidget(self.conversations)
        self.chat_layout.addWidget(self.chat, 1)
        self.contacts = ContactsPage(service)
        self.ai = AICenterPage(service, store)
        self.admin = AdminPage()
        self.settings = SettingsPage(service, store, host, port)
        for page in (self.chat_root, self.contacts, self.ai, self.admin, self.settings):
            self.pages.addWidget(page)
        root.addWidget(self.rail)
        root.addWidget(self.pages, 1)
        self.conversations.selected.connect(self._select_conversation)
        self.chat.conversation_list_requested.connect(self._show_compact_conversations)
        self.contacts.conversation_requested.connect(self._open_from_contacts)
        self.ai.result_requested.connect(self._open_ai_result)
        self.settings.logout_requested.connect(self.logout_requested)

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if 0 <= index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)
        if index == 0:
            self._apply_chat_layout()

    def _refresh_navigation_icons(self, *_args: object) -> None:
        for button in self.nav_buttons:
            button.refresh_icon()

    def _select_conversation(self, key: ConversationKey) -> None:
        self.conversations.set_selected(key)
        summary = next((item for item in self.store.state.conversations if item.key == key), None)
        self._open_from_contacts(key, summary.title if summary else "会话")

    def _open_from_contacts(self, key: ConversationKey, title: str) -> None:
        self._navigate(0)
        self.conversations.set_selected(key)
        self.chat.set_conversation(key, title)
        self._compact_list_open = False
        self._apply_chat_layout()
        self.service.open_conversation(key)

    def _show_compact_conversations(self) -> None:
        if self.width() >= self.CHAT_SPLIT_BREAKPOINT:
            return
        self._compact_list_open = True
        self._apply_chat_layout()

    def _apply_chat_layout(self) -> None:
        compact = self.width() < self.CHAT_SPLIT_BREAKPOINT
        self.chat.conversation_list_button.setVisible(compact)
        if not compact:
            self._compact_list_open = False
            self.conversations.setMinimumWidth(280)
            self.conversations.setMaximumWidth(340)
            self.conversations.show()
            self.chat.show()
            return
        if self._compact_list_open:
            self.conversations.setMinimumWidth(0)
            self.conversations.setMaximumWidth(16_777_215)
            self.conversations.show()
            self.chat.hide()
        else:
            self.conversations.hide()
            self.chat.show()

    def _open_ai_result(self, key: ConversationKey, message_id: int) -> None:
        summary = next((item for item in self.store.state.conversations if item.key == key), None)
        self._open_from_contacts(key, summary.title if summary else "历史会话")
        self.chat.focus_message(message_id)
        self.service.load_result(key, message_id)

    def update_state(self, topic: str, state: AppState) -> None:
        self.nav_buttons[3].setVisible(state.current_user.get("role") == "admin")
        self.nav_buttons[0].set_badge(sum(max(0, item.unread_count) for item in state.conversations))
        if topic in {"auth", "conversations"}:
            self.conversations.set_conversations(state.conversations)
            self.conversations.set_selected(state.current_conversation)
        for page in (self.chat, self.contacts, self.ai, self.admin, self.settings):
            page.update_state(topic, state)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_chat_layout()


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

