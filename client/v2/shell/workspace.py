"""Workspace shell and navigation rail."""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget
from client.models import AppState, ConversationKey
from client.service import ChatService
from client.store import AppStore
from ..icons import NAVIGATION_ICONS, brand_logo_icon, lucide_icon
from ..personal_todo import PersonalTodoPage
from ..theme import METRICS, active_theme_manager
from ..widgets import Avatar, configure_surface
from ..pages.admin_page import AdminPage
from ..pages.ai_center_page import AICenterPage
from ..pages.chat_page import ChatPane, ConversationPane
from ..pages.contacts_page import ContactsPage
from ..pages.settings_page import SettingsPage


class NavigationButton(QToolButton):
    """Sidebar item with a themeable icon, label and optional unread badge."""

    def __init__(self, label: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.setObjectName("NavItem")
        self.setText(label)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(18, 18))
        self.setMinimumHeight(METRICS.nav_row)
        self.setMaximumHeight(METRICS.nav_row)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._compact = False
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
        self.setIcon(lucide_icon(self.icon_name, 18 if not self._compact else 20))

    def set_compact(self, compact: bool, width: int = 56) -> None:
        self._compact = compact
        self.setProperty("compact", "true" if compact else "false")
        if compact:
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            self.setFixedSize(width, 56)
            self.setIconSize(QSize(20, 20))
        else:
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.setMinimumWidth(0)
            self.setMaximumWidth(16_777_215)
            self.setMinimumHeight(METRICS.nav_row)
            self.setMaximumHeight(METRICS.nav_row)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setIconSize(QSize(18, 18))
        self.style().unpolish(self)
        self.style().polish(self)
        self.refresh_icon()
        self._position_badge()

    def set_badge(self, count: int) -> None:
        count = max(0, int(count))
        self.badge.setText("99+" if count > 99 else str(count))
        self.badge.setVisible(count > 0)
        self.setAccessibleDescription(f"{count} 条未读消息" if count else "")
        self.badge.adjustSize()
        self._position_badge()

    def _position_badge(self) -> None:
        right = 4 if self._compact else 10
        top = 3 if self._compact else (self.height() - self.badge.height()) // 2
        self.badge.move(self.width() - self.badge.width() - right, top)

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
        self._pending_todo_id: int | None = None
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.rail = self.sidebar
        self.sidebar.setFixedWidth(METRICS.sidebar_wide)
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(12, 16, 12, 12)
        self.sidebar_layout.setSpacing(4)
        self.brand_row = QWidget()
        self.brand_row.setObjectName("SidebarBrand")
        brand_layout = QHBoxLayout(self.brand_row)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(10)
        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(32, 32)
        self.brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_icon.setAccessibleName("Parlor")
        brand_names = QVBoxLayout()
        brand_names.setContentsMargins(0, 0, 0, 0)
        brand_names.setSpacing(0)
        self.sidebar_wordmark = QLabel("Parlor")
        self.sidebar_wordmark.setObjectName("SidebarWordmark")
        self.sidebar_meta = QLabel("智能聊天室")
        self.sidebar_meta.setObjectName("SidebarMeta")
        brand_names.addWidget(self.sidebar_wordmark)
        brand_names.addWidget(self.sidebar_meta)
        brand_layout.addWidget(self.brand_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_layout.addLayout(brand_names, 1)
        self.sidebar_layout.addWidget(self.brand_row)
        self.sidebar_layout.addSpacing(18)
        self.sidebar_section = QLabel("工作台")
        self.sidebar_section.setObjectName("SidebarSection")
        self.sidebar_layout.addWidget(self.sidebar_section)
        self.sidebar_layout.addSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[NavigationButton] = []
        navigation = (
            ("messages", "消息"),
            ("contacts", "通讯"),
            ("ai", "AI 任务"),
            ("todos", "待办"),
            ("admin", "管理"),
        )
        for index, (icon_key, label) in enumerate(navigation):
            button = NavigationButton(label, NAVIGATION_ICONS[icon_key], self.sidebar)
            button.clicked.connect(lambda _checked=False, page=index: self._navigate(page))
            self.nav_group.addButton(button)
            self.nav_buttons.append(button)
            self.sidebar_layout.addWidget(button)
        self.sidebar_layout.addStretch(1)
        self.user_card = QFrame()
        self.user_card.setObjectName("SidebarUser")
        user_layout = QHBoxLayout(self.user_card)
        user_layout.setContentsMargins(8, 7, 8, 7)
        user_layout.setSpacing(9)
        self.user_avatar = Avatar("", 32)
        user_names = QVBoxLayout()
        user_names.setContentsMargins(0, 0, 0, 0)
        user_names.setSpacing(0)
        self.user_name = QLabel("未登录")
        self.user_name.setObjectName("SidebarUserName")
        self.user_role = QLabel("")
        self.user_role.setObjectName("SidebarUserRole")
        user_names.addWidget(self.user_name)
        user_names.addWidget(self.user_role)
        user_layout.addWidget(self.user_avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        user_layout.addLayout(user_names, 1)
        self.sidebar_layout.addWidget(self.user_card)
        self.settings_button = NavigationButton("设置", NAVIGATION_ICONS["settings"], self.sidebar)
        self.settings_button.clicked.connect(lambda _checked=False: self._navigate(5))
        self.nav_group.addButton(self.settings_button)
        self.nav_buttons.append(self.settings_button)
        self.sidebar_layout.addWidget(self.settings_button)
        self.nav_buttons[0].setChecked(True)
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(self._refresh_navigation_icons)
        self.pages = QStackedWidget()
        self.chat_root = QWidget()
        configure_surface(self.chat_root, "canvas")
        self.chat_layout = QHBoxLayout(self.chat_root)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(0)
        self.conversations = ConversationPane(service, store)
        self.chat = ChatPane(service, store)
        self.chat_layout.addWidget(self.conversations)
        self.chat_layout.addWidget(self.chat, 1)
        self.contacts = ContactsPage(service)
        self.ai = AICenterPage(service, store)
        self.admin = AdminPage()
        self.todos = PersonalTodoPage(service)
        self.settings = SettingsPage(service, store, host, port)
        for page in (self.chat_root, self.contacts, self.ai, self.todos, self.admin, self.settings):
            self.pages.addWidget(page)
        root.addWidget(self.sidebar)
        root.addWidget(self.pages, 1)
        self.conversations.selected.connect(self._select_conversation)
        self.chat.draft_preview_changed.connect(self.conversations.set_draft)
        self.chat.drafts_loaded.connect(self.conversations.set_drafts)
        self.chat.publish_drafts()
        self.chat.conversation_list_requested.connect(self._show_compact_conversations)
        self.chat.todo_open_requested.connect(self._open_todo)
        self.contacts.conversation_requested.connect(self._open_from_contacts)
        self.ai.result_requested.connect(self._open_ai_result)
        self.settings.logout_requested.connect(self.logout_requested)
        self.settings.density_changed.connect(self.chat.set_density)
        self.settings.test_notification_requested.connect(self.notification_test_requested)
        self._apply_sidebar_mode(self.width())
        self.pages.setFocus()

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if 0 <= index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)
        if index == 0:
            self._apply_chat_layout()
        elif index == 1:
            self.contacts.update_state("contacts", self.store.state)
        elif index == 3 and self.store.state.authenticated and not self.store.state.todos_loaded:
            if not any(action == "todo.list" for action in self.store.state.pending.values()):
                self.service.list_todos()

    def _refresh_navigation_icons(self, *_args: object) -> None:
        self.brand_icon.setPixmap(brand_logo_icon(30).pixmap(30, 30))
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
            self.conversations.setMinimumWidth(300)
            self.conversations.setMaximumWidth(360)
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

    def _open_todo(self, todo_id: int) -> None:
        self._pending_todo_id = int(todo_id)
        self.todos.update_state("todos", self.store.state)
        self._navigate(3)
        if self.todos.select_todo(todo_id) or self.store.state.todos_loaded:
            self._pending_todo_id = None

    def update_state(self, topic: str, state: AppState) -> None:
        self.nav_buttons[4].setVisible(state.current_user.get("role") == "admin")
        self.nav_buttons[0].set_badge(sum(max(0, item.unread_count) for item in state.conversations))
        if topic in {"auth", "profile", "avatar", "users"}:
            user = state.current_user or {}
            display = str(user.get("display_name") or user.get("username") or "未登录")
            self.user_name.setText(display)
            role = str(user.get("role") or "")
            self.user_role.setText("管理员" if role == "admin" else "成员" if role else "等待登录")
            self.user_avatar.set_avatar(display if display != "未登录" else "?", user.get("avatar_data"))
        if topic in {"auth", "conversations", "profile", "avatar", "users"}:
            self.conversations.set_conversations(state.conversations)
            self.conversations.set_selected(state.current_conversation)
        for page in (self.chat, self.contacts, self.ai, self.admin, self.settings):
            page.update_state(topic, state)
        self.todos.update_state(topic, state)
        if topic == "todos" and self._pending_todo_id is not None:
            if self.todos.select_todo(self._pending_todo_id):
                self._pending_todo_id = None
        if topic == "todo_target" and state.todo_target:
            target = state.todo_target
            key = ConversationKey(str(target["conversation_type"]), str(target["conversation_id"]))
            self._open_ai_result(key, int(target["message_id"]))
            state.todo_target = None

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_sidebar_mode(event.size().width())
        self._apply_chat_layout()

    def _apply_sidebar_mode(self, width: int) -> None:
        if width >= METRICS.sidebar_label_breakpoint:
            mode, target = "wide", METRICS.sidebar_wide
        elif width >= METRICS.sidebar_medium_breakpoint:
            mode, target = "medium", METRICS.sidebar_medium
        else:
            mode, target = "compact", METRICS.sidebar_compact
        if self.sidebar.width() != target:
            self.sidebar.setFixedWidth(target)
        wide = mode == "wide"
        self.sidebar_layout.setContentsMargins(12 if wide else 6, 16, 12 if wide else 6, 12)
        self.sidebar_wordmark.setVisible(wide)
        self.sidebar_meta.setVisible(wide)
        self.sidebar_section.setVisible(wide)
        self.user_card.setVisible(wide)
        self.brand_icon.setFixedSize(32 if wide else 30, 32 if wide else 30)
        if wide:
            self._set_brand_alignment(Qt.AlignmentFlag.AlignLeft)
        else:
            self._set_brand_alignment(Qt.AlignmentFlag.AlignCenter)
        button_width = max(40, target - (24 if wide else 12))
        for button in self.nav_buttons:
            button.set_compact(not wide, button_width)
        self._refresh_navigation_icons()

    def _set_brand_alignment(self, alignment: Qt.AlignmentFlag) -> None:
        layout = self.brand_row.layout()
        if layout is not None:
            layout.setAlignment(self.brand_icon, alignment)
