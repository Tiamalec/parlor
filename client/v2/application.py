"""Compatibility facade for the V2 client UI.

Implementations live in ``client.v2.pages`` and ``client.v2.shell``. Existing
imports from this module keep working while callers migrate to the new modules.
"""

from __future__ import annotations

from .ui_constants import (
    BOTTOM_DISTANCE_PX,
    DRAFT_DEBOUNCE_MS,
    MAX_AVATAR_SIZE,
    MAX_IMAGE_SIZE,
    MESSAGE_DENSITY_COLUMN_SPACING,
    MESSAGE_DENSITY_KEY,
    MESSAGE_DENSITY_MARGINS,
    MESSAGE_DENSITY_SPACING,
    SMOOTH_SCROLL_MS,
)
from .ui_helpers import (
    _message_preview,
    _message_sender_key,
    _message_timestamp,
    _messages_are_grouped,
    clear_layout,
)
from .pages.admin_page import AdminPage
from .pages.agent_manager import AgentManagerDialog, MemoryDialog
from .pages.ai_center_page import (
    TASK_CENTER_TYPES,
    TASK_STATUS_LABELS,
    TASK_TYPE_LABELS,
    AICenterPage,
    AgentTaskCard,
    AgentTaskDetailsDialog,
    NewAgentTaskDialog,
    activity_time,
    task_error_text,
)
from .pages.auth_page import AuthPage
from .pages.chat_page import ChatPane, ConversationPane, DetailsScrim
from .pages.contacts_page import ContactsPage, TextPrompt
from .pages.settings_page import SettingsPage
from .shell.main_window import MainWindow, run
from .shell.workspace import NavigationButton, Workspace

__all__ = [
    "AICenterPage",
    "AdminPage",
    "AgentManagerDialog",
    "AgentTaskCard",
    "AgentTaskDetailsDialog",
    "AuthPage",
    "ChatPane",
    "ContactsPage",
    "ConversationPane",
    "DetailsScrim",
    "MainWindow",
    "MemoryDialog",
    "NavigationButton",
    "NewAgentTaskDialog",
    "SettingsPage",
    "TextPrompt",
    "Workspace",
    "run",
]
