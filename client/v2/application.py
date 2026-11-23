"""PySide6 application shell and V2 product pages."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from client.models import AppState, ConversationKey, ConversationSummary
from client.network import NetworkClient
from client.service import ChatService
from client.store import AppStore
from client.drafts import Draft, DraftStore

from .icons import NAVIGATION_ICONS, brand_app_icon, brand_logo_icon, lucide_icon, lucide_icon_with_color
from .theme import (
    ThemeMode,
    ThemeSnapshot,
    active_theme_manager,
    current_tokens,
    install_theme_manager,
)
from .details import ConversationDetailsPanel
from .profile_widgets import ProfilePopover, UserProfileCard, build_profile_menu
from .stickers import LocalStickerLibrary, MAX_STICKER_SIZE, StickerPanel
from .widgets import (
    Avatar,
    ConversationCard,
    EmptyState,
    MentionTextEdit,
    MessageBubble,
    UserAvatar,
    avatar_pixmap,
    configure_scroll_surface,
    configure_surface,
    message_card_kind,
)


MAX_IMAGE_SIZE = 2 * 1024 * 1024
MAX_AVATAR_SIZE = 2 * 1024 * 1024
BOTTOM_DISTANCE_PX = 32
SMOOTH_SCROLL_MS = 220
DRAFT_DEBOUNCE_MS = 350
MESSAGE_DENSITY_KEY = "ui/message_density"
MESSAGE_DENSITY_SPACING = {"default": 2, "compact": 0}
MESSAGE_DENSITY_MARGINS = {"default": (18, 16, 18, 16), "compact": (14, 10, 14, 10)}
MESSAGE_DENSITY_COLUMN_SPACING = {"default": 3, "compact": 1}


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


class AuthPage(QWidget):
    submitted = Signal(str, str, str)
    HERO_BREAKPOINT = 1000

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self._host = host
        self._port = port
        self._password_hidden = True
        self._feature_icons: list[tuple[QLabel, str, str, int]] = []

        self.outer = QHBoxLayout(self)
        self.outer.setContentsMargins(32, 32, 32, 32)
        self.outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shell = QWidget()
        self.shell.setMaximumWidth(1000)
        self.shell_layout = QHBoxLayout(self.shell)
        self.shell_layout.setContentsMargins(0, 0, 0, 0)
        self.shell_layout.setSpacing(72)
        self.hero = self._build_hero()
        self.card = self._build_card()
        self.shell_layout.addWidget(self.hero, 1)
        self.shell_layout.addWidget(self.card, 0)
        self.outer.addWidget(self.shell, 0, Qt.AlignmentFlag.AlignCenter)

        self._apply_responsive_layout(self.width())
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(self._refresh_icons)
        self._refresh_icons()

        self.setTabOrder(self.login_mode, self.register_mode)
        self.setTabOrder(self.register_mode, self.username)
        self.setTabOrder(self.username, self.password)
        self.setTabOrder(self.password, self.submit)
        self.login_mode.clicked.connect(self._update_mode)
        self.register_mode.clicked.connect(self._update_mode)
        self.submit.clicked.connect(self._submit)
        self.password.returnPressed.connect(self._submit)
        self.username.returnPressed.connect(self.password.setFocus)
        self.username.textChanged.connect(self._clear_error)
        self.password.textChanged.connect(self._clear_error)
        self.username.setFocus()

    def _build_hero(self) -> QWidget:
        hero = QWidget()
        hero.setMaximumWidth(440)
        layout = QVBoxLayout(hero)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        self.brand_tile = QLabel()
        self.brand_tile.setObjectName("AuthBrandTile")
        self.brand_tile.setFixedSize(46, 46)
        self.brand_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        names = QVBoxLayout()
        names.setSpacing(1)
        wordmark = QLabel("Parlor")
        wordmark.setObjectName("AuthWordmark")
        wordmark_meta = QLabel("智能聊天室")
        wordmark_meta.setObjectName("AuthWordmarkMeta")
        names.addWidget(wordmark)
        names.addWidget(wordmark_meta)
        brand.addWidget(self.brand_tile, 0, Qt.AlignmentFlag.AlignVCenter)
        brand.addLayout(names)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(44)
        headline = QLabel("和团队对话，\n也向 Agent 提问")
        headline.setObjectName("AuthHeroTitle")
        layout.addWidget(headline)
        layout.addSpacing(14)
        intro = QLabel("一个安静的工作台，用于团队协作、任务推进与 AI 助手对话。")
        intro.setObjectName("AuthHeroText")
        intro.setWordWrap(True)
        intro.setMaximumWidth(380)
        layout.addWidget(intro)
        layout.addSpacing(36)
        features = (
            ("message-circle", "primary", "群聊、私信与实时协作"),
            ("bot", "agent", "Agent 对话与 AI 任务"),
            ("users", "primary", "团队通讯录与成员状态"),
        )
        for icon_name, color_key, text in features:
            row = QHBoxLayout()
            row.setSpacing(12)
            icon = QLabel()
            icon.setFixedSize(20, 20)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = QLabel(text)
            label.setObjectName("AuthFeatureText")
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addSpacing(14)
            self._feature_icons.append((icon, icon_name, color_key, 18))
        layout.addStretch(1)
        return hero

    def _build_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Surface")
        card.setFixedWidth(408)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 30, 32, 26)
        layout.setSpacing(0)

        self.heading = QLabel("欢迎回来")
        self.heading.setObjectName("AuthHeading")
        self.subtitle = QLabel("登录后继续你的协作与对话")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.heading)
        layout.addSpacing(6)
        layout.addWidget(self.subtitle)
        layout.addSpacing(22)

        track = QFrame()
        track.setObjectName("AuthModeTrack")
        track_layout = QHBoxLayout(track)
        track_layout.setContentsMargins(3, 3, 3, 3)
        track_layout.setSpacing(3)
        self.login_mode = QPushButton("登录")
        self.register_mode = QPushButton("注册")
        for button in (self.login_mode, self.register_mode):
            button.setObjectName("AuthModeTab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            track_layout.addWidget(button, 1)
        self.login_mode.setChecked(True)
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.login_mode)
        modes.addButton(self.register_mode)
        layout.addWidget(track)
        layout.addSpacing(20)

        self.username_label = QLabel("账号")
        self.username_label.setObjectName("AuthFieldLabel")
        self.username = QLineEdit()
        self.username.setObjectName("AuthInput")
        self.username.setMinimumHeight(40)
        self.username.setPlaceholderText("请输入账号")
        self.username.setAccessibleName("账号")
        self.username_action = self.username.addAction(
            lucide_icon_with_color("user-round", current_tokens().text_tertiary, 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        layout.addWidget(self.username_label)
        layout.addSpacing(6)
        layout.addWidget(self.username)
        layout.addSpacing(14)

        self.password_label = QLabel("密码")
        self.password_label.setObjectName("AuthFieldLabel")
        self.password = QLineEdit()
        self.password.setObjectName("AuthInput")
        self.password.setMinimumHeight(40)
        self.password.setPlaceholderText("至少 8 位字符")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("密码")
        self.password_action = self.password.addAction(
            lucide_icon_with_color("lock", current_tokens().text_tertiary, 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.reveal_action = self.password.addAction(
            lucide_icon_with_color("eye", current_tokens().text_tertiary, 16),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.reveal_action.setToolTip("显示密码")
        self.reveal_action.setText("显示密码")
        self.reveal_action.triggered.connect(self._toggle_password)
        layout.addWidget(self.password_label)
        layout.addSpacing(6)
        layout.addWidget(self.password)
        layout.addSpacing(12)

        self.error = QLabel("")
        self.error.setObjectName("InlineNotice")
        self.error.setProperty("noticeKind", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        layout.addSpacing(12)

        self.submit = QPushButton("登录")
        self.submit.setObjectName("Primary")
        self.submit.setMinimumHeight(42)
        self.submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit.setAccessibleName("登录")
        layout.addWidget(self.submit)
        layout.addSpacing(18)

        divider = QFrame()
        divider.setObjectName("CardDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(12)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setObjectName("AuthStatusDot")
        self.status_dot.setProperty("connectionState", "pending")
        self.status_dot.setFixedSize(8, 8)
        self.status = QLabel("正在连接…")
        self.status.setObjectName("AuthStatusText")
        server = QLabel(f"· {self._host}:{self._port}")
        server.setObjectName("AuthServer")
        status_row.addStretch(1)
        status_row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self.status)
        status_row.addWidget(server)
        status_row.addStretch(1)
        layout.addLayout(status_row)
        return card

    def _icon_pixmap(self, name: str, color_key: str, size: int) -> Any:
        tokens = current_tokens()
        color = tokens.agent if color_key == "agent" else tokens.primary
        return lucide_icon_with_color(name, color, size).pixmap(size, size)

    def _refresh_icons(self, *_args: object) -> None:
        tokens = current_tokens()
        self.brand_tile.setPixmap(brand_logo_icon(30).pixmap(30, 30))
        for label, name, color_key, size in self._feature_icons:
            label.setPixmap(self._icon_pixmap(name, color_key, size))
        self.username_action.setIcon(lucide_icon_with_color("user-round", tokens.text_tertiary, 16))
        self.password_action.setIcon(lucide_icon_with_color("lock", tokens.text_tertiary, 16))
        self.reveal_action.setIcon(
            lucide_icon_with_color("eye" if self._password_hidden else "eye-off", tokens.text_tertiary, 16)
        )

    def _toggle_password(self) -> None:
        self._password_hidden = not self._password_hidden
        self.password.setEchoMode(
            QLineEdit.EchoMode.Password if self._password_hidden else QLineEdit.EchoMode.Normal
        )
        self.reveal_action.setToolTip("显示密码" if self._password_hidden else "隐藏密码")
        self.reveal_action.setText("显示密码" if self._password_hidden else "隐藏密码")
        self._refresh_icons()
        self.password.setFocus()

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()

    def _clear_error(self, *_args: object) -> None:
        if self.error.isVisible() or self.error.text():
            self.error.clear()
            self.error.hide()

    def _update_mode(self) -> None:
        registering = self.register_mode.isChecked()
        self.submit.setText("创建账号" if registering else "登录")
        self.submit.setAccessibleName("创建账号" if registering else "登录")
        self.heading.setText("创建账号" if registering else "欢迎回来")
        self.subtitle.setText("加入大厅，与团队和 Agent 开始协作" if registering else "登录后继续你的协作与对话")
        self._clear_error()
        self.username.setFocus()

    def _submit(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not 3 <= len(username) <= 20:
            self._show_error("账号需要 3–20 个字符")
            self.username.setFocus()
            return
        if len(password) < 8:
            self._show_error("密码至少需要 8 个字符")
            self.password.setFocus()
            return
        self._clear_error()
        self.submit.setEnabled(False)
        mode = "register" if self.register_mode.isChecked() else "login"
        self.submitted.emit(mode, username, password)

    def _apply_responsive_layout(self, width: int) -> None:
        wide = width >= self.HERO_BREAKPOINT
        self.hero.setVisible(wide)
        self.shell_layout.setSpacing(72 if wide else 0)
        shell_width = 920 if wide else 408
        self.shell.setMinimumWidth(shell_width)
        self.shell.setMaximumWidth(shell_width)
        margin = 32 if wide else 24
        self.outer.setContentsMargins(margin, margin, margin, margin)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

    def update_state(self, topic: str, state: AppState) -> None:
        self.status.setText(state.connection_text)
        text = state.connection_text
        if state.connected:
            connection_state = "online"
        elif "重连" in text or "正在连接" in text:
            connection_state = "pending"
        else:
            connection_state = "offline"
        if self.status_dot.property("connectionState") != connection_state:
            self.status_dot.setProperty("connectionState", connection_state)
            self.status_dot.style().unpolish(self.status_dot)
            self.status_dot.style().polish(self.status_dot)
        self.submit.setEnabled(state.connected and not any(action.startswith("auth.") for action in state.pending.values()))
        if topic in {"auth", "error"} and state.last_error:
            self._show_error(state.last_error)


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
        self._draft_previews: dict[ConversationKey, str] = {}
        self._empty: EmptyState | None = None
        self._selected_key: ConversationKey | None = None

    def set_conversations(self, summaries: list[ConversationSummary]) -> None:
        self._summaries = list(summaries)
        self.count.setText(f"{len(summaries)} 个会话")
        present = {item.key for item in summaries}
        for key in tuple(self._cards):
            if key in present:
                continue
            card = self._cards.pop(key)
            self.cards.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._filter(self.search.text())

    def set_selected(self, key: ConversationKey | None) -> None:
        if self._selected_key == key:
            return
        previous = self._selected_key
        self._selected_key = key
        for card_key in {previous, key}:
            if card_key is None:
                continue
            card = self._cards.get(card_key)
            if card is not None:
                card.set_selected(card_key == key)

    def set_drafts(self, previews: dict[ConversationKey, str]) -> None:
        self._draft_previews = dict(previews)
        for key, card in self._cards.items():
            card.set_draft_preview(self._draft_previews.get(key, ""))

    def set_draft(self, key: ConversationKey, preview: str) -> None:
        clean = " ".join(str(preview).split())
        if clean:
            self._draft_previews[key] = clean
        else:
            self._draft_previews.pop(key, None)
        card = self._cards.get(key)
        if card is not None:
            card.set_draft_preview(clean)

    def _activate(self, key: ConversationKey) -> None:
        self.set_selected(key)
        self.selected.emit(key)

    def _filter(self, keyword: str) -> None:
        clean = keyword.strip().lower()
        visible = [
            item for item in self._summaries
            if clean in item.title.lower()
            or clean in item.preview.lower()
            or clean in self._draft_previews.get(item.key, "").lower()
        ]
        if not visible:
            for card in self._cards.values():
                self.cards.removeWidget(card)
                card.hide()
            if self._empty is None:
                self._empty = EmptyState("暂无会话", "从通讯录选择联系人，或加入一个群聊", "◇")
            if self._empty.parent() is not self.content:
                self._empty.setParent(self.content)
            if self.cards.indexOf(self._empty) < 0:
                self.cards.addWidget(self._empty)
            self._empty.show()
            return
        if self._empty is not None:
            self.cards.removeWidget(self._empty)
            self._empty.hide()
        visible_keys: list[ConversationKey] = []
        for summary in visible:
            card = self._cards.get(summary.key)
            if card is None:
                card = ConversationCard(summary, self.content)
                card.activated.connect(self._activate)
                self._cards[summary.key] = card
            elif card.summary != summary:
                card.update_summary(summary)
            card.set_draft_preview(self._draft_previews.get(summary.key, ""))
            card.set_selected(summary.key == self._selected_key)
            visible_keys.append(summary.key)
        visible_set = set(visible_keys)
        for key, card in tuple(self._cards.items()):
            if key in visible_set:
                continue
            self.cards.removeWidget(card)
            card.hide()
        for index, key in enumerate(visible_keys):
            card = self._cards[key]
            current = self.cards.itemAt(index).widget() if index < self.cards.count() else None
            if current is card:
                continue
            self.cards.removeWidget(card)
            self.cards.insertWidget(index, card)
            card.show()


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
    latest_viewed = Signal()
    draft_preview_changed = Signal(object, str)
    drafts_loaded = Signal(object)

    def __init__(
        self,
        service: ChatService,
        store: AppStore,
        *,
        draft_store: DraftStore | None = None,
    ) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        self.draft_store = draft_store or self._create_draft_store()
        self.key: ConversationKey | None = None
        self.title_text = ""
        self._focus_message_id: int | None = None
        self._reply_to_id: int | None = None
        self._pending_reply_sender_id: str | None = None
        self._restoring_draft = False
        self._deferred_draft_key: ConversationKey | None = None
        self._last_draft_previews: dict[ConversationKey, str] = {}
        self._loading_key: ConversationKey | None = None
        self._rendered_key: ConversationKey | None = None
        self._rendered_signature: tuple[Any, ...] | None = None
        self._rendered_records_signature: tuple[Any, ...] = ()
        self._rendered_message_ids: list[int] = []
        self._render_pending_count_new = False
        self._render_pending_force = False
        self._auto_media_requested: set[int] = set()
        self._message_rows: dict[int, QWidget] = {}
        self._conversation_views: dict[ConversationKey, dict[str, Any]] = {}
        self._pending_view_restore = False
        self._pending_restore_anchor: tuple[int, int] | None = None
        self._pending_restore_position = 0
        self._animating_scroll = False
        self._layout_pending = False
        self._render_serial = 0
        density = str(QSettings().value(MESSAGE_DENSITY_KEY, "default"))
        self._density = density if density in MESSAGE_DENSITY_SPACING else "default"
        self._last_message_id: int | None = None
        self._pending_new_messages = 0
        self._force_follow = True
        self._follow_pending = True
        self._sticker_library: LocalStickerLibrary | None = None
        self._sticker_panel: StickerPanel | None = None
        self._latest_outgoing_id = 0
        self._outbox_seen: dict[str, dict[str, Any]] = {}
        self._typing_active = False
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(3000)
        self._typing_timer.timeout.connect(self._pulse_private_typing)
        self._peer_typing_timer = QTimer(self)
        self._peer_typing_timer.setSingleShot(True)
        self._peer_typing_timer.setInterval(6000)
        self._peer_typing_timer.timeout.connect(self._expire_peer_typing)
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
        self.search_results.itemActivated.connect(self._open_search_result)
        self.search_results.itemClicked.connect(self._open_search_result)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.message_content = QWidget()
        self.messages = QVBoxLayout(self.message_content)
        self.messages.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.messages.setContentsMargins(*MESSAGE_DENSITY_MARGINS[self._density])
        self.messages.setSpacing(MESSAGE_DENSITY_SPACING[self._density])
        configure_scroll_surface(self.scroll, self.message_content)
        self.scroll.setWidget(self.message_content)
        self.new_messages_badge = QPushButton("0 条新消息")
        self.new_messages_badge.setObjectName("NewMessagesBadge")
        self.new_messages_badge.setAccessibleName("查看最新消息")
        self.new_messages_badge.setAccessibleDescription("有新消息，点击跳到最新内容")
        self.new_messages_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_messages_badge.setIconSize(QSize(14, 14))
        self.new_messages_badge.setParent(self.scroll)
        self.new_messages_badge.hide()
        self.new_messages_badge.clicked.connect(self._jump_to_latest)
        self.scroll.installEventFilter(self)
        self.scroll.viewport().installEventFilter(self)
        scrollbar = self.scroll.verticalScrollBar()
        scrollbar.installEventFilter(self)
        scrollbar.valueChanged.connect(self._handle_scroll)
        scrollbar.rangeChanged.connect(self._handle_scroll_range)
        scrollbar.sliderPressed.connect(self._stop_scroll_animation)
        self._scroll_animation = QPropertyAnimation(scrollbar, b"value", self)
        self._scroll_animation.setDuration(SMOOTH_SCROLL_MS)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_animation.finished.connect(self._finish_smooth_scroll)
        self._send_feedback_timer = QTimer(self)
        self._send_feedback_timer.setSingleShot(True)
        self._send_feedback_timer.setInterval(4000)
        self._send_feedback_timer.timeout.connect(self._refresh_subtitle)
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(DRAFT_DEBOUNCE_MS)
        self._draft_timer.timeout.connect(self._flush_draft_timer)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(0)
        self._render_timer.timeout.connect(self._flush_pending_render)
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(self._refresh_new_messages_badge_icon)
        self._refresh_new_messages_badge_icon()
        composer = QWidget()
        composer.setObjectName("Composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 10, 14, 12)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.emoji = QPushButton("☺ 表情")
        self.sticker = QPushButton("表情包")
        self.sticker.setAccessibleName("打开表情包面板")
        self.image = QPushButton("图片")
        self.ai = QPushButton("✦ AI")
        for button in (self.emoji, self.sticker, self.image, self.ai):
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
        self.reply_hint = QPushButton("取消引用")
        self.reply_hint.setObjectName("Ghost")
        self.reply_hint.hide()
        self.reply_hint.clicked.connect(self._clear_reply)
        self.send = QPushButton("发送")
        self.send.setObjectName("Primary")
        self.send.setFixedWidth(88)
        self.send.setMinimumHeight(42)
        input_row.addWidget(self.compose, 1)
        input_row.addWidget(self.send)
        composer_layout.addLayout(toolbar)
        self.agent_activity = QLabel("")
        self.agent_activity.setObjectName("AgentActivity")
        self.agent_activity.setProperty("messageType", "agent_status")
        self.agent_activity.hide()
        composer_layout.addWidget(self.agent_activity)
        self.private_outbox_panel = QWidget()
        self.private_outbox_layout = QVBoxLayout(self.private_outbox_panel)
        self.private_outbox_layout.setContentsMargins(0, 0, 0, 0)
        self.private_outbox_layout.setSpacing(4)
        self.private_outbox_panel.hide()
        composer_layout.addWidget(self.private_outbox_panel)
        composer_layout.addWidget(self.reply_hint)
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
        self.compose.send_requested.connect(self._send)
        self.sticker.clicked.connect(self._open_sticker_panel)
        self.image.clicked.connect(self._send_image)
        self.search_button.clicked.connect(lambda: self.search_panel.setVisible(not self.search_panel.isVisible()))
        search_action.clicked.connect(self._search)
        self.search_input.returnPressed.connect(self._search)
        self.details_button.clicked.connect(self._toggle_details)
        self.details.close_requested.connect(self._hide_details)
        self.details.agent_manager_requested.connect(self._open_group_agent_manager)
        self.details.agent_memory_requested.connect(self._open_group_agent_memory)
        self.details.mention_requested.connect(self._mention_from_details)
        self.details_scrim.clicked.connect(self._hide_details)
        self.conversation_list_button.clicked.connect(self.conversation_list_requested)
        self.compose.mention_query.connect(self._show_mentions)
        self.compose.mention_closed.connect(self._hide_mentions)
        self.compose.mention_key.connect(self._mention_key)
        self.compose.textChanged.connect(self._capture_draft_change)
        self.compose.textChanged.connect(self._update_private_typing)
        self.compose.composition_state_changed.connect(self._composition_state_changed)
        self.mention_list.itemClicked.connect(self._choose_mention)
        QShortcut(QKeySequence("Ctrl+Return"), self.compose, activated=self._send)
        QShortcut(QKeySequence("Meta+Return"), self.compose, activated=self._send)
        QShortcut(QKeySequence("Escape"), self, activated=self._hide_details)
        self._emoji_menu()
        self._ai_menu()
        self._suppress_tooltips_until = 0.0
        self._tooltip_guard_timer = QTimer(self)
        self._tooltip_guard_timer.setSingleShot(True)
        self._tooltip_guard_timer.setInterval(1050)
        self._tooltip_guard_timer.timeout.connect(self._release_tooltip_guard)

    @staticmethod
    def _create_draft_store() -> DraftStore:
        app = QApplication.instance()
        has_app_identity = bool(
            app is not None and app.organizationName() and app.applicationName()
        )
        return DraftStore(QSettings() if has_app_identity else None)

    def _current_draft(self) -> Draft:
        return Draft.create(
            self.compose.toPlainText(),
            self.compose.mentions(),
            self._reply_to_id,
        )

    def _emit_draft_preview(self, key: ConversationKey, draft: Draft | None) -> None:
        preview = draft.preview() if draft is not None and draft.text else ""
        if self._last_draft_previews.get(key, "") == preview:
            return
        if preview:
            self._last_draft_previews[key] = preview
        else:
            self._last_draft_previews.pop(key, None)
        self.draft_preview_changed.emit(key, preview)

    def _publish_loaded_drafts(self) -> None:
        previews = self.draft_store.previews()
        self._last_draft_previews = dict(previews)
        self.drafts_loaded.emit(previews)

    def publish_drafts(self) -> None:
        self._publish_loaded_drafts()

    def _capture_draft_change(self) -> None:
        if (
            self._restoring_draft
            or self._deferred_draft_key is not None
            or self.key is None
            or self.compose.is_composing
        ):
            return
        draft = self._current_draft()
        if draft.text:
            if not draft.same_content(self.draft_store.get(self.key)):
                self.draft_store.put(self.key, draft)
            self._emit_draft_preview(self.key, draft)
            self._draft_timer.start()
            return
        if self.key.kind == "private" and any(
            record.get("key") == self.key and record.get("status") in {"uncertain", "retry_ready", "sending"}
            for record in self.store.state.private_outbox.values()
        ):
            return
        if self.draft_store.remove(self.key):
            self.draft_store.flush()
        self._emit_draft_preview(self.key, None)
        self._draft_timer.stop()

    def _flush_draft_timer(self) -> None:
        self._flush_current_draft()

    def _flush_current_draft(self) -> None:
        self._draft_timer.stop()
        self._capture_draft_change()
        self.draft_store.flush()

    def _apply_draft(self, key: ConversationKey) -> None:
        if self.key != key:
            return
        if self.compose.is_composing:
            self._deferred_draft_key = key
            return
        self._deferred_draft_key = None
        draft = self.draft_store.get(key)
        if key.kind == "private" and draft is not None and any(
            record.get("key") == key
            and record.get("payload", {}).get("content") == draft.text
            and record.get("status") in {"uncertain", "retry_ready", "sending"}
            for record in self.store.state.private_outbox.values()
        ):
            draft = None
        self._restoring_draft = True
        try:
            self.compose.blockSignals(True)
            self.compose.set_content(
                draft.text if draft is not None else "",
                draft.mentions if draft is not None else (),
            )
            self.compose.document().clearUndoRedoStacks(
                QTextDocument.Stacks.UndoAndRedoStacks
            )
            if draft is not None and draft.reply_to_id is not None:
                self._set_reply_target(draft.reply_to_id)
            else:
                self._clear_reply(schedule_draft=False)
        finally:
            self.compose.blockSignals(False)
            self._restoring_draft = False
        self._hide_mentions()
        self._draft_timer.stop()

    def _composition_state_changed(self, composing: bool) -> None:
        if composing:
            return
        if self._deferred_draft_key is not None:
            key = self._deferred_draft_key
            self._deferred_draft_key = None
            self._apply_draft(key)
            return
        self._capture_draft_change()

    def _sync_draft_account(self, state: AppState) -> None:
        account = str(state.current_user.get("id") or "anonymous")
        if account == self.draft_store.account:
            return
        previous_account = self.draft_store.account
        self._draft_timer.stop()
        self._capture_draft_change()
        self.draft_store.flush()
        self.draft_store.set_account(account)
        switching_identity = previous_account != "anonymous" and account != previous_account
        if switching_identity:
            state.private_outbox.clear()
        for request_id, (key, payload) in self.draft_store.pending_private_sends().items():
            state.private_outbox.setdefault(
                request_id, {"key": key, "payload": payload, "status": "uncertain"}
            )
        self._publish_loaded_drafts()
        if switching_identity:
            self._reset_for_account_switch()
        elif self.key is not None:
            self._apply_draft(self.key)

    def _reset_for_account_switch(self) -> None:
        self._render_timer.stop()
        self._render_pending_count_new = False
        self._render_pending_force = False
        self._deferred_draft_key = None
        self.key = None
        self.title_text = ""
        self._loading_key = None
        self._focus_message_id = None
        self._pending_reply_sender_id = None
        self._last_message_id = None
        self._pending_new_messages = 0
        self._follow_pending = True
        self._force_follow = True
        self._conversation_views.clear()
        self._outbox_seen.clear()
        self._auto_media_requested.clear()
        self._restoring_draft = True
        try:
            self.compose.blockSignals(True)
            self.compose.set_content("", ())
            self.compose.document().clearUndoRedoStacks(
                QTextDocument.Stacks.UndoAndRedoStacks
            )
            self._clear_reply(schedule_draft=False)
        finally:
            self.compose.blockSignals(False)
            self._restoring_draft = False
        self.title.setText("选择一个会话")
        self.subtitle.setText("消息、成员与 Agent 协作都会显示在这里")
        self.avatar.set_avatar("会话", group=False)
        self._hide_mentions()
        self._hide_details()
        self.profile_popover.hide()
        self.render_messages(force=True)

    def flush_drafts(self) -> None:
        self._flush_current_draft()

    def _schedule_render(self, *, count_new: bool, force: bool = False) -> None:
        self._render_pending_count_new = self._render_pending_count_new or count_new
        self._render_pending_force = self._render_pending_force or force
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _flush_pending_render(self) -> None:
        count_new = self._render_pending_count_new
        force = self._render_pending_force
        self._render_pending_count_new = False
        self._render_pending_force = False
        if self.key is not None:
            self.render_messages(count_new=count_new, force=force)

    def set_history_loading(self, key: ConversationKey, loading: bool) -> None:
        if self.key != key:
            return
        target = key if loading else None
        if target == self._loading_key:
            return
        self._loading_key = target
        self._schedule_render(count_new=False)

    def refresh_history_loading(self) -> None:
        if self.key is None:
            return
        self.set_history_loading(self.key, self.service.history_loading(self.key))

    def closeEvent(self, event: Any) -> None:
        self._tooltip_guard_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._render_timer.stop()
        self._draft_timer.stop()
        self.flush_drafts()
        super().closeEvent(event)

    def _emoji_menu(self) -> None:
        menu = QMenu(self)
        for emoji in ("😀", "😂", "😍", "👍", "🎉", "❤️", "🤔", "😢"):
            action = menu.addAction(emoji)
            action.triggered.connect(lambda _checked=False, value=emoji: self.compose.insertPlainText(value))
        self.emoji.setMenu(menu)

    def _ai_menu(self) -> None:
        self.ai_menu = QMenu(self)
        self.ai_menu.aboutToShow.connect(self._populate_ai_menu)
        self.ai_menu_actions: dict[str, Any] = {}
        self.ai.setMenu(self.ai_menu)

    def _populate_ai_menu(self) -> None:
        menu = self.ai_menu
        menu.clear()
        self.ai_menu_actions = {}
        summary = menu.addAction("总结最近消息")
        summary.triggered.connect(lambda _checked=False: self._invoke("总结最近 50 条消息"))
        todo = menu.addAction("提取待办")
        todo.triggered.connect(lambda _checked=False: self._invoke("提取最近 50 条消息中的待办"))
        menu.addSeparator()
        agents = self._conversation_agents()
        if not agents:
            empty = menu.addAction("当前会话暂无可用 Agent")
            empty.setEnabled(False)
            return
        for agent in agents:
            name = str(agent.get("name") or agent.get("display_name") or agent.get("username") or "AI Agent")
            label = f"向 {name} 提问" + (" · 预设" if agent.get("builtin") else "")
            action = menu.addAction(label)
            action.setData(str(agent.get("id") or ""))
            card = agent.get("card") if isinstance(agent.get("card"), dict) else {}
            action.setToolTip(str(agent.get("description") or card.get("personality") or ""))
            action.setIcon(self._agent_avatar_icon(agent))
            action.triggered.connect(lambda _checked=False, member=dict(agent): self._insert_agent_mention(member))
            agent_id = str(agent.get("id") or "")
            if agent_id:
                self.ai_menu_actions[agent_id] = action

    def _conversation_agents(self) -> list[dict[str, Any]]:
        if not self.key or self.key.kind != "group":
            return []
        agents = self.store.state.group_agents.get(self.key.identifier)
        if agents is None:
            agents = [
                member for member in self.store.state.members_by_group.get(self.key.identifier, [])
                if member.get("kind") == "agent"
            ]
        return [dict(item) for item in agents if item.get("enabled", True)]

    def _agent_avatar_icon(self, agent: dict[str, Any], size: int = 22) -> QIcon:
        profile = self.service.profile(None, agent)
        data = self.service.avatar_data(profile)
        return QIcon(avatar_pixmap(profile.display_name, size, data, agent=True))

    def _refresh_ai_menu_icons(self) -> None:
        if not getattr(self, "ai_menu_actions", None):
            return
        agents = {str(item.get("id") or ""): item for item in self._conversation_agents()}
        for agent_id, action in self.ai_menu_actions.items():
            agent = agents.get(agent_id)
            if agent:
                action.setIcon(self._agent_avatar_icon(agent))

    def _prefetch_agent_avatars(self) -> None:
        for agent in self._conversation_agents():
            self.service.avatar_data(self.service.profile(None, agent))

    def set_density(self, density: str) -> None:
        value = density if density in MESSAGE_DENSITY_SPACING else "default"
        if value == self._density:
            return
        self._density = value
        self.messages.setContentsMargins(*MESSAGE_DENSITY_MARGINS[value])
        self.messages.setSpacing(MESSAGE_DENSITY_SPACING[value])
        if self.key is not None:
            self.render_messages(force=True)

    def set_conversation(self, key: ConversationKey, title: str) -> None:
        if self.key == key:
            self.title_text = title
            self.title.setText(title)
            self.compose.setPlaceholderText("输入消息，可用 @ 提及对方或 AI Agent" if key.kind == "private" else "输入 @ 选择群成员或 AI Agent")
            self.avatar.set_avatar(title, group=key.kind == "group")
            self._refresh_agent_activity()
            self._refresh_subtitle()
            return
        # A rebuilt message list can place a fresh tooltip target under the
        # stationary pointer. Suppress automatic tooltips briefly so Windows
        # does not flash a small native tooltip window during the switch.
        self._suppress_tooltips_until = time.monotonic() + 1.0
        QToolTip.hideText()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._tooltip_guard_timer.start()
        self._stop_private_typing()
        self._peer_typing_timer.stop()
        self.profile_popover.hide()
        if self.key and self.key.kind == "private":
            self.store.state.private_typing[self.key] = False
        self._flush_current_draft()
        self._render_timer.stop()
        self._render_pending_count_new = False
        self._render_pending_force = False
        self._save_active_view()
        self._stop_scroll_animation()
        self._auto_media_requested.clear()
        self.key = key
        self._loading_key = key if self.service.history_loading(key) else None
        self._load_view(key)
        self._apply_draft(key)
        self._refresh_agent_activity()
        self.title_text = title
        self.title.setText(title)
        self.compose.setPlaceholderText("输入消息，可用 @ 提及对方或 AI Agent" if key.kind == "private" else "输入 @ 选择群成员或 AI Agent")
        self.details_button.setText("群设置" if key.kind == "group" else "详情")
        self.details_button.setAccessibleName(
            "打开群设置" if key.kind == "group" else "打开会话详情"
        )
        details_tip = (
            "管理群成员、AI Agent、共享记忆和定时总结"
            if key.kind == "group"
            else "查看会话与成员信息"
        )
        if self.details_button.toolTip() != details_tip:
            self.details_button.setToolTip(details_tip)
        self._refresh_subtitle()
        self.avatar.set_avatar(title, group=key.kind == "group")
        self._hide_mentions()
        self._hide_details()
        self.details.set_context(key, title, self.store.state)
        self._refresh_private_state()
        self._refresh_private_outbox()
        self._prefetch_agent_avatars()
        self.render_messages(force=True)

    @staticmethod
    def _message_signature(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(message.get("id", message.get("message_id", ""))),
            int(message.get("event_id", 0) or 0),
            bool(message.get("edited")),
            bool(message.get("recalled")),
            str(message.get("message_type") or ""),
            str(message.get("sender_id") or ""),
            str(message.get("sender_kind") or ""),
            str(message.get("sender_name") or ""),
            int(message.get("reply_to_id", 0) or 0),
            str(message.get("content") or ""),
            bool(message.get("image_data")),
            str(message.get("sticker_name") or ""),
            bool(message.get("image_missing")),
        )

    def _records_signature(self, records: list[dict[str, Any]]) -> tuple[Any, ...]:
        return tuple(self._message_signature(message) for message in records)

    def _add_message_row(
        self,
        message: dict[str, Any],
        previous_message: dict[str, Any] | None,
        by_id: dict[int, dict[str, Any]],
        my_id: str,
        can_manage: bool,
    ) -> QWidget:
        mine = str(message.get("sender_id", "")) == my_id
        grouped = _messages_are_grouped(previous_message, message)
        row = QWidget()
        row.setObjectName("MessageRow")
        row.setProperty("grouped", grouped)
        row.setProperty("mine", mine)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0 if grouped else 6, 0, 0)
        if message.get("message_type") == "system":
            row.setProperty("messageType", "system")
            system_label = QLabel(str(message.get("content") or "系统消息"))
            system_label.setObjectName("SystemMessage")
            system_label.setWordWrap(True)
            system_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_layout.addStretch()
            row_layout.addWidget(system_label)
            row_layout.addStretch()
            self.messages.addWidget(row)
            return row
        assert self.key is not None
        can_recall = mine or can_manage or self.store.state.current_user.get("role") == "admin"
        displayed = message
        sender_id_text = str(message.get("sender_id", ""))
        if message.get("sender_kind") != "agent" and sender_id_text:
            candidates = (
                self.store.state.members_by_group.get(self.key.identifier, [])
                if self.key.kind == "group"
                else self.store.state.users
            )
            identity = next((item for item in candidates if str(item.get("id")) == sender_id_text), None)
            if identity:
                name = identity.get("nickname") if self.key.kind == "group" else None
                displayed = {
                    **message,
                    "sender_name": name
                    or identity.get("display_name")
                    or identity.get("username")
                    or message.get("sender_name"),
                }
        original = by_id.get(int(message.get("reply_to_id") or 0))
        if original:
            preview = (
                "消息已撤回"
                if original.get("recalled")
                else f"{original.get('sender_name', '')}: {_message_preview(original)}"
            )
            displayed = {**message, "reply_preview": preview}
        bubble = MessageBubble(
            displayed, mine=mine, can_recall=can_recall, density=self._density
        )
        bubble.recall_requested.connect(self.service.recall)
        bubble.reply_requested.connect(self._reply_to)
        bubble.edit_requested.connect(self._edit_message)
        bubble.quote_requested.connect(self._focus_quoted_message)
        bubble.media_requested.connect(self.service.fetch_media)
        bubble.sources_requested.connect(self._focus_sources)
        is_agent = message.get("sender_kind") == "agent" or message.get("message_type") == "agent"
        try:
            sender_id = 0 if is_agent else int(message.get("sender_id", 0) or 0)
        except (TypeError, ValueError):
            sender_id = 0
        sender_name = (
            str(displayed.get("sender_name") or "AI Agent")
            if is_agent
            else str(displayed.get("sender_name", "未知用户"))
        )
        fallback = {"id": sender_id, "username": sender_name, "kind": "agent" if is_agent else "user"}
        if is_agent:
            agent_id = str(message.get("sender_agent_id") or "")
            configured = next(
                (item for item in self.store.state.agents if str(item.get("id")) == agent_id),
                None,
            )
            if configured:
                fallback.update(configured)
        elif self.key.kind == "group":
            member = next(
                (
                    item
                    for item in self.store.state.members_by_group.get(self.key.identifier, [])
                    if str(item.get("id")) == str(sender_id)
                ),
                None,
            )
            if member:
                fallback.update(member)
        profile = self.service.profile(
            None if is_agent else sender_id,
            fallback,
            self.key.identifier if self.key and self.key.kind == "group" else None,
        )
        if grouped:
            avatar: QWidget = QWidget()
            avatar.setObjectName("AvatarPlaceholder")
            avatar.setFixedSize(36, 1)
        else:
            # Open profiles on click only. Hover popovers grab the pointer on
            # Windows and swallow the first right-click context menu.
            profile_avatar = UserAvatar(
                self.service, profile, 36, clickable=True, hoverable=False
            )
            profile_avatar.activated.connect(self._show_message_profile)
            profile_avatar.context_requested.connect(self._show_avatar_menu)
            avatar = profile_avatar

        message_column = QWidget()
        message_column.setObjectName("MessageColumn")
        message_column.setMaximumWidth(bubble.maximumWidth())
        column_layout = QVBoxLayout(message_column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(MESSAGE_DENSITY_COLUMN_SPACING[self._density])
        if self.key.kind == "group" and not grouped and (not mine or is_agent):
            role_label = {"summary": " · 总结", "todo": " · 待办"}.get(
                message_card_kind(displayed), ""
            )
            name = QLabel(f"{sender_name}{role_label}")
            name.setObjectName("AgentMessageSender" if is_agent else "MessageSender")
            name.setAlignment(
                Qt.AlignmentFlag.AlignRight if mine else Qt.AlignmentFlag.AlignLeft
            )
            column_layout.addWidget(name)
        content_alignment = (
            Qt.AlignmentFlag.AlignRight if mine else Qt.AlignmentFlag.AlignLeft
        )
        if message_card_kind(displayed):
            column_layout.addWidget(bubble)
        else:
            column_layout.addWidget(bubble, alignment=content_alignment)
        stamp = _message_timestamp(message)
        footer_text = (
            time.strftime("%H:%M", time.localtime(stamp)) if stamp else ""
        ) + (" · 已编辑" if message.get("edited") else "")
        if (self.key.kind == "private" and self.store.state.capabilities.get("private_chat_v2")
                and mine and int(message.get("id", 0) or 0) == self._latest_outgoing_id):
            summary = next((item for item in self.store.state.conversations if item.key == self.key), None)
            read_id = summary.peer_last_read_message_id if summary else 0
            footer_text += " · 已读" if read_id >= self._latest_outgoing_id else " · 已发送"
        footer = QLabel(footer_text)
        footer.setObjectName("MessageTime")
        footer.setAlignment(content_alignment)
        column_layout.addWidget(footer)
        card_kind = message_card_kind(displayed)
        if mine:
            row_layout.addStretch()
            row_layout.addWidget(message_column, 1 if card_kind else 0)
            row_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
        else:
            row_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
            row_layout.addWidget(message_column, 1 if card_kind else 0)
            if not card_kind:
                row_layout.addStretch()
        self.messages.addWidget(row)
        message_id = int(message.get("id", message.get("message_id", 0)) or 0)
        if message_id:
            self._message_rows[message_id] = row
            if (
                self.store.state.connected
                and self.store.state.authenticated
                and message.get("message_type") in {"image", "sticker"}
                and not message.get("recalled")
                and not message.get("image_data")
                and message_id not in self._auto_media_requested
            ):
                self._auto_media_requested.add(message_id)
                QTimer.singleShot(0, lambda value=message_id: self._load_image(value))
        return row

    def _append_message_rows(
        self,
        records: list[dict[str, Any]],
        start: int,
        previous_message: dict[str, Any] | None,
        by_id: dict[int, dict[str, Any]],
        my_id: str,
        can_manage: bool,
    ) -> None:
        previous = previous_message
        for message in records[start:]:
            self._add_message_row(message, previous, by_id, my_id, can_manage)
            previous = message

    def _set_empty_message_state(self, title: str, description: str) -> None:
        self.messages.addWidget(EmptyState(title, description))

    def render_messages(self, *, count_new: bool = True, force: bool = False) -> None:
        if self._render_timer.isActive():
            count_new = count_new or self._render_pending_count_new
            force = force or self._render_pending_force
            self._render_timer.stop()
        self._render_pending_count_new = False
        self._render_pending_force = False
        self._stop_scroll_animation()
        self._render_serial += 1
        serial = self._render_serial
        scrollbar = self.scroll.verticalScrollBar()
        old_position = scrollbar.value()
        was_at_bottom = self._is_at_bottom() if self.scroll.isVisible() else False
        restoring_view = self._pending_view_restore
        force_follow = self._force_follow
        follow = force_follow or self._follow_pending or (was_at_bottom and not restoring_view)
        records = self.store.state.messages.get(self.key, []) if self.key else []
        if count_new:
            new_count = self._consume_new_messages(records)
        else:
            new_count = 0
            self._remember_latest_message(records)
        loading = self.key is not None and self._loading_key == self.key
        records_signature = self._records_signature(records)
        summary = next((item for item in self.store.state.conversations if item.key == self.key), None)
        receipt_id = summary.peer_last_read_message_id if summary and self.key and self.key.kind == "private" else 0
        render_state = (self.key, loading, records_signature, receipt_id)
        own_id = str(self.store.state.current_user.get("id", ""))
        self._latest_outgoing_id = max((int(item.get("id", 0) or 0) for item in records if str(item.get("sender_id", "")) == own_id), default=0)
        if (
            not force
            and self.key == self._rendered_key
            and render_state == self._rendered_signature
        ):
            focused = self._message_rows.get(self._focus_message_id or 0)
            if focused and self._focus_message_id is not None:
                self._follow_pending = False
                QTimer.singleShot(0, lambda: self._complete_render_focus(serial))
            return
        can_append = bool(
            not force
            and not restoring_view
            and self.key == self._rendered_key
            and self._message_rows
            and self._focus_message_id is None
            and records_signature[: len(self._rendered_records_signature)]
            == self._rendered_records_signature
            and len(records) > len(self._rendered_message_ids)
        )
        if force_follow or (new_count and follow):
            self._pending_new_messages = 0
        elif new_count:
            self._pending_new_messages += new_count
        self._sync_new_messages_badge()
        self._force_follow = False
        self._pending_view_restore = False
        self._layout_pending = True
        if can_append:
            my_id = str(self.store.state.current_user.get("id", ""))
            by_id = {int(item["id"]): item for item in records if item.get("id") is not None}
            can_manage = bool(
                self.key is not None
                and self.key.kind == "group"
                and self.store.state.group_permissions.get(self.key.identifier, {}).get(
                    "can_manage_members", False
                )
            )
            start = len(self._rendered_message_ids)
            scrollbar.blockSignals(True)
            self._append_message_rows(
                records,
                start,
                records[start - 1] if start else None,
                by_id,
                my_id,
                can_manage,
            )
            scrollbar.blockSignals(False)
            self._rendered_key = self.key
            self._rendered_signature = render_state
            self._rendered_records_signature = records_signature
            self._rendered_message_ids = [
                int(item.get("id", item.get("message_id", 0)) or 0) for item in records
            ]
            self.message_content.adjustSize()
            self.messages.activate()
            self._follow_pending = follow
            if follow:
                QTimer.singleShot(0, lambda: self._complete_render_follow(serial))
            else:
                self._layout_pending = False
                scrollbar.setValue(old_position)
            return
        if restoring_view:
            anchor = self._pending_restore_anchor
            restore_position = self._pending_restore_position
            self._pending_restore_anchor = None
        else:
            anchor = None if follow else self._capture_scroll_anchor()
            restore_position = old_position
        # The rebuild briefly collapses the scroll range; keep that transient
        # state from being mistaken for the reader returning to the bottom.
        scrollbar.blockSignals(True)
        clear_layout(self.messages)
        self._message_rows.clear()
        self._rendered_key = self.key
        self._rendered_signature = render_state
        self._rendered_records_signature = records_signature
        self._rendered_message_ids = [
            int(item.get("id", item.get("message_id", 0)) or 0) for item in records
        ]
        if not self.key:
            self._set_empty_message_state("选择一个会话", "从会话列表或通讯录开始聊天")
            self._follow_pending = follow
            self._layout_pending = False
            scrollbar.blockSignals(False)
            return
        if not records:
            if loading:
                loading_label = QLabel("正在加载消息…")
                loading_label.setObjectName("Muted")
                loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                loading_label.setMinimumHeight(48)
                self.messages.addWidget(loading_label)
            else:
                self._set_empty_message_state("还没有消息", "发一条消息开启这段对话")
            self._follow_pending = follow
            self._layout_pending = False
            scrollbar.blockSignals(False)
            return
        my_id = str(self.store.state.current_user.get("id", ""))
        by_id = {int(item["id"]): item for item in records if item.get("id") is not None}
        can_manage = bool(
            self.key.kind == "group"
            and self.store.state.group_permissions.get(self.key.identifier, {}).get(
                "can_manage_members", False
            )
        )
        previous_message: dict[str, Any] | None = None
        for message in records:
            self._add_message_row(message, previous_message, by_id, my_id, can_manage)
            previous_message = message
        self.message_content.adjustSize()
        self.messages.activate()
        focused = self._message_rows.get(self._focus_message_id or 0)
        if focused:
            self._follow_pending = False
            QTimer.singleShot(0, lambda: self._complete_render_focus(serial))
        elif self._focus_message_id is None:
            self._follow_pending = follow
            if follow:
                QTimer.singleShot(0, lambda: self._complete_render_follow(serial))
            else:
                QTimer.singleShot(
                    0, lambda: self._complete_render_restore(serial, anchor, restore_position)
                )
        else:
            self._follow_pending = False
            self._layout_pending = False
        scrollbar.blockSignals(False)

    def _message_ids(self, records: list[dict[str, Any]]) -> set[int]:
        ids: set[int] = set()
        for item in records:
            value = item.get("id")
            if value is None:
                continue
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
        return ids

    def _consume_new_messages(self, records: list[dict[str, Any]]) -> int:
        ids = self._message_ids(records)
        if not ids:
            return 0
        latest = max(ids)
        previous = self._last_message_id
        self._last_message_id = latest
        if previous is None:
            return 0
        return sum(1 for value in ids if value > previous)

    def _remember_latest_message(self, records: list[dict[str, Any]]) -> None:
        ids = self._message_ids(records)
        if not ids:
            return
        latest = max(ids)
        self._last_message_id = latest if self._last_message_id is None else max(self._last_message_id, latest)

    def _is_at_bottom(self) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - BOTTOM_DISTANCE_PX

    def _capture_scroll_anchor(self) -> tuple[int, int] | None:
        if not self._message_rows:
            return None
        top = self.scroll.verticalScrollBar().value()
        rows: list[tuple[int, int]] = []
        for message_id, row in self._message_rows.items():
            try:
                rows.append((row.y(), message_id))
            except RuntimeError:
                continue
        if not rows:
            return None
        above = [item for item in rows if item[0] <= top]
        y, message_id = max(above) if above else min(rows)
        return message_id, top - y

    def _restore_scroll_anchor(self, anchor: tuple[int, int] | None, fallback: int) -> None:
        if self._follow_pending:
            return
        self.message_content.adjustSize()
        bar = self.scroll.verticalScrollBar()
        target = fallback
        if anchor:
            message_id, offset = anchor
            row = self._message_rows.get(message_id)
            if row is not None:
                try:
                    target = row.y() + offset
                except RuntimeError:
                    target = fallback
        bar.setValue(max(bar.minimum(), min(bar.maximum(), target)))

    def _save_active_view(self) -> None:
        if not self.key:
            return
        at_bottom = self._is_at_bottom() if self.scroll.isVisible() else self._follow_pending
        follow = at_bottom or self._follow_pending
        self._conversation_views[self.key] = {
            "last_seen_id": self._last_message_id,
            "pending": self._pending_new_messages,
            "follow": follow,
            "anchor": None if follow else self._capture_scroll_anchor(),
            "scroll_value": self.scroll.verticalScrollBar().value(),
        }

    def _load_view(self, key: ConversationKey) -> None:
        view = self._conversation_views.get(key)
        if view is None:
            self._last_message_id = None
            self._pending_new_messages = 0
            self._follow_pending = True
            self._force_follow = True
            self._pending_view_restore = False
            self._pending_restore_anchor = None
            self._pending_restore_position = 0
        else:
            self._last_message_id = view.get("last_seen_id")
            self._pending_new_messages = int(view.get("pending", 0))
            follow = bool(view.get("follow", True))
            self._follow_pending = follow
            self._force_follow = follow
            self._pending_view_restore = True
            self._pending_restore_anchor = view.get("anchor")
            self._pending_restore_position = int(view.get("scroll_value", 0))
        self._sync_new_messages_badge()

    def _handle_scroll(self, value: int) -> None:
        del value
        if not self.scroll.isVisible():
            return
        if self._layout_pending and self.scroll.verticalScrollBar().maximum() <= 0:
            return
        if self._animating_scroll:
            return
        if not self._is_at_bottom():
            self._follow_pending = False
            return
        self._follow_pending = True
        if self._pending_new_messages:
            self._pending_new_messages = 0
            self._sync_new_messages_badge()
        self.latest_viewed.emit()

    def _handle_scroll_range(self, _minimum: int, maximum: int) -> None:
        if not self.scroll.isVisible():
            return
        if self._layout_pending and maximum <= 0:
            return
        if self._animating_scroll:
            return
        if self._follow_pending:
            self.scroll.verticalScrollBar().setValue(maximum)

    def _sync_new_messages_badge(self) -> None:
        if self._pending_new_messages <= 0:
            self.new_messages_badge.hide()
            return
        count = self._pending_new_messages
        self.new_messages_badge.setText(f"{count} 条新消息" if count <= 99 else "99+ 条新消息")
        self.new_messages_badge.setAccessibleDescription(f"{count} 条新消息未读")
        self.new_messages_badge.adjustSize()
        self._position_new_messages_badge()
        self.new_messages_badge.show()
        self.new_messages_badge.raise_()

    def _position_new_messages_badge(self) -> None:
        if not hasattr(self, "new_messages_badge"):
            return
        self.new_messages_badge.adjustSize()
        x = max(8, self.scroll.width() - self.new_messages_badge.width() - 18)
        y = max(8, self.scroll.height() - self.new_messages_badge.height() - 14)
        self.new_messages_badge.move(x, y)

    def _jump_to_latest(self) -> None:
        self._follow_pending = True
        self._scroll_to_bottom(smooth=True)

    def _refresh_new_messages_badge_icon(self, *_args: object) -> None:
        self.new_messages_badge.setIcon(
            lucide_icon_with_color("arrow-down", current_tokens().surface, 14)
        )

    def _stop_scroll_animation(self) -> None:
        if hasattr(self, "_scroll_animation"):
            self._scroll_animation.stop()
        self._animating_scroll = False

    def _finish_smooth_scroll(self) -> None:
        self._animating_scroll = False
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._pending_new_messages = 0
        self._sync_new_messages_badge()

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if event.type() == QEvent.Type.ToolTip and time.monotonic() < self._suppress_tooltips_until:
            return True
        if watched is self.scroll and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._position_new_messages_badge()
            if event.type() == QEvent.Type.Show and self._follow_pending:
                QTimer.singleShot(0, self._scroll_to_bottom)
        if watched is self.scroll or watched is self.scroll.viewport() or watched is self.scroll.verticalScrollBar():
            if event.type() in (QEvent.Type.Wheel, QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress):
                self._stop_scroll_animation()
        return super().eventFilter(watched, event)

    def _release_tooltip_guard(self) -> None:
        if time.monotonic() < self._suppress_tooltips_until:
            self._tooltip_guard_timer.start()
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _complete_render_focus(self, serial: int) -> None:
        if serial != self._render_serial:
            return
        try:
            self._layout_pending = False
            self._scroll_to_focused_message()
        except RuntimeError:
            return

    def _complete_render_follow(self, serial: int) -> None:
        if serial != self._render_serial:
            return
        try:
            self._layout_pending = False
            self._scroll_to_bottom()
            self.latest_viewed.emit()
        except RuntimeError:
            return

    def _complete_render_restore(self, serial: int, anchor: tuple[int, int] | None, fallback: int) -> None:
        if serial != self._render_serial:
            return
        try:
            self._layout_pending = False
            self._restore_scroll_anchor(anchor, fallback)
        except RuntimeError:
            return

    def _scroll_to_focused_message(self) -> None:
        self._follow_pending = False
        message_id = self._focus_message_id
        row = self._message_rows.get(message_id or 0)
        if row:
            self.message_content.adjustSize()
            self.scroll.ensureWidgetVisible(row, 0, 80)
            self._focus_message_id = None

    def _load_image(self, message_id: int) -> None:
        if not self.service.fetch_media(message_id):
            self._auto_media_requested.discard(message_id)

    def focus_message(self, message_id: int) -> None:
        self._focus_message_id = message_id
        self.render_messages(count_new=False)

    def _focus_quoted_message(self, message_id: int) -> None:
        self.focus_message(message_id)
        if message_id not in self._message_rows and self.key:
            self.service.load_result(self.key, message_id)

    def _focus_sources(self, source_ids: list[int]) -> None:
        valid: list[int] = []
        for value in source_ids:
            try:
                message_id = int(value)
            except (TypeError, ValueError):
                continue
            if message_id > 0 and message_id not in valid:
                valid.append(message_id)
        if valid:
            self._focus_quoted_message(min(valid))

    def _restore_failed_send(self, state: AppState) -> None:
        failed = state.failed_send
        if failed is None:
            return
        key, content, mentions, reply_to_id = failed
        if not content:
            return
        draft = Draft.create(content, mentions, reply_to_id)
        existing = self.draft_store.get(key)
        if key == self.key and self.compose.toPlainText():
            return
        if existing is not None and existing.text:
            return
        self.draft_store.put(key, draft)
        self.draft_store.flush()
        self._emit_draft_preview(key, draft)
        if key == self.key and not (key.kind == "private" and any(
            record.get("key") == key and record.get("status") in {"uncertain", "retry_ready", "sending"}
            for record in state.private_outbox.values()
        )):
            self._apply_draft(key)

    def _private_can_send(self) -> bool:
        if self.key is None or self.key.kind != "private":
            return True
        summary = next((item for item in self.store.state.conversations if item.key == self.key), None)
        state = self.store.state.private_states.get(self.key, {})
        return bool(state.get("can_send", summary.can_send if summary else True))

    def _refresh_private_state(self) -> None:
        allowed = self._private_can_send()
        tip = "" if allowed else "当前私聊不可发送"
        for widget in (self.compose, self.send, self.image, self.sticker, self.ai):
            widget.setEnabled(allowed)
            if widget.toolTip() != tip:
                widget.setToolTip(tip)
        if not allowed:
            self._stop_private_typing()

    def _update_private_typing(self) -> None:
        if (not self.key or self.key.kind != "private" or not self.store.state.connected
                or not self.store.state.capabilities.get("private_chat_v2") or not self._private_can_send()):
            self._stop_private_typing()
            return
        if self.compose.toPlainText().strip():
            if not self._typing_active:
                self._typing_active = True
                self.service.set_private_typing(self.key, True)
                self._typing_timer.start()
        else:
            self._stop_private_typing()

    def _pulse_private_typing(self) -> None:
        if self._typing_active and self.key and self.key.kind == "private" and self.store.state.connected:
            self.service.set_private_typing(self.key, True)
        else:
            self._typing_timer.stop()

    def _stop_private_typing(self) -> None:
        if self._typing_active and self.key and self.key.kind == "private" and self.store.state.connected:
            self.service.set_private_typing(self.key, False)
        self._typing_active = False
        self._typing_timer.stop()

    def _expire_peer_typing(self) -> None:
        if self.key and self.key.kind == "private":
            self.store.state.private_typing[self.key] = False
            self._refresh_subtitle()

    def _refresh_private_outbox(self) -> None:
        current_outbox = self.store.state.private_outbox
        for request_id, previous in self._outbox_seen.items():
            if request_id in current_outbox or previous.get("status") == "failed":
                continue
            self.draft_store.forget_private_send(request_id)
            key = previous.get("key")
            content = previous.get("payload", {}).get("content")
            if isinstance(key, ConversationKey) and content:
                draft = self.draft_store.get(key)
                if (draft and draft.text == content
                        and not (key == self.key and self.compose.toPlainText())
                        and self.draft_store.remove(key)):
                    self.draft_store.flush()
                    self._emit_draft_preview(key, None)
        self._outbox_seen = {request_id: dict(record) for request_id, record in current_outbox.items()}
        clear_layout(self.private_outbox_layout)
        if not self.key or self.key.kind != "private":
            self.private_outbox_panel.hide()
            return
        records = [
            (request_id, record) for request_id, record in self.store.state.private_outbox.items()
            if record.get("key") == self.key
        ]
        for request_id, record in records:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            kind = str(record.get("payload", {}).get("message_type", "text"))
            preview = str(record.get("payload", {}).get("content") or "")[:30] if kind == "text" else "[图片]" if kind == "image" else "[表情包]"
            status = str(record.get("status", "sending"))
            status_text = {"sending": "发送中", "uncertain": "等待重连核对", "retry_ready": "未发送，可手动重试", "failed": "发送失败"}.get(status, status)
            label = QLabel(f"{status_text} · {preview}")
            label.setObjectName("Muted")
            label.setToolTip(str(record.get("error") or status_text))
            line.addWidget(label, 1)
            if status == "retry_ready":
                button = QPushButton("重试")
                button.setObjectName("Ghost")
                button.setAccessibleName(f"重试发送 {preview}")
                button.clicked.connect(lambda _checked=False, value=request_id: self.service.retry_private_send(value))
                line.addWidget(button)
            elif status == "uncertain" and self.store.state.connected and self.store.state.authenticated:
                button = QPushButton("核对")
                button.setObjectName("Ghost")
                button.setAccessibleName(f"核对发送结果 {preview}")
                button.clicked.connect(lambda _checked=False: self.service.reconcile_private_sends())
                line.addWidget(button)
            elif status == "failed":
                button = QPushButton("清除")
                button.setObjectName("Ghost")
                button.setAccessibleName(f"清除失败发送 {preview}")
                button.clicked.connect(lambda _checked=False, value=request_id: self._dismiss_private_send(value))
                line.addWidget(button)
            self.private_outbox_layout.addWidget(row)
        self.private_outbox_panel.setVisible(bool(records))

    def _dismiss_private_send(self, request_id: str) -> None:
        self.store.state.private_outbox.pop(request_id, None)
        self.draft_store.forget_private_send(request_id)
        self._refresh_private_outbox()

    def _refresh_message_avatars(self) -> None:
        for row in tuple(self._message_rows.values()):
            for avatar in row.findChildren(UserAvatar):
                avatar.refresh()

    def update_state(self, topic: str, state: AppState) -> None:
        if topic in {"auth", "session"}:
            self._sync_draft_account(state)
        if topic in {"agent_result", "tools", "selection", "auth", "session"}:
            self._refresh_agent_activity()
        if topic == "connection" and not state.connected:
            self._auto_media_requested.clear()
            self._stop_private_typing()
        if topic == "details" and self._pending_reply_sender_id:
            self._insert_reply_mention()
        if topic == "send_failed":
            self._restore_failed_send(state)
        if topic == "private_typing" and self.key and self.key.kind == "private":
            if state.private_typing.get(self.key):
                self._peer_typing_timer.start()
            else:
                self._peer_typing_timer.stop()
        if topic in {"conversations", "private_outbox", "private_read", "connection", "auth"}:
            self._refresh_private_state()
            self._refresh_private_outbox()
        if topic in {"private_read", "conversations"} and self.key and self.key.kind == "private":
            self.render_messages(count_new=False)
        if (
            topic in {"history", "messages", "agent_result", "error", "connection"}
            and self.key is not None
        ):
            self.set_history_loading(self.key, self.service.history_loading(self.key))
        if topic == "avatar":
            self._refresh_message_avatars()
            for avatar in self.details.findChildren(UserAvatar):
                avatar.refresh()
            self.profile_popover.hide()
            self._refresh_ai_menu_icons()
        elif topic == "profile":
            self.profile_popover.hide()
            self.render_messages(count_new=False, force=True)
        elif topic in {"auth", "history", "media", "details"}:
            self.render_messages(
                count_new=topic == "auth",
                force=topic in {"auth", "details"},
            )
        elif topic in {"messages", "agent_result"}:
            self._schedule_render(
                count_new=True,
            )
        if topic in {"details", "profile", "agent", "agents", "pending", "error", "conversations"}:
            self.details.set_context(self.key, self.title_text, state, topic)
        if topic == "search" and self.key:
            self.search_results.clear()
            for item in state.search_results.get(self.key, []):
                content = _message_preview(item)
                result = QListWidgetItem(f"{item.get('sender_name', '')}: {content}")
                result.setData(Qt.ItemDataRole.UserRole, int(item["id"]))
                self.search_results.addItem(result)
            if state.search_has_more.get(self.key):
                more = QListWidgetItem("加载更多结果")
                more.setData(Qt.ItemDataRole.UserRole, "more")
                self.search_results.addItem(more)
            if not self.search_results.count():
                self._set_search_status("未找到匹配消息")
            self.search_results.show()
        elif topic == "error" and self.search_panel.isVisible() and state.last_error:
            self._set_search_status(f"搜索失败：{state.last_error}")
            self.search_results.show()
        if topic in {"auth", "agents", "details"}:
            self._prefetch_agent_avatars()
        self._refresh_subtitle()

    def _refresh_agent_activity(self) -> None:
        if not self.key:
            self.agent_activity.hide()
            return
        active = next((run for run in self.store.state.agent_runs.values() if run.get("key") == self.key), None)
        if active:
            name = str(active.get("name") or "Agent")
            tool = str(active.get("tool") or "")
            self.agent_activity.setText(f"{name} 正在调用工具：{tool}" if tool else f"{name} 正在回复…")
        else:
            self.agent_activity.setText(self.store.state.agent_notices.get(self.key, ""))
        self.agent_activity.setVisible(bool(self.agent_activity.text()))

    def _refresh_subtitle(self) -> None:
        if not self.key:
            return
        parts = ["群聊" if self.key.kind == "group" else "私聊"]
        if self.key.kind == "private" and self.store.state.private_typing.get(self.key):
            parts.append("对方正在输入…")
        if self.key.kind == "private" and not self._private_can_send():
            parts.append("当前私聊不可发送")
        if self.key.kind == "group":
            member_count = len(self._members())
            if member_count:
                parts.append(f"{member_count} 位成员")
            agent_count = len(
                [
                    item for item in self.store.state.members_by_group.get(self.key.identifier, [])
                    if item.get("kind") == "agent"
                ]
            )
            if agent_count:
                parts.append(f"{agent_count} 个 AI Agent")
        parts.append("Agent 在线" if self.store.state.agent_online else "Agent 离线")
        self.subtitle.setText(" · ".join(parts))

    def _show_message_profile(self, profile: object, anchor: QWidget) -> None:
        self.profile_popover.show_for(anchor, profile, pinned=True)

    def _profile_member(self, profile: object) -> dict[str, Any] | None:
        if not self.key or self.key.kind != "group":
            return None
        kind = str(getattr(profile, "kind", "user"))
        agent_id = str(getattr(profile, "agent_id", "") or "")
        user_id = getattr(profile, "user_id", None)
        if kind != "agent" and user_id is not None and str(user_id) == str(
            self.store.state.current_user.get("id", "")
        ):
            return None
        for member in self._members():
            is_agent = member.get("kind") == "agent"
            if kind == "agent" and is_agent and str(member.get("id")) == agent_id:
                return dict(member)
            if kind != "agent" and not is_agent and str(member.get("id")) == str(user_id):
                return dict(member)
        display_name = str(getattr(profile, "display_name", "") or "")
        if kind == "agent" and agent_id:
            return {
                "id": agent_id,
                "member_id": f"agent:{agent_id}",
                "kind": "agent",
                "username": display_name,
                "display_name": display_name,
            }
        if user_id is not None:
            return {
                "id": user_id,
                "member_id": f"user:{user_id}",
                "kind": "user",
                "username": str(getattr(profile, "username", "") or display_name),
                "display_name": display_name,
            }
        return None

    def _insert_profile_mention(self, profile: object) -> None:
        member = self._profile_member(profile)
        if not member:
            return
        if self.compose.toPlainText() and not self.compose.toPlainText()[-1].isspace():
            self.compose.insertPlainText(" ")
        self.compose.setFocus()
        self.compose.insertPlainText("@")
        self.compose._update_mention_query()
        self.compose.insert_mention(member)

    def _mention_from_details(self, profile: object) -> None:
        self._hide_details()
        self._insert_profile_mention(profile)

    def _moderate_profile(self, profile: object, action: str) -> None:
        user_id = getattr(profile, "user_id", None)
        if not self.key or self.key.kind != "group" or user_id is None:
            return
        self.service.moderate(self.key.identifier, int(user_id), action == "mute")

    def _avatar_menu(self, profile: object, anchor: QWidget) -> QMenu:
        return build_profile_menu(self, profile, can_mention=bool(self._profile_member(profile)))

    def _show_avatar_menu(self, profile: object, anchor: QWidget) -> None:
        self.profile_popover.hide()
        menu = self._avatar_menu(profile, anchor)
        selected = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if selected is None:
            return
        kind, value = selected.data() or ("", "")
        if kind == "mention":
            self._insert_profile_mention(profile)
        elif kind == "view":
            self.profile_popover.show_for(anchor, profile, pinned=True)
        elif kind == "copy" and value:
            QApplication.clipboard().setText(str(value))
        elif kind == "moderate":
            self._moderate_profile(profile, str(value))

    def _toggle_details(self) -> None:
        if self.details.isVisible():
            self._hide_details()
        else:
            self.details.show()
            self._layout_details()

    def _hide_details(self) -> None:
        self.details.hide()
        self.details_scrim.hide()

    def _open_group_agent_manager(self, group_id: str) -> None:
        dialog = AgentManagerDialog(
            self.service, self.store, self, group_id=str(group_id)
        )
        dialog.exec()

    def _open_group_agent_memory(self, group_id: str, agent_id: str) -> None:
        if not self.key or self.key.kind != "group" or self.key.identifier != str(group_id):
            return
        dialog = MemoryDialog(
            self.service, self.store, self, preferred_agent_id=str(agent_id)
        )
        dialog.exec()

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
            stick_to_bottom = self._is_at_bottom() if self.scroll.isVisible() else self._follow_pending
        super().resizeEvent(event)
        self._layout_details()
        self._position_new_messages_badge()
        if stick_to_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self, *, smooth: bool = False) -> None:
        try:
            bar = self.scroll.verticalScrollBar()
        except RuntimeError:
            return
        self._follow_pending = True
        if smooth and bar.maximum() > bar.value():
            self._scroll_animation.stop()
            self._animating_scroll = True
            self._scroll_animation.setStartValue(bar.value())
            self._scroll_animation.setEndValue(bar.maximum())
            self._scroll_animation.start()
            return
        self._stop_scroll_animation()
        bar.setValue(bar.maximum())
        self._pending_new_messages = 0
        self._sync_new_messages_badge()

    def _send(self) -> None:
        if not self.key or not self._private_can_send():
            return
        raw_text = self.compose.toPlainText()
        if not raw_text.strip():
            return
        key = self.key
        request_id = self.service.send_text(
            key, raw_text, self.compose.mentions(), self._reply_to_id
        )
        if request_id:
            if key.kind == "private" and request_id in self.store.state.private_outbox:
                self.draft_store.remember_private_send(
                    request_id, key, self.store.state.private_outbox[request_id]["payload"]
                )
            self._restoring_draft = True
            try:
                self.compose.blockSignals(True)
                self.compose.clear()
                self.compose.document().clearUndoRedoStacks(
                    QTextDocument.Stacks.UndoAndRedoStacks
                )
                self._clear_reply(schedule_draft=False)
            finally:
                self.compose.blockSignals(False)
                self._restoring_draft = False
            self._draft_timer.stop()
            if self.draft_store.remove(key):
                self.draft_store.flush()
            self._emit_draft_preview(key, None)
            self._hide_mentions()
            self._stop_private_typing()
            self._follow_latest_after_send()
        else:
            self._capture_draft_change()
            self.subtitle.setText(
                f"发送失败：{self.store.state.last_error or '未连接到聊天服务器'}"
            )
            self._send_feedback_timer.start()

    def _follow_latest_after_send(self) -> None:
        self._force_follow = True
        self._pending_new_messages = 0
        self._sync_new_messages_badge()
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _set_reply_target(self, message_id: int) -> None:
        self._reply_to_id = message_id
        original = next((item for item in self.store.state.messages.get(self.key, []) if int(item.get("id", 0)) == message_id), None) if self.key else None
        self.reply_hint.setText(f"回复 {original.get('sender_name', '消息') if original else '消息'} · 取消")
        self.reply_hint.show()

    def _reply_to(self, message_id: int) -> None:
        self._set_reply_target(message_id)
        original = next((item for item in self.store.state.messages.get(self.key, []) if int(item.get("id", 0)) == message_id), None) if self.key else None
        if self.key and self.key.kind == "group" and original and str(original.get("sender_id", "")) != str(self.store.state.current_user.get("id", "")):
            self._pending_reply_sender_id = str(original.get("sender_id"))
            self._insert_reply_mention()
            if self._pending_reply_sender_id:
                self.service.request("group.members", {"group_id": self.key.identifier})
        self.compose.setFocus()
        self._capture_draft_change()

    def _insert_reply_mention(self) -> None:
        sender_id = self._pending_reply_sender_id
        members = self.store.state.members_by_group.get(self.key.identifier, []) if self.key else []
        member = next((item for item in members if str(item.get("id")) == sender_id and item.get("kind") != "agent"), None)
        if not member:
            return
        self._pending_reply_sender_id = None
        if any(str(mention.get("member_id")) == f"user:{member['id']}" for mention in self.compose.mentions()):
            return
        if self.compose.toPlainText() and not self.compose.toPlainText()[-1].isspace():
            self.compose.insertPlainText(" ")
        self.compose.insertPlainText("@")
        self.compose._update_mention_query()
        self.compose.insert_mention(member)

    def _clear_reply(self, *, schedule_draft: bool = True) -> None:
        self._reply_to_id = None
        self._pending_reply_sender_id = None
        self.reply_hint.hide()
        if schedule_draft:
            self._capture_draft_change()

    def _edit_message(self, message_id: int) -> None:
        if not self.key:
            return
        original = next((item for item in self.store.state.messages.get(self.key, []) if int(item.get("id", 0)) == message_id), None)
        if not original:
            return
        content, accepted = QInputDialog.getMultiLineText(self, "编辑消息", "消息内容", str(original.get("content", "")))
        if accepted and content.strip():
            self.service.edit(message_id, content)

    def _open_search_result(self, item: QListWidgetItem) -> None:
        value = item.data(Qt.ItemDataRole.UserRole)
        if value == "more" and self.key:
            results = self.store.state.search_results.get(self.key, [])
            if results:
                self.service.search(self.key, self.search_input.text().strip(), int(results[-1]["id"]))
        elif isinstance(value, int):
            self.focus_message(value)
            if value not in self._message_rows and self.key:
                self.service.load_result(self.key, value)

    def _members(self) -> list[dict[str, Any]]:
        if not self.key or self.key.kind != "group":
            return []
        return self.store.state.members_by_group.get(self.key.identifier, self.store.state.members)

    def _show_mentions(self, query: str) -> None:
        if not self.key or self.key.kind != "group":
            self._hide_mentions()
            return
        folded = query.casefold()
        candidates = [member for member in self._members() if folded in f"{member.get('username', '')} {member.get('display_name') or ''} {member.get('nickname') or ''}".casefold()]
        self.mention_list.clear()
        for member in candidates:
            item = QListWidgetItem(str(member.get("nickname") or member.get("display_name") or member.get("username", "")))
            item.setSizeHint(QSize(0, 44))
            item.setData(Qt.ItemDataRole.UserRole, dict(member))
            is_agent = member.get("kind") == "agent"
            profile = self.service.profile(
                None if is_agent else int(member.get("id", 0) or 0),
                member,
                self.key.identifier if self.key else None,
            )
            avatar = UserAvatar(self.service, profile, 30, hoverable=False)
            item.setIcon(QIcon(avatar.pixmap()))
            if is_agent:
                item.setText(f"{member.get('username') or member.get('display_name') or 'AI Agent'}  ·  智能助手")
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

    def _insert_agent_mention(self, agent: dict[str, Any] | None = None) -> None:
        if not self.key or self.key.kind != "group":
            return
        candidates = self._conversation_agents()
        member = dict(agent) if isinstance(agent, dict) else (candidates[0] if candidates else None)
        if not member:
            return
        member.setdefault("kind", "agent")
        if not member.get("member_id") and member.get("id"):
            member["member_id"] = f"agent:{member['id']}"
        self.compose.setFocus()
        self.compose.insertPlainText("@")
        self.compose._update_mention_query()
        self.compose.insert_mention(member)

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
        if not self.key or not self._private_can_send():
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
        if self.service.send_image(self.key, Path(path).name, mime, base64.b64encode(raw).decode("ascii"), len(raw)):
            self._follow_latest_after_send()

    def _open_sticker_panel(self) -> None:
        if not self.key:
            return
        try:
            if self._sticker_library is None:
                self._sticker_library = LocalStickerLibrary()
            self._sticker_panel = StickerPanel(self._sticker_library, self)
        except OSError as exc:
            QMessageBox.warning(self, "表情包不可用", f"无法打开本地表情包库：{exc}")
            return
        self._sticker_panel.sticker_selected.connect(self._send_sticker)
        self._sticker_panel.open()

    def _send_sticker(self, sticker: dict[str, Any]) -> None:
        if not self.key or not self._private_can_send() or self._sticker_library is None:
            return
        path = Path(str(sticker.get("path", "")))
        try:
            raw = path.read_bytes()
        except OSError:
            QMessageBox.warning(self, "发送失败", "表情包文件已不存在，请重新导入。")
            if self._sticker_panel:
                self._sticker_panel.refresh()
            return
        if not 0 < len(raw) <= MAX_STICKER_SIZE:
            QMessageBox.warning(self, "发送失败", "表情包不能为空且不能超过 2 MiB。")
            return
        request_id = self.service.send_sticker(
            self.key,
            str(sticker.get("id", "")),
            str(sticker.get("name") or path.stem or "表情包"),
            str(sticker.get("source_name") or path.name),
            str(sticker.get("mime") or mimetypes.guess_type(path.name)[0] or ""),
            base64.b64encode(raw).decode("ascii"),
            len(raw),
            self._reply_to_id,
        )
        if request_id:
            try:
                self._sticker_library.mark_used(str(sticker.get("id", "")))
            except OSError:
                pass
            self._clear_reply()
            self._follow_latest_after_send()


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
            item.setSizeHint(QSize(0, 52))
            item.setData(Qt.ItemDataRole.UserRole, dict(user))
            avatar = UserAvatar(self.service, profile, 32, hoverable=False)
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
            item.setIcon(QIcon(Avatar(str(group.get("name", "")), 30, group=True).pixmap()))
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


TASK_TYPE_LABELS = {
    "summary": "总结会话",
    "todo": "提取待办",
    "question": "回答问题",
    "search": "搜索并整理会话",
    "identity": "Agent 信息",
    "scheduled_summary": "定时总结",
}
TASK_CENTER_TYPES = ("summary", "todo", "search", "scheduled_summary")
TASK_STATUS_LABELS = {"running": "进行中", "succeeded": "已完成", "failed": "失败"}


def task_error_text(message: str) -> str:
    detail = str(message or "").lower()
    if "database" in detail or "unable to open" in detail or "数据库" in detail:
        return "服务暂时无法访问数据，请稍后重试"
    if "timeout" in detail or "timed out" in detail:
        return "任务执行超时，请稍后重试"
    if any(term in detail for term in ("model", "模型", "ollama", "connection", "unreachable")):
        return "AI 服务暂时不可用，请稍后重试"
    return "任务执行失败，请稍后重试"


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
        error = task_error_text(str(activity.get("error") or ""))
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
            failure = QLabel(task_error_text(error))
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
        root.addWidget(self.task_type)
        hint = QLabel("任务结果会显示在对应会话中；普通问答请直接在聊天里发起。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)
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
        }
        return (key if isinstance(key, ConversationKey) else None, prompts.get(task_type, ""))


class AICenterPage(QWidget):
    result_requested = Signal(object, int)

    def __init__(self, service: ChatService, store: AppStore) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        self._render_signature: tuple[Any, ...] | None = None
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
        title = QLabel("我的 AI 任务")
        title.setObjectName("PageTitle")
        self.new_task = QPushButton("新建 AI 任务")
        self.new_task.setObjectName("Primary")
        self.new_task.setEnabled(False)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.new_task)
        self.subtitle = QLabel("只显示你发起的总结、待办与整理任务；普通问答保留在会话中。")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
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
        for value in TASK_CENTER_TYPES:
            self.type_filter.addItem(TASK_TYPE_LABELS[value], value)
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
        content.addWidget(self.subtitle)
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
        if topic not in {
            "auth",
            "connection",
            "session",
            "activities",
            "agent",
            "conversations",
            "pending",
            "error",
            "ack",
        }:
            return
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
        status = str(self.status_filter.currentData() or "")
        task_type = str(self.type_filter.currentData() or "")
        scope = self.scope_filter.currentData()
        current_user_id = self.store.state.current_user.get("id")
        activities = [
            item for item in self.store.state.activities
            if str(item.get("task_type") or "") in TASK_CENTER_TYPES
            and (
                current_user_id is None
                or not item.get("requested_by")
                or str(item.get("requested_by")) == str(current_user_id)
            )
            and (not status or item.get("status") == status)
            and (not task_type or item.get("task_type") == task_type)
            and (
                not isinstance(scope, ConversationKey)
                or (str(item.get("conversation_type")) == scope.kind and str(item.get("conversation_id")) == scope.identifier)
            )
        ]
        signature = (
            current_user_id,
            status,
            task_type,
            scope,
            tuple(
                (
                    item.get("id"),
                    item.get("status"),
                    item.get("error"),
                    item.get("result_message_id"),
                    item.get("finished_at"),
                    self._conversation_title(
                        ConversationKey(
                            str(item.get("conversation_type", "")),
                            str(item.get("conversation_id", "")),
                        )
                    ),
                )
                for item in activities
            ),
        )
        if signature == self._render_signature and self.task_rows.count():
            return
        self._render_signature = signature
        clear_layout(self.task_rows)
        self.filter_summary.setText(f"{len(activities)} 个任务")
        if not activities:
            filtered = bool(status or task_type or isinstance(scope, ConversationKey))
            self.task_rows.addWidget(
                EmptyState(
                    "没有匹配的任务" if filtered else "还没有个人 AI 任务",
                    "调整筛选条件"
                    if filtered
                    else "普通问答会保留在会话中；使用“新建 AI 任务”发起总结或待办后，会显示在这里",
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
            QMessageBox.warning(self, "任务信息不完整", "请选择要处理的会话。")
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


class AgentManagerDialog(QDialog):
    def __init__(
        self,
        service: ChatService,
        store: AppStore,
        parent: QWidget | None = None,
        *,
        group_id: str = "",
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.store = store
        self.current_id = ""
        self.context_group_id = str(group_id or "")
        self.setWindowTitle("Agent 管理")
        self.resize(820, 620)
        self._unsubscribe = store.subscribe(self._store_changed)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        left = QVBoxLayout()
        heading = QLabel("Agent")
        heading.setObjectName("SectionTitle")
        self.agent_list = QListWidget()
        self.agent_list.setMinimumWidth(220)
        self.agent_list.setIconSize(QSize(28, 28))
        self.new_button = QPushButton("新建 Agent")
        self.new_button.setObjectName("Primary")
        left.addWidget(heading)
        left.addWidget(self.agent_list, 1)
        left.addWidget(self.new_button)
        root.addLayout(left)

        editor = QScrollArea()
        editor.setWidgetResizable(True)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(10, 4, 10, 10)
        title_row = QHBoxLayout()
        self.avatar_preview = UserAvatar(
            self.service,
            self.service.profile(None, {"id": "", "name": "新 Agent", "kind": "agent"}),
            48,
        )
        self.editor_title = QLabel("新建 Agent")
        self.editor_title.setObjectName("PageTitle")
        title_row.addWidget(self.avatar_preview)
        title_row.addWidget(self.editor_title, 1)
        form.addLayout(title_row)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：项目分析师")
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("一句话介绍这个 Agent")
        self.model_combo = QComboBox()
        self.enabled_check = QCheckBox("启用")
        self.memory_check = QCheckBox("允许长期记忆（仍需在会话内明确开启）")
        self.auto_sticker_check = QCheckBox("允许自动发送表情包")
        form.addWidget(QLabel("名称"))
        form.addWidget(self.name_edit)
        form.addWidget(QLabel("简介"))
        form.addWidget(self.description_edit)
        form.addWidget(QLabel("模型配置"))
        form.addWidget(self.model_combo)
        form.addWidget(self.enabled_check)
        form.addWidget(self.memory_check)
        form.addWidget(self.auto_sticker_check)

        card_title = QLabel("角色卡")
        card_title.setObjectName("SectionTitle")
        form.addWidget(card_title)
        self.card_editors: dict[str, QTextEdit] = {}
        for field, label, placeholder in (
            ("personality", "性格", "例如：冷静、直接、先澄清事实"),
            ("background", "背景", "例如：长期参与交付项目"),
            ("language_style", "语言风格", "例如：先结论后依据，使用短句"),
            ("behavior_rules", "行为规则", "描述表达与工作偏好；不能改变服务端工具权限"),
        ):
            edit = QTextEdit()
            edit.setPlaceholderText(placeholder)
            edit.setMaximumHeight(78)
            self.card_editors[field] = edit
            form.addWidget(QLabel(label))
            form.addWidget(edit)

        tool_title = QLabel("工具权限")
        tool_title.setObjectName("SectionTitle")
        form.addWidget(tool_title)
        self.tool_list = QListWidget()
        self.tool_list.setMaximumHeight(160)
        form.addWidget(self.tool_list)
        self.save_tools = QPushButton("保存工具权限")
        form.addWidget(self.save_tools, alignment=Qt.AlignmentFlag.AlignLeft)

        group_title = QLabel("群组关联")
        group_title.setObjectName("SectionTitle")
        form.addWidget(group_title)
        group_row = QHBoxLayout()
        self.group_combo = QComboBox()
        self.add_group = QPushButton("加入群组")
        self.remove_group = QPushButton("从群组移除")
        self.remove_group.setObjectName("Ghost")
        group_row.addWidget(self.group_combo, 1)
        group_row.addWidget(self.add_group)
        group_row.addWidget(self.remove_group)
        form.addLayout(group_row)
        group_sticker_row = QHBoxLayout()
        self.group_sticker_check = QCheckBox("在选中群组自动发送表情包")
        self.group_sticker_button = QPushButton("保存群组设置")
        self.group_sticker_button.setObjectName("Ghost")
        group_sticker_row.addWidget(self.group_sticker_check)
        group_sticker_row.addWidget(self.group_sticker_button)
        form.addLayout(group_sticker_row)
        group_help = QLabel("只有群主或管理员可以调整群组关联与群内工具权限。")
        group_help.setObjectName("Muted")
        group_help.setWordWrap(True)
        form.addWidget(group_help)

        self.notice = QLabel("")
        self.notice.setObjectName("InlineNotice")
        self.notice.setProperty("noticeKind", "info")
        self.notice.hide()
        form.addWidget(self.notice)
        actions = QHBoxLayout()
        self.avatar_button = QPushButton("上传头像")
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("Primary")
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerButton")
        actions.addWidget(self.avatar_button)
        actions.addStretch()
        actions.addWidget(self.delete_button)
        actions.addWidget(self.save_button)
        form.addLayout(actions)
        form.addStretch()
        configure_scroll_surface(editor, content, "surface")
        editor.setWidget(content)
        root.addWidget(editor, 1)

        self.agent_list.currentItemChanged.connect(self._selection_changed)
        self.new_button.clicked.connect(self._start_new)
        self.save_button.clicked.connect(self._save)
        self.delete_button.clicked.connect(self._delete)
        self.avatar_button.clicked.connect(self._upload_avatar)
        self.save_tools.clicked.connect(self._save_tools)
        self.add_group.clicked.connect(lambda: self._change_group(True))
        self.remove_group.clicked.connect(lambda: self._change_group(False))
        self.group_combo.currentIndexChanged.connect(self._refresh_group_sticker)
        self.group_sticker_button.clicked.connect(self._save_group_sticker)
        self.service.request("agent.list")
        self.service.request("tool.list")
        self._start_new()
        self._render_agents()

    def closeEvent(self, event: Any) -> None:
        self._unsubscribe()
        super().closeEvent(event)

    def _store_changed(self, topic: str, _state: AppState) -> None:
        if topic in {"agents", "auth", "avatar"}:
            self._render_agents()
            self._refresh_group_sticker()
            if self.current_id:
                self._request_tools(self.current_id)
                agent = next((item for item in self.store.state.agents if str(item.get("id")) == self.current_id), None)
                if agent:
                    self.avatar_preview.refresh(self.service.profile(None, agent))
        if topic == "ack" and self.store.state.last_error == "":
            self._notice("配置已提交", "success")

    def _notice(self, message: str, kind: str = "info") -> None:
        self.notice.setText(message)
        self.notice.setProperty("noticeKind", kind)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        self.notice.setVisible(bool(message))

    def _render_agents(self) -> None:
        selected = self.current_id
        self.agent_list.blockSignals(True)
        self.agent_list.clear()
        for agent in self.store.state.agents:
            label = str(agent.get("name") or "未命名 Agent")
            suffix = "" if agent.get("enabled", True) else " · 已停用"
            if agent.get("builtin"):
                suffix += " · 预设"
            item = QListWidgetItem(f"{label}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, str(agent.get("id", "")))
            item.setIcon(self._agent_icon(agent))
            self.agent_list.addItem(item)
            if str(agent.get("id", "")) == selected:
                self.agent_list.setCurrentItem(item)
        self.agent_list.blockSignals(False)

    def _agent_icon(self, agent: dict[str, Any]) -> QIcon:
        profile = self.service.profile(None, agent)
        data = self.service.avatar_data(profile)
        return QIcon(avatar_pixmap(profile.display_name, 28, data, agent=True))

    def _selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        agent_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        agent = next((item for item in self.store.state.agents if str(item.get("id")) == agent_id), None)
        if agent:
            self._load_agent(agent)

    def _start_new(self) -> None:
        self.current_id = ""
        self.agent_list.setCurrentRow(-1)
        self.editor_title.setText("新建 Agent")
        self.avatar_preview.refresh(self.service.profile(None, {"id": "", "name": "新 Agent", "kind": "agent"}))
        self.name_edit.clear()
        self.description_edit.clear()
        for editor in self.card_editors.values():
            editor.clear()
        self.enabled_check.setChecked(True)
        self.memory_check.setChecked(True)
        self.auto_sticker_check.setChecked(True)
        self.model_combo.clear()
        self.model_combo.addItem("使用默认模型", None)
        for model in self.store.state.agent_models:
            label = f"{model.get('name')} · {model.get('model')}"
            if model.get("is_default"):
                label += "（默认）"
            self.model_combo.addItem(label, model.get("id"))
        self.tool_list.clear()
        for tool in self.store.state.tool_definitions:
            item = QListWidgetItem(f"{tool.get('label')}  ·  {'需确认' if tool.get('mode') == 'confirm' else '自动'}")
            item.setData(Qt.ItemDataRole.UserRole, str(tool.get("name")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.tool_list.addItem(item)
        self.delete_button.setEnabled(False)
        self.avatar_button.setEnabled(False)
        self.save_tools.setEnabled(False)
        self.save_button.setEnabled(True)
        self._fill_groups()

    def _load_agent(self, agent: dict[str, Any]) -> None:
        self.current_id = str(agent.get("id") or "")
        self.editor_title.setText(f"编辑 {agent.get('name') or 'Agent'}")
        self.avatar_preview.refresh(self.service.profile(None, agent))
        self.name_edit.setText(str(agent.get("name") or ""))
        self.description_edit.setText(str(agent.get("description") or ""))
        card = agent.get("card") if isinstance(agent.get("card"), dict) else {}
        for field, editor in self.card_editors.items():
            editor.setPlainText(str(card.get(field) or ""))
        self.enabled_check.setChecked(bool(agent.get("enabled", True)))
        self.memory_check.setChecked(bool(agent.get("memory_enabled", True)))
        self.auto_sticker_check.setChecked(bool(agent.get("auto_sticker", True)))
        self.model_combo.clear()
        self.model_combo.addItem("使用默认模型", None)
        for model in self.store.state.agent_models:
            label = f"{model.get('name')} · {model.get('model')}"
            if model.get("is_default"):
                label += "（默认）"
            self.model_combo.addItem(label, model.get("id"))
        index = self.model_combo.findData(agent.get("model_profile_id"))
        self.model_combo.setCurrentIndex(max(0, index))
        self.delete_button.setEnabled(self.current_id != "agent" and bool(agent.get("can_manage")))
        self.avatar_button.setEnabled(bool(agent.get("can_manage")))
        self.save_tools.setEnabled(bool(agent.get("can_manage")))
        self.save_button.setEnabled(bool(agent.get("can_manage")))
        self._fill_groups()
        self._request_tools(self.current_id)

    def _request_tools(self, agent_id: str) -> None:
        tools = self.store.state.agent_tools.get(agent_id)
        if tools is None:
            self.service.request("agent.tool.list", {"agent_id": agent_id})
            return
        self.tool_list.clear()
        for tool in tools:
            item = QListWidgetItem(
                f"{tool.get('label')}  ·  {'需确认' if tool.get('mode') == 'confirm' else '自动'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, str(tool.get("name")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if tool.get("enabled", True) else Qt.CheckState.Unchecked
            )
            self.tool_list.addItem(item)

    def _fill_groups(self) -> None:
        current = self.group_combo.currentData()
        self.group_combo.clear()
        for group in self.store.state.groups:
            if group.get("joined") or group.get("name") == "大厅":
                self.group_combo.addItem(str(group.get("name")), str(group.get("id")))
        index = self.group_combo.findData(current)
        if index >= 0:
            self.group_combo.setCurrentIndex(index)
        elif self.context_group_id:
            index = self.group_combo.findData(self.context_group_id)
            if index >= 0:
                self.group_combo.setCurrentIndex(index)
                self.add_group.setText("加入当前群")
        self._refresh_group_sticker()

    def _refresh_group_sticker(self) -> None:
        group_id = str(self.group_combo.currentData() or "")
        selected = next((item for item in self.store.state.group_agents.get(group_id, []) if str(item.get("id")) == self.current_id), None)
        group = next((item for item in self.store.state.groups if str(item.get("id")) == group_id), {})
        current_user_id = self.store.state.current_user.get("id")
        can_manage = self.store.state.current_user.get("role") == "admin" or (current_user_id is not None and str(group.get("owner_id")) == str(current_user_id))
        self.group_sticker_check.setEnabled(bool(selected and can_manage))
        self.group_sticker_button.setEnabled(bool(selected and can_manage))
        self.group_sticker_check.setChecked(bool(selected and selected.get("group_auto_sticker", True)))
        if group_id and group_id not in self.store.state.group_agents:
            self.service.request("agent.group.list", {"group_id": group_id})

    def _save_group_sticker(self) -> None:
        group_id = str(self.group_combo.currentData() or "")
        if group_id and self.current_id:
            self.service.set_group_auto_sticker(group_id, self.current_id, self.group_sticker_check.isChecked())
            self._notice("正在更新群组自动表情包设置…", "info")

    def _payload(self) -> dict[str, Any]:
        card = {field: editor.toPlainText().strip() for field, editor in self.card_editors.items()}
        return {
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.text().strip(),
            "card": card,
            "model_profile_id": self.model_combo.currentData(),
            "enabled": self.enabled_check.isChecked(),
            "memory_enabled": self.memory_check.isChecked(),
            "auto_sticker": self.auto_sticker_check.isChecked(),
        }


    def _save(self) -> None:
        payload = self._payload()
        if not payload["name"]:
            self._notice("请填写 Agent 名称", "error")
            return
        if self.current_id:
            request_id = self.service.update_agent(self.current_id, **payload)
        else:
            if self.context_group_id:
                payload["group_id"] = self.context_group_id
            request_id = self.service.create_agent(**payload)
        if not request_id:
            self._notice(self.store.state.last_error or "提交失败", "error")
            return
        self._notice("正在保存…", "info")

    def _delete(self) -> None:
        if not self.current_id:
            return
        if QMessageBox.question(self, "删除 Agent", "删除后该 Agent 的群组关联和记忆也会移除，继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.service.delete_agent(self.current_id)
        self._start_new()

    def _upload_avatar(self) -> None:
        if not self.current_id:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 Agent 头像", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp)")
        if not path:
            return
        if os.path.getsize(path) > MAX_AVATAR_SIZE:
            self._notice("头像不能超过 2 MiB", "error")
            return
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        self.service.set_agent_avatar(self.current_id, mime, encoded)
        self._notice("正在上传头像…", "info")

    def _save_tools(self) -> None:
        if not self.current_id:
            return
        names = [
            str(self.tool_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.tool_list.count())
            if self.tool_list.item(index).checkState() == Qt.CheckState.Checked
        ]
        self.service.set_agent_tools(self.current_id, names)
        self._notice("正在更新工具权限…", "info")

    def _change_group(self, enabled: bool) -> None:
        if not self.current_id or not self.group_combo.currentData():
            return
        self.service.set_group_agent(
            str(self.group_combo.currentData()), self.current_id, enabled
        )
        self._notice("正在更新群组关联…", "info")


class MemoryDialog(QDialog):
    def __init__(
        self,
        service: ChatService,
        store: AppStore,
        parent: QWidget | None = None,
        *,
        preferred_agent_id: str = "",
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.store = store
        self.preferred_agent_id = str(preferred_agent_id or "")
        self.setWindowTitle("Agent 记忆")
        self.resize(620, 520)
        self._unsubscribe = store.subscribe(self._store_changed)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        title = QLabel("当前会话的 Agent 记忆")
        title.setObjectName("PageTitle")
        self.scope_label = QLabel("")
        self.scope_label.setObjectName("Muted")
        root.addWidget(title)
        root.addWidget(self.scope_label)
        agent_row = QHBoxLayout()
        self.agent_combo = QComboBox()
        agent_row.addWidget(QLabel("Agent"))
        agent_row.addWidget(self.agent_combo, 1)
        root.addLayout(agent_row)
        self.enabled_check = QCheckBox("允许该 Agent 在本会话使用长期记忆")
        root.addWidget(self.enabled_check)
        self.memory_list = QListWidget()
        root.addWidget(self.memory_list, 1)
        self.notice = QLabel("")
        self.notice.setObjectName("InlineNotice")
        self.notice.setProperty("noticeKind", "info")
        self.notice.hide()
        root.addWidget(self.notice)
        actions = QHBoxLayout()
        self.add_button = QPushButton("新增记忆")
        self.edit_button = QPushButton("编辑")
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerButton")
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("Ghost")
        actions.addWidget(self.add_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        actions.addWidget(self.refresh_button)
        root.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        root.addWidget(close)
        self.agent_combo.currentIndexChanged.connect(self._reload)
        self.enabled_check.clicked.connect(self._toggle_memory)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.refresh_button.clicked.connect(self._reload)
        self._refresh_agents()

    def closeEvent(self, event: Any) -> None:
        self._unsubscribe()
        super().closeEvent(event)

    def _store_changed(self, topic: str, _state: AppState) -> None:
        if topic == "agents":
            self._refresh_agents()
        if topic == "memories":
            self._render_memories()

    def _key(self) -> ConversationKey | None:
        key = self.store.state.current_conversation
        return key if key and key.kind in {"group", "private"} else None

    def _agent_id(self) -> str:
        return str(self.agent_combo.currentData() or "agent")

    def _refresh_agents(self) -> None:
        current = self.agent_combo.currentData()
        self.agent_combo.blockSignals(True)
        self.agent_combo.clear()
        key = self._key()
        available: set[str] | None = None
        if key and key.kind == "group":
            members = self.store.state.members_by_group.get(key.identifier, [])
            if members:
                available = {
                    str(member.get("id"))
                    for member in members
                    if member.get("kind") == "agent"
                }
        for agent in self.store.state.agents:
            if agent.get("enabled", True) and (available is None or str(agent.get("id")) in available):
                self.agent_combo.addItem(str(agent.get("name") or "Agent"), str(agent.get("id")))
        index = self.agent_combo.findData(current)
        if index < 0 and self.preferred_agent_id:
            index = self.agent_combo.findData(self.preferred_agent_id)
        if index >= 0:
            self.agent_combo.setCurrentIndex(index)
        self.agent_combo.blockSignals(False)
        self._reload()

    def _reload(self) -> None:
        key = self._key()
        if not key:
            self.scope_label.setText("请先在聊天页选择一个会话。")
            self.memory_list.clear()
            self.enabled_check.setEnabled(False)
            return
        self.scope_label.setText(
            f"{'群聊共享记忆' if key.kind == 'group' else '个人私聊记忆'} · {key.identifier}"
        )
        self.enabled_check.setEnabled(True)
        self.service.load_memories(key, self._agent_id())
        self._render_memories()

    def _render_memories(self) -> None:
        key = self._key()
        if not key:
            return
        storage_key = (self._agent_id(), key.kind, key.identifier)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(bool(self.store.state.memory_settings.get(storage_key, False)))
        self.enabled_check.blockSignals(False)
        self.memory_list.clear()
        for memory in self.store.state.memories.get(storage_key, []):
            preview = str(memory.get("content") or "").replace("\n", " ")[:100]
            kind = str(memory.get("kind") or "note")
            item = QListWidgetItem(f"[{kind}] {preview}")
            item.setData(Qt.ItemDataRole.UserRole, int(memory.get("id", 0) or 0))
            self.memory_list.addItem(item)

    def _toggle_memory(self) -> None:
        key = self._key()
        if not key:
            return
        self.service.set_memory_enabled(key, self._agent_id(), self.enabled_check.isChecked())
        self._notice("记忆开关已提交")

    def _add(self) -> None:
        key = self._key()
        if not key:
            return
        value, ok = QInputDialog.getMultiLineText(self, "新增记忆", "内容")
        if ok and value.strip():
            self.service.add_memory(key, self._agent_id(), value.strip())
            self._notice("正在保存记忆…")

    def _selected_memory_id(self) -> int:
        item = self.memory_list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole) or 0) if item else 0

    def _edit(self) -> None:
        memory_id = self._selected_memory_id()
        if not memory_id:
            return
        key = self._key()
        if not key:
            return
        current = next(
            (item for item in self.store.state.memories.get((self._agent_id(), key.kind, key.identifier), []) if int(item.get("id", 0)) == memory_id),
            {},
        )
        value, ok = QInputDialog.getMultiLineText(
            self, "编辑记忆", "内容", str(current.get("content") or "")
        )
        if ok and value.strip():
            self.service.update_memory(memory_id, value.strip(), str(current.get("kind") or "note"))
            self._notice("正在更新记忆…")

    def _delete(self) -> None:
        memory_id = self._selected_memory_id()
        if not memory_id:
            return
        if QMessageBox.question(self, "删除记忆", "删除后无法恢复，继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.service.delete_memory(memory_id)
        self._notice("正在删除记忆…")

    def _notice(self, message: str, kind: str = "info") -> None:
        self.notice.setText(message)
        self.notice.setProperty("noticeKind", kind)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        self.notice.setVisible(bool(message))


class SettingsPage(QWidget):
    logout_requested = Signal()
    density_changed = Signal(str)
    test_notification_requested = Signal()
    COMPACT_BREAKPOINT = 760

    def __init__(self, service: ChatService, store: AppStore, host: str, port: int) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        self._host = host
        self._port = port
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
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("登录时使用的账号")
        self.username_edit.setMaxLength(20)
        self.username_edit.setAccessibleName("账号")
        self.display_name_edit = QLineEdit()
        self.display_name_edit.setPlaceholderText("聊天时显示的昵称")
        self.display_name_edit.setMaxLength(30)
        self.display_name_edit.setAccessibleName("昵称")
        self.save_identity = QPushButton("保存")
        self.save_identity.clicked.connect(self._save_identity)
        profile_content.addWidget(QLabel("账号"))
        profile_content.addWidget(self.username_edit)
        profile_content.addWidget(QLabel("昵称"))
        profile_content.addWidget(self.display_name_edit)
        profile_content.addWidget(self.save_identity, alignment=Qt.AlignmentFlag.AlignLeft)
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
        self.enter_to_send = QCheckBox("按 Enter 发送；Shift+Enter 换行")
        self.enter_to_send.setChecked(QSettings().value("chat/enter_to_send", False, type=bool))
        self.enter_to_send.toggled.connect(lambda enabled: QSettings().setValue("chat/enter_to_send", enabled))
        chat_content.addWidget(self.enter_to_send)
        density_row = QFrame()
        density_row.setObjectName("SettingsRow")
        density_layout = QHBoxLayout(density_row)
        density_layout.setContentsMargins(0, 8, 0, 8)
        density_text = QVBoxLayout()
        density_heading = QLabel("消息显示密度")
        density_heading.setObjectName("MemberName")
        density_helper = QLabel("紧凑模式会减少消息之间的垂直留白。")
        density_helper.setObjectName("Muted")
        density_helper.setWordWrap(True)
        density_text.addWidget(density_heading)
        density_text.addWidget(density_helper)
        self.message_density = QComboBox()
        self.message_density.addItem("标准", "default")
        self.message_density.addItem("紧凑", "compact")
        current_density = str(QSettings().value(MESSAGE_DENSITY_KEY, "default"))
        density_index = self.message_density.findData(current_density)
        self.message_density.setCurrentIndex(max(0, density_index))
        self.message_density.currentIndexChanged.connect(self._change_density)
        density_layout.addLayout(density_text, 1)
        density_layout.addWidget(self.message_density)
        chat_content.addWidget(density_row)
        chat.addWidget(chat_section)
        chat.addStretch()

        notifications = self._add_settings_page("消息通知", "新消息通知使用系统通知能力；偏好保存在本机。")
        notification_section, notification_content = self._settings_section("桌面通知")
        self.notify_enabled = QCheckBox("后台收到新消息时显示通知")
        self.notify_enabled.setChecked(QSettings().value("notifications/enabled", False, type=bool))
        self.notify_enabled.toggled.connect(lambda enabled: QSettings().setValue("notifications/enabled", enabled))
        self.do_not_disturb = QCheckBox("免打扰（暂停新消息通知）")
        self.do_not_disturb.setChecked(QSettings().value("notifications/dnd", False, type=bool))
        self.do_not_disturb.toggled.connect(lambda enabled: QSettings().setValue("notifications/dnd", enabled))
        self.notify_sound = QCheckBox("新消息声音提醒")
        self.notify_sound.setChecked(QSettings().value("notifications/sound", False, type=bool))
        self.notify_sound.toggled.connect(lambda enabled: QSettings().setValue("notifications/sound", enabled))
        notification_content.addWidget(self.notify_enabled)
        notification_content.addWidget(self.do_not_disturb)
        notification_content.addWidget(self.notify_sound)
        self.test_notification = QPushButton("发送测试通知")
        self.test_notification.clicked.connect(self._request_test_notification)
        notification_content.addWidget(
            self.test_notification, alignment=Qt.AlignmentFlag.AlignLeft
        )
        notification_hint = QLabel(
            "测试通知会立即发送；实际新消息通知仅在应用处于后台且未开启免打扰时显示。"
        )
        notification_hint.setObjectName("Muted")
        notification_hint.setWordWrap(True)
        notification_content.addWidget(notification_hint)
        self.notification_feedback = QLabel("")
        self.notification_feedback.setObjectName("InlineNotice")
        self.notification_feedback.setWordWrap(True)
        self.notification_feedback.hide()
        notification_content.addWidget(self.notification_feedback)
        notifications.addWidget(notification_section)
        notifications.addStretch()

        ai_page = self._add_settings_page(
            "AI 助手",
            "这里管理 Agent 的全局身份；群内启用、工具边界、定时任务和共享记忆请打开群聊的“群设置”。",
        )
        ai_section, ai_content = self._settings_section("Agent 角色库")
        self.agent_setting_status = QLabel("状态未知")
        self.agent_setting_status.setObjectName("AgentBadge")
        self.agent_setting_model = QLabel("模型信息尚未加载")
        self.agent_setting_model.setObjectName("Muted")
        ai_content.addWidget(self.agent_setting_status, alignment=Qt.AlignmentFlag.AlignLeft)
        ai_content.addWidget(self.agent_setting_model)
        ai_actions = QHBoxLayout()
        self.manage_agents = QPushButton("管理 Agent 角色库")
        self.manage_memory = QPushButton("当前会话记忆")
        self.manage_memory.setObjectName("Ghost")
        ai_actions.addWidget(self.manage_agents)
        ai_actions.addWidget(self.manage_memory)
        ai_actions.addStretch()
        ai_content.addLayout(ai_actions)
        self.agent_capability = QLabel("多 Agent 与记忆服务尚未加载")
        self.agent_capability.setObjectName("Muted")
        self.agent_capability.setWordWrap(True)
        ai_content.addWidget(self.agent_capability)
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
        export_row = QFrame()
        export_row.setObjectName("SettingsRow")
        export_layout = QHBoxLayout(export_row)
        export_layout.setContentsMargins(0, 8, 0, 8)
        export_text = QVBoxLayout()
        export_heading = QLabel("导出诊断报告")
        export_heading.setObjectName("MemberName")
        export_helper = QLabel("仅包含连接状态、计数与本机偏好；不包含密码、密钥、图片或消息正文。")
        export_helper.setObjectName("Muted")
        export_helper.setWordWrap(True)
        export_text.addWidget(export_heading)
        export_text.addWidget(export_helper)
        self.export_diagnostics = QPushButton("导出诊断报告")
        self.export_diagnostics.clicked.connect(self._export_diagnostics)
        export_layout.addLayout(export_text, 1)
        export_layout.addWidget(self.export_diagnostics)
        diagnostics_content.addWidget(export_row)
        self.diagnostics_feedback = QLabel("")
        self.diagnostics_feedback.setObjectName("InlineNotice")
        self.diagnostics_feedback.setWordWrap(True)
        self.diagnostics_feedback.hide()
        diagnostics_content.addWidget(self.diagnostics_feedback)
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
        self.manage_agents.clicked.connect(self._open_agent_manager)
        self.manage_memory.clicked.connect(self._open_memory_manager)

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

    def _change_density(self, _index: int) -> None:
        value = str(self.message_density.currentData() or "default")
        QSettings().setValue(MESSAGE_DENSITY_KEY, value)
        self.density_changed.emit(value)

    @staticmethod
    def _set_feedback(label: QLabel, text: str, kind: str) -> None:
        label.setText(text)
        label.setProperty("noticeKind", kind)
        label.style().unpolish(label)
        label.style().polish(label)
        label.setVisible(bool(text))

    def _export_diagnostics(self) -> None:
        default_name = f"chatroom-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path, _selected = QFileDialog.getSaveFileName(
            self, "导出诊断报告", default_name, "JSON 文件 (*.json)"
        )
        if not path:
            return
        state = self.store.state
        manager = active_theme_manager()
        settings = QSettings()
        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "platform": sys.platform,
            "connection": {
                "server": f"{self._host}:{self._port}",
                "connected": bool(state.connected),
                "status": state.connection_text,
            },
            "account": {
                "id": state.current_user.get("id"),
                "role": str(state.current_user.get("role") or ""),
            },
            "agent": {
                "online": bool(state.agent_online),
                "model": state.agent_model,
            },
            "counts": {
                "conversations": len(state.conversations),
                "cached_messages": sum(len(records) for records in state.messages.values()),
                "activities": len(state.activities),
            },
            "preferences": {
                "theme": manager.mode.value if manager is not None else "system",
                "message_density": str(settings.value(MESSAGE_DENSITY_KEY, "default")),
                "enter_to_send": bool(settings.value("chat/enter_to_send", False, type=bool)),
                "notifications_enabled": bool(
                    settings.value("notifications/enabled", False, type=bool)
                ),
                "notifications_dnd": bool(
                    settings.value("notifications/dnd", False, type=bool)
                ),
                "notifications_sound": bool(
                    settings.value("notifications/sound", False, type=bool)
                ),
            },
            "last_error_present": bool(state.last_error),
        }
        try:
            Path(path).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self._set_feedback(self.diagnostics_feedback, f"导出失败：{exc}", "error")
            return
        self._set_feedback(
            self.diagnostics_feedback, f"诊断报告已保存到 {path}", "success"
        )

    def _request_test_notification(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._set_feedback(
                self.notification_feedback,
                "当前系统托盘不可用，无法发送桌面通知。",
                "error",
            )
            return
        self._set_feedback(
            self.notification_feedback,
            "已发送测试通知；若未看到，请检查系统通知权限。",
            "success",
        )
        self.test_notification_requested.emit()

    def _sync_theme_mode(self, mode: ThemeMode) -> None:
        index = self.theme_mode.findData(mode.value)
        if index >= 0 and index != self.theme_mode.currentIndex():
            self.theme_mode.blockSignals(True)
            self.theme_mode.setCurrentIndex(index)
            self.theme_mode.blockSignals(False)

    def _theme_snapshot_changed(self, snapshot: ThemeSnapshot) -> None:
        self._sync_theme_mode(snapshot.mode)

    def update_state(self, topic: str, state: AppState) -> None:
        if topic in {"auth", "connection", "session", "profile", "avatar", "agent", "agents", "memories", "tools", "error", "ack"}:
            role = "管理员" if state.current_user.get("role") == "admin" else "普通用户"
            self.user.setText(f"{state.current_user.get('display_name') or state.current_user.get('username', '未登录')} · {role}")
            if topic in {"auth", "profile", "session"}:
                if not self.username_edit.hasFocus():
                    self.username_edit.setText(str(state.current_user.get("username", "")))
                if not self.display_name_edit.hasFocus():
                    self.display_name_edit.setText(str(state.current_user.get("display_name") or state.current_user.get("username", "")))
            self.connection.setText(state.connection_text)
            user_id = int(state.current_user.get("id", 0) or 0)
            profile = self.service.profile(user_id, state.current_user)
            self.avatar.refresh(profile)
            self.agent_setting_status.setText("Agent 在线" if state.agent_online else "Agent 离线")
            self.agent_setting_status.setProperty("online", state.agent_online)
            self.agent_setting_status.style().unpolish(self.agent_setting_status)
            self.agent_setting_status.style().polish(self.agent_setting_status)
            self.agent_setting_model.setText(f"当前模型：{state.agent_model}" if state.agent_model else "模型信息尚未加载")
            enabled_agents = sum(1 for item in state.agents if item.get("enabled", True))
            memory_scopes = sum(1 for value in state.memory_settings.values() if value)
            self.agent_capability.setText(
                f"已启用 {enabled_agents} 个 Agent，{len(state.tool_definitions)} 个工具定义；"
                f"当前客户端已加载 {memory_scopes} 个已开启的记忆范围。"
            )
        if topic == "avatar":
            self._profile_action_pending = False
            self.profile_feedback.setText("头像已更新")
            self.profile_feedback.setProperty("noticeKind", "success")
            self.profile_feedback.style().unpolish(self.profile_feedback)
            self.profile_feedback.style().polish(self.profile_feedback)
            self.profile_feedback.show()
        elif topic == "ack" and self._profile_action_pending:
            self.profile_feedback.setText("资料已提交")
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

    def _open_agent_manager(self) -> None:
        dialog = AgentManagerDialog(self.service, self.store, self)
        dialog.exec()

    def _open_memory_manager(self) -> None:
        if not self.store.state.current_conversation:
            QMessageBox.information(self, "尚未选择会话", "请先在聊天页选择一个群聊或私聊，再管理该会话的记忆。")
            return
        dialog = MemoryDialog(self.service, self.store, self)
        dialog.exec()

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

    def _save_identity(self) -> None:
        username = self.username_edit.text().strip()
        display_name = self.display_name_edit.text().strip()
        if not 3 <= len(username) <= 20 or not 1 <= len(display_name) <= 30:
            self.profile_feedback.setText("账号需为 3–20 字符，昵称需为 1–30 字符")
            self.profile_feedback.show()
            return
        self._profile_action_pending = True
        self.profile_feedback.setText("正在保存账号和昵称…")
        self.profile_feedback.show()
        if not self.service.set_identity(username, display_name):
            self._profile_action_pending = False
            self.profile_feedback.setText(self.store.state.last_error)

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
        self.setIconSize(QSize(21, 21))
        self.setFixedSize(64, 60)
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
        self.setIcon(lucide_icon(self.icon_name, 21))

    def set_badge(self, count: int) -> None:
        count = max(0, int(count))
        self.badge.setText("99+" if count > 99 else str(count))
        self.badge.setVisible(count > 0)
        self.setAccessibleDescription(f"{count} 条未读消息" if count else "")
        self.badge.adjustSize()
        self._position_badge()

    def _position_badge(self) -> None:
        self.badge.move(self.width() - self.badge.width() - 4, 3)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._position_badge()


class Workspace(QWidget):
    logout_requested = Signal()
    notification_test_requested = Signal()
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
        self.rail.setFixedWidth(80)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(8, 12, 8, 12)
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
        self.chat.draft_preview_changed.connect(self.conversations.set_draft)
        self.chat.drafts_loaded.connect(self.conversations.set_drafts)
        self.chat.publish_drafts()
        self.chat.conversation_list_requested.connect(self._show_compact_conversations)
        self.contacts.conversation_requested.connect(self._open_from_contacts)
        self.ai.result_requested.connect(self._open_ai_result)
        self.settings.logout_requested.connect(self.logout_requested)
        self.settings.density_changed.connect(self.chat.set_density)
        self.settings.test_notification_requested.connect(self.notification_test_requested)

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if 0 <= index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)
        if index == 0:
            self._apply_chat_layout()
        elif index == 1:
            self.contacts.update_state("contacts", self.store.state)

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
        requested = self.service.open_conversation(key)
        self.chat.set_conversation(key, title)
        self.chat.set_history_loading(key, requested)
        self._compact_list_open = False
        self._apply_chat_layout()
        self.store.notify("selection")

    def flush_drafts(self) -> None:
        self.chat.flush_drafts()

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
        self.setWindowTitle("Parlor 智能聊天室")
        self.setWindowIcon(brand_app_icon(128))
        self.resize(1180, 760)
        self.setMinimumSize(720, 520)
        self.network = NetworkClient(host, port)
        self.store = AppStore()
        self.service = ChatService(self.network, self.store)
        self.credentials: tuple[str, str] | None = None
        self.was_authenticated = False
        self._read_cursors: dict[ConversationKey, int] = {}
        self._read_requests: dict[str, tuple[ConversationKey, int]] = {}
        self._tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(brand_logo_icon(24), self)
            self._tray.setToolTip("Parlor 智能聊天室")
            self._tray.show()
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
        self.workspace.notification_test_requested.connect(self._show_test_notification)
        self.workspace.chat.latest_viewed.connect(lambda: self._mark_conversation_read(self.store.state))
        self.workspace.pages.currentChanged.connect(lambda _index: QTimer.singleShot(0, lambda: self._mark_conversation_read(self.store.state)))
        self.store.subscribe(self._on_state)
        self.poll = QTimer(self)
        self.poll.setInterval(50)
        self.poll.timeout.connect(self._poll_events)
        self.poll.start()
        self._conversation_refresh_timer = QTimer(self)
        self._conversation_refresh_timer.setSingleShot(True)
        self._conversation_refresh_timer.setInterval(120)
        self._conversation_refresh_timer.timeout.connect(self._request_conversation_list)
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
        self.workspace.flush_drafts()
        self._conversation_refresh_timer.stop()
        self._read_cursors.clear()
        self._read_requests.clear()
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
            request_id = str(event.get("request_id") or "")
            if event.get("type") == "error" and request_id in self._read_requests:
                key, previous = self._read_requests.pop(request_id)
                self._read_cursors[key] = previous
            elif event.get("type") == "ack":
                self._read_requests.pop(request_id, None)
            self.store.handle_event(event)
            if event.get("type") == "message.event":
                self._notify_new_message(event.get("payload"))
            processed += 1

    def _notify_new_message(self, payload: Any) -> None:
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
            return
        message = payload["message"]
        if str(message.get("sender_id") or "") == str(self.store.state.current_user.get("id") or ""):
            return
        if not self.store.state.authenticated or self.isActiveWindow():
            return
        settings = QSettings()
        if settings.value("notifications/dnd", False, type=bool):
            return
        card_kind = message_card_kind(message)
        kind = str(message.get("message_type") or "text")
        if card_kind == "summary":
            preview = "[群聊总结]"
        elif card_kind == "todo":
            preview = "[待办事项]"
        else:
            preview = "[表情包]" if kind == "sticker" else "[图片]" if kind == "image" else "[Agent 回复]" if kind == "agent" else str(message.get("content") or "")
        if self._tray and settings.value("notifications/enabled", False, type=bool):
            self._tray.showMessage(str(message.get("sender_name") or "新消息"), preview[:120], QSystemTrayIcon.MessageIcon.Information, 4000)
        if settings.value("notifications/sound", False, type=bool):
            QApplication.beep()

    def _show_test_notification(self) -> None:
        if self._tray is None:
            return
        self._tray.showMessage(
            "测试通知",
            "如果你看到这条消息，桌面通知已经可用。",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )
        if QSettings().value("notifications/sound", False, type=bool):
            QApplication.beep()

    def _schedule_conversation_refresh(self) -> None:
        if not self.store.state.authenticated:
            return
        if not self._conversation_refresh_timer.isActive():
            self._conversation_refresh_timer.start()

    def _request_conversation_list(self) -> None:
        if self.store.state.authenticated:
            self.service.request("conversation.list")

    def _mark_conversation_read(self, state: AppState) -> None:
        key = state.current_conversation
        if (key is None or not state.authenticated or self.stack.currentWidget() is not self.workspace
                or self.workspace.pages.currentIndex() != 0 or not self.workspace.chat.isVisible()
                or not self.isActiveWindow() or not self.workspace.chat._is_at_bottom()):
            return
        ids = [
            int(item.get("id", item.get("message_id")))
            for item in state.messages.get(key, [])
            if item.get("id", item.get("message_id")) is not None
        ]
        if not ids:
            return
        latest = max(ids)
        if latest <= self._read_cursors.get(key, 0):
            return
        payload = self.service.scope(key)
        payload["message_id"] = latest
        previous = self._read_cursors.get(key, 0)
        request_id = self.service.request("history.read", payload)
        if request_id:
            self._read_cursors[key] = latest
            self._read_requests[request_id] = (key, previous)

    def _on_state(self, topic: str, state: AppState) -> None:
        if topic == "profile" and self.credentials and state.current_user.get("username"):
            self.credentials = (str(state.current_user["username"]), self.credentials[1])
        self.auth.update_state(topic, state)
        self.workspace.update_state(topic, state)
        if topic == "auth" and state.authenticated:
            self.was_authenticated = True
            self.stack.setCurrentWidget(self.workspace)
            self.service.bootstrap_workspace()
            self.service.reconcile_private_sends()
            QTimer.singleShot(0, lambda: self._mark_conversation_read(self.store.state))
            if state.events_has_more:
                self.service.sync_events()
        elif topic == "events" and state.events_has_more:
            self.service.sync_events()
        elif topic == "connection" and state.connected and self.was_authenticated and self.credentials and not state.authenticated:
            self.service.login(*self.credentials)
        elif topic == "connection" and not state.connected:
            self._read_cursors.clear()
            self._read_requests.clear()
        elif topic == "error" and state.last_error:
            self.statusBar().showMessage(state.last_error, 5000)
        if topic == "agent_result" and state.authenticated:
            self.service.request("agent.activity.list", {"limit": 100})
            self._schedule_conversation_refresh()
        if topic == "profile" and state.authenticated:
            self._schedule_conversation_refresh()
        if topic in {"messages", "selection"} and state.authenticated:
            self._schedule_conversation_refresh()
        if topic == "read":
            self._schedule_conversation_refresh()
        if topic == "private_read":
            self._schedule_conversation_refresh()

    def changeEvent(self, event: Any) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and hasattr(self, "workspace"):
            if self.isActiveWindow():
                QTimer.singleShot(0, lambda: self._mark_conversation_read(self.store.state))
            else:
                self.workspace.chat._stop_private_typing()

    def closeEvent(self, event: Any) -> None:
        self._conversation_refresh_timer.stop()
        self.workspace.flush_drafts()
        self.network.close()
        super().closeEvent(event)


def run(host: str, port: int) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("Chatroom")
    app.setApplicationName("Parlor 智能聊天室")
    app.setWindowIcon(brand_app_icon(256))
    install_theme_manager(app)
    window = MainWindow(host, port)
    window.show()
    raise SystemExit(app.exec())

