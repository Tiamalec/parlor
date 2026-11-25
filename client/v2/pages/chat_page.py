"""Chat conversation page."""

from __future__ import annotations

import base64
import mimetypes
import os
import time
from pathlib import Path
from typing import Any
from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QSettings, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import QAbstractItemView, QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QToolTip, QVBoxLayout, QWidget
from client.models import AppState, ConversationKey, ConversationSummary
from client.service import ChatService
from client.store import AppStore
from client.drafts import Draft, DraftStore
from ..icons import lucide_icon, lucide_icon_with_color
from ..agent_cards import TodoCard
from ..time_text import format_message_time
from ..theme import active_theme_manager, current_tokens
from ..details import ConversationDetailsPanel
from ..profile_widgets import ProfilePopover, build_profile_menu
from ..stickers import LocalStickerLibrary, MAX_STICKER_SIZE, StickerPanel
from ..widgets import Avatar, BrandLockup, ConversationCard, EmptyState, MentionTextEdit, MessageBubble, UserAvatar, avatar_pixmap, configure_scroll_surface, configure_surface, message_card_kind
from ..ui_constants import BOTTOM_DISTANCE_PX, DRAFT_DEBOUNCE_MS, MAX_IMAGE_SIZE, MESSAGE_DENSITY_COLUMN_SPACING, MESSAGE_DENSITY_KEY, MESSAGE_DENSITY_MARGINS, MESSAGE_DENSITY_SPACING, SMOOTH_SCROLL_MS
from ..ui_helpers import _message_preview, _message_timestamp, _messages_are_grouped, clear_layout
from .agent_manager import AgentManagerDialog, MemoryDialog


class ConversationPane(QFrame):
    selected = Signal(object)

    def __init__(
        self,
        service: ChatService | None = None,
        store: AppStore | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.store = store
        self.setObjectName("ConversationPane")
        self.setMinimumWidth(300)
        self.setMaximumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 16)
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
            self._apply_summary_avatar(card, summary)
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

    def _apply_summary_avatar(
        self, card: ConversationCard, summary: ConversationSummary
    ) -> None:
        if summary.key.kind == "group":
            card.set_avatar(summary.title, group=True)
            return
        if self.service is None or self.store is None:
            card.set_avatar(summary.title, group=False)
            return
        peer_id = summary.key.peer_user_id(
            self.store.state.current_user.get("id")
        )
        if peer_id is None:
            card.set_avatar(summary.title, group=False)
            return
        profile = self.service.profile(
            peer_id,
            {
                "kind": "user",
                "username": summary.title,
                "display_name": summary.title,
            },
        )
        card.set_avatar(
            profile.display_name or summary.title,
            self.service.avatar_data(profile),
            group=False,
        )


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
    todo_open_requested = Signal(int)
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
        self._todo_import_requests: dict[str, int] = {}
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
        main.setObjectName("ChatSurface")
        layout = QVBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget()
        header.setObjectName("ChatHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(20, 14, 16, 14)
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
        self.search_button.setIcon(lucide_icon("search", 16))
        self.search_button.setToolTip("搜索当前会话")
        self.details_button = QPushButton("详情")
        self.details_button.setObjectName("Ghost")
        self.details_button.setIcon(lucide_icon("panel-right", 16))
        self.details_button.setToolTip("查看会话详情")
        self._header_icon_buttons = (
            (self.conversation_list_button, "arrow-left"),
            (self.search_button, "search"),
            (self.details_button, "panel-right"),
        )
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
        # The scroll widget must grow to the rows' minimum height before Qt
        # lays out a newly selected conversation. Otherwise a stale viewport
        # height can squeeze every message into a thin clipped strip.
        self.messages.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
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
            manager.themeChanged.connect(self._refresh_header_icons)
        self._refresh_new_messages_badge_icon()
        self._refresh_header_icons()
        composer = QWidget()
        composer.setObjectName("Composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(18, 10, 18, 14)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.emoji = QPushButton("表情")
        self.emoji.setIcon(lucide_icon("smile", 16))
        self.sticker = QPushButton("表情包")
        self.sticker.setIcon(lucide_icon("sparkles", 16))
        self.sticker.setAccessibleName("打开表情包面板")
        self.image = QPushButton("图片")
        self.image.setIcon(lucide_icon("image", 16))
        self.ai = QPushButton("AI")
        self.ai.setIcon(lucide_icon("bot", 16))
        for button in (self.emoji, self.sticker, self.image, self.ai):
            button.setObjectName("ComposerTool")
            toolbar.addWidget(button)
        self._header_icon_buttons += (
            (self.emoji, "smile"),
            (self.sticker, "sparkles"),
            (self.image, "image"),
            (self.ai, "bot"),
        )
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
        if message_card_kind(displayed) == "todo" and isinstance(displayed.get("card"), dict):
            current = self.store.state.current_user
            current_id = str(current.get("id") or "")
            identity = next(
                (
                    item
                    for item in self.store.state.members_by_group.get(self.key.identifier, [])
                    if str(item.get("id") or "") == current_id
                ),
                {},
            )
            current_name = (
                identity.get("nickname")
                or identity.get("display_name")
                or current.get("display_name")
                or current.get("username")
                or ""
            )
            displayed = {
                **displayed,
                "card": {
                    **displayed["card"],
                    "current_user_id": current.get("id"),
                    "current_user_name": current_name,
                    "requester_id": current.get("id"),
                    "requester_name": current_name,
                },
            }
        bubble = MessageBubble(
            displayed, mine=mine, can_recall=can_recall, density=self._density
        )
        bubble.recall_requested.connect(self.service.recall)
        bubble.reply_requested.connect(self._reply_to)
        bubble.edit_requested.connect(self._edit_message)
        bubble.quote_requested.connect(self._focus_quoted_message)
        bubble.media_requested.connect(self.service.fetch_media)
        bubble.sources_requested.connect(self._focus_sources)
        bubble.todo_import_requested.connect(self._import_todo_tasks)
        bubble.todo_open_requested.connect(self.todo_open_requested.emit)
        todo_card = getattr(bubble, "agent_card", None)
        if isinstance(todo_card, TodoCard):
            message_id = int(message.get("id", 0) or 0)
            todo_card.set_runtime_context(
                message_id,
                self.store.state.todos,
                self.store.state.todo_import_results.get(message_id),
            )
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
        footer_text = format_message_time(stamp) + (
            " · 已编辑" if message.get("edited") else ""
        )
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
            row_layout.addStretch()
        row.setMinimumHeight(row.sizeHint().height())
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
        self.messages.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty = EmptyState(
            title,
            description,
            mark=BrandLockup(),
            body_width=280,
        )
        self.messages.addWidget(empty, alignment=Qt.AlignmentFlag.AlignCenter)

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
                self.messages.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self.messages.setAlignment(Qt.AlignmentFlag.AlignBottom)
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
            lucide_icon_with_color("arrow-down", current_tokens().on_primary, 14)
        )

    def _refresh_header_icons(self, *_args: object) -> None:
        for button, icon_name in getattr(self, "_header_icon_buttons", ()):
            button.setIcon(lucide_icon(icon_name, 16))

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

    def _import_todo_tasks(self, message_id: int, task_indices: list[int]) -> None:
        request_id = self.service.import_todo_tasks(message_id, task_indices)
        if request_id:
            self._todo_import_requests[request_id] = message_id
        self.store.state.todo_import_results[message_id] = {
            "status": "pending" if request_id else "failed",
            "request_id": request_id or "",
            "error": "" if request_id else "待办请求发送失败",
        }
        self.store.notify("todo_import")

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
        if topic == "todo_import":
            self._todo_import_requests.clear()
        elif topic == "todos" and state.todo_error and self._todo_import_requests:
            for message_id in self._todo_import_requests.values():
                state.todo_import_results[message_id] = {
                    "status": "failed",
                    "error": state.todo_error,
                }
            self._todo_import_requests.clear()
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
        elif topic in {"todos", "todo_import"}:
            self._schedule_render(count_new=False, force=True)
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
                compact = self.width() < 820
                parts.append(f"{member_count} 人" if compact else f"{member_count} 位成员")
            agent_count = len(
                [
                    item for item in self.store.state.members_by_group.get(self.key.identifier, [])
                    if item.get("kind") == "agent"
                ]
            )
            if agent_count and self.width() >= 820:
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
        self._refresh_subtitle()
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
