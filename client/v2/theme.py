"""Semantic Light/Dark/System theme infrastructure for the V2 Qt client."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Callable

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


class ThemeMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


class ThemeVariant(str, Enum):
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    canvas: str
    surface: str
    surface_subtle: str
    surface_elevated: str
    text_primary: str
    text_secondary: str
    text_tertiary: str
    text_disabled: str
    border: str
    border_strong: str
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_subtle: str
    agent: str
    agent_subtle: str
    success: str
    warning: str
    danger: str
    overlay_scrim: str
    scrollbar: str
    message_incoming: str
    message_outgoing: str
    message_agent: str
    message_agent_border: str
    mention_user_bg: str
    mention_user_fg: str
    mention_agent_bg: str
    mention_agent_fg: str
    code_bg: str
    code_fg: str
    avatar_user: str
    avatar_text: str
    switch_off: str
    switch_disabled: str
    switch_knob: str

    def as_dict(self) -> dict[str, str]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


LIGHT_TOKENS = ThemeTokens(
    canvas="#F5F7FA",
    surface="#FFFFFF",
    surface_subtle="#F8FAFC",
    surface_elevated="#FFFFFF",
    text_primary="#172033",
    text_secondary="#566176",
    text_tertiary="#667085",
    text_disabled="#98A2B3",
    border="#DCE2EA",
    border_strong="#C5CEDA",
    primary="#356AE6",
    primary_hover="#2C5CD0",
    primary_pressed="#244EAF",
    primary_subtle="#EAF0FF",
    agent="#6B4FD3",
    agent_subtle="#F1ECFF",
    success="#21865B",
    warning="#B8660B",
    danger="#C43D45",
    overlay_scrim="rgba(15, 23, 42, 0.42)",
    scrollbar="#C0C8D4",
    message_incoming="#FFFFFF",
    message_outgoing="#E8F0FF",
    message_agent="#F4F0FF",
    message_agent_border="#DDD3FF",
    mention_user_bg="#EDF3FF",
    mention_user_fg="#2F5FD0",
    mention_agent_bg="#F1ECFF",
    mention_agent_fg="#6848C7",
    code_bg="#111827",
    code_fg="#E5E7EB",
    avatar_user="#356AE6",
    avatar_text="#FFFFFF",
    switch_off="#C5CEDA",
    switch_disabled="#DCE2EA",
    switch_knob="#FFFFFF",
)


DARK_TOKENS = ThemeTokens(
    canvas="#0F141C",
    surface="#151B24",
    surface_subtle="#1A2230",
    surface_elevated="#202938",
    text_primary="#F3F6FA",
    text_secondary="#B7C0CE",
    text_tertiary="#98A5B7",
    text_disabled="#687486",
    border="#2D3746",
    border_strong="#3E4A5C",
    primary="#7EA2FF",
    primary_hover="#96B3FF",
    primary_pressed="#668FF7",
    primary_subtle="#1D2B4F",
    agent="#B59CFF",
    agent_subtle="#2A2247",
    success="#58C996",
    warning="#F2B45D",
    danger="#FF7A7A",
    overlay_scrim="rgba(0, 0, 0, 0.58)",
    scrollbar="#465266",
    message_incoming="#1A2230",
    message_outgoing="#20345D",
    message_agent="#29213F",
    message_agent_border="#493A70",
    mention_user_bg="#20345D",
    mention_user_fg="#AFC5FF",
    mention_agent_bg="#2A2247",
    mention_agent_fg="#C4B5FD",
    code_bg="#0B0F15",
    code_fg="#E6EBF2",
    avatar_user="#668FF7",
    avatar_text="#FFFFFF",
    switch_off="#465266",
    switch_disabled="#2D3746",
    switch_knob="#F3F6FA",
)


TOKENS_BY_VARIANT = {ThemeVariant.LIGHT: LIGHT_TOKENS, ThemeVariant.DARK: DARK_TOKENS}


@dataclass(frozen=True, slots=True)
class ThemeSnapshot:
    mode: ThemeMode
    variant: ThemeVariant
    tokens: ThemeTokens
    revision: int


def build_palette(tokens: ThemeTokens) -> QPalette:
    palette = QPalette()
    mappings = {
        QPalette.ColorRole.Window: tokens.canvas,
        QPalette.ColorRole.WindowText: tokens.text_primary,
        QPalette.ColorRole.Base: tokens.surface,
        QPalette.ColorRole.AlternateBase: tokens.surface_subtle,
        QPalette.ColorRole.Text: tokens.text_primary,
        QPalette.ColorRole.Button: tokens.surface,
        QPalette.ColorRole.ButtonText: tokens.text_primary,
        QPalette.ColorRole.Highlight: tokens.primary,
        QPalette.ColorRole.HighlightedText: tokens.surface,
        QPalette.ColorRole.ToolTipBase: tokens.surface_elevated,
        QPalette.ColorRole.ToolTipText: tokens.text_primary,
        QPalette.ColorRole.PlaceholderText: tokens.text_disabled,
        QPalette.ColorRole.Link: tokens.primary,
        QPalette.ColorRole.LinkVisited: tokens.agent,
    }
    for role, value in mappings.items():
        palette.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(tokens.text_disabled))
    return palette


def build_stylesheet(c: ThemeTokens) -> str:
    return f"""
    * {{ font-size: 14px; color: {c.text_primary}; }}
    QMainWindow, QWidget#AppRoot, QWidget[surfaceRole="canvas"] {{ background: {c.canvas}; }}
    QWidget[surfaceRole="surface"] {{ background: {c.surface}; }}
    QWidget[surfaceRole="subtle"] {{ background: {c.surface_subtle}; }}
    QWidget[surfaceRole="elevated"] {{ background: {c.surface_elevated}; }}
    QFrame#Surface, QWidget#Surface {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; }}
    QFrame#DetailsPanel {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; }}
    QFrame#DetailsOverview {{ background: {c.surface_subtle}; border: none; border-radius: 10px; }}
    QFrame#MemberRow {{ background: transparent; border: none; border-radius: 8px; }}
    QFrame#MemberRow:hover, QFrame#ConversationCard:hover {{ background: {c.surface_subtle}; }}
    QFrame#ConversationCard {{ background: transparent; border: none; border-radius: 8px; }}
    QFrame#ConversationCard[selected="true"] {{ background: {c.primary_subtle}; }}
    QFrame#ProfileCard {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 12px; }}
    QFrame#ProfilePane {{ background: transparent; border: none; }}
    QFrame#ProfilePopover {{ background: {c.surface_elevated}; border: 1px solid {c.border_strong}; border-radius: 12px; }}
    QScrollArea#ProfileCardScroll, QWidget#ProfileCardViewport {{ background: transparent; border: none; }}
    QFrame#TaskRow {{ background: transparent; border: none; border-bottom: 1px solid {c.border}; border-radius: 0; }}
    QFrame#TaskRow:hover {{ background: {c.surface_subtle}; }}
    QFrame#SettingsSection {{ background: transparent; border: none; border-bottom: 1px solid {c.border}; }}
    QFrame#SettingsRow {{ background: transparent; border: none; }}
    QFrame#Rail {{ background: {c.surface}; border: none; border-right: 1px solid {c.border}; }}
    QFrame#DetailsScrim {{ background: {c.overlay_scrim}; border: none; }}
    QWidget#ChatHeader {{ background: {c.surface}; border-bottom: 1px solid {c.border}; }}
    QWidget#Composer {{ background: {c.surface}; border-top: 1px solid {c.border}; }}
    QStackedWidget#ContactDetails {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; }}
    QStackedWidget#SettingsContent {{ background: transparent; border: none; }}
    QLabel#PageTitle {{ font-size: 20px; font-weight: 700; }}
    QLabel#SectionTitle {{ font-size: 16px; font-weight: 600; }}
    QLabel#ProfileName {{ font-size: 20px; font-weight: 700; }}
    QLabel#MetricValue {{ font-size: 26px; font-weight: 700; }}
    QLabel#EmptyStateIcon {{ font-size: 42px; }}
    QLabel#EmptyStateTitle {{ font-size: 18px; font-weight: 700; }}
    QLabel#AuthBrandTile {{ background: {c.primary_subtle}; border: 1px solid {c.border}; border-radius: 12px; }}
    QLabel#AuthWordmark {{ font-size: 16px; font-weight: 700; }}
    QLabel#AuthWordmarkMeta {{ color: {c.text_tertiary}; font-size: 12px; }}
    QLabel#AuthHeroTitle {{ font-size: 24px; font-weight: 700; }}
    QLabel#AuthHeroText {{ color: {c.text_secondary}; font-size: 14px; }}
    QLabel#AuthFeatureText {{ color: {c.text_secondary}; }}
    QLabel#AuthHeading {{ font-size: 20px; font-weight: 700; }}
    QFrame#AuthModeTrack {{ background: {c.surface_subtle}; border: 1px solid {c.border}; border-radius: 8px; }}
    QPushButton#AuthModeTab {{ min-height: 30px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: {c.text_secondary}; }}
    QPushButton#AuthModeTab:hover {{ color: {c.text_primary}; background: {c.surface}; }}
    QPushButton#AuthModeTab:checked {{ background: {c.surface_elevated}; border-color: {c.border_strong}; color: {c.primary}; font-weight: 600; }}
    QPushButton#AuthModeTab:focus {{ border: 2px solid {c.primary}; }}
    QPushButton#AuthModeTab:disabled {{ color: {c.text_disabled}; }}
    QLabel#AuthFieldLabel {{ color: {c.text_secondary}; font-size: 12px; font-weight: 600; }}
    QLineEdit#AuthInput {{ min-height: 22px; padding: 9px 10px; }}
    QLabel#AuthStatusDot {{ min-width: 8px; max-width: 8px; min-height: 8px; max-height: 8px; border-radius: 4px; background: {c.text_disabled}; }}
    QLabel#AuthStatusDot[connectionState="online"] {{ background: {c.success}; }}
    QLabel#AuthStatusDot[connectionState="pending"] {{ background: {c.warning}; }}
    QLabel#AuthStatusDot[connectionState="offline"] {{ background: {c.danger}; }}
    QLabel#AuthStatusText {{ color: {c.text_secondary}; font-size: 12px; }}
    QLabel#AuthServer {{ color: {c.text_tertiary}; font-size: 11px; }}
    QLabel#NavBadge {{ color: {c.surface}; background: {c.danger}; border: none; border-radius: 7px; min-width: 14px; min-height: 14px; padding: 0 3px; font-size: 9px; font-weight: 700; }}
    QLabel#TaskTitle {{ font-weight: 600; }}
    QLabel#TaskStatus {{ color: {c.text_secondary}; background: {c.surface_subtle}; border-radius: 6px; padding: 4px 8px; font-size: 12px; }}
    QLabel#TaskStatus[taskStatus="running"] {{ color: {c.warning}; }}
    QLabel#TaskStatus[taskStatus="succeeded"] {{ color: {c.success}; }}
    QLabel#TaskStatus[taskStatus="failed"] {{ color: {c.danger}; }}
    QLabel#InlineNotice {{ color: {c.text_secondary}; background: {c.surface_subtle}; border-radius: 6px; padding: 7px 9px; }}
    QLabel#InlineNotice[noticeKind="success"] {{ color: {c.success}; }}
    QLabel#InlineNotice[noticeKind="error"] {{ color: {c.danger}; }}
    QLabel#Muted, QLabel#MessageTime {{ color: {c.text_tertiary}; }}
    QLabel#DraftPreview {{ color: {c.text_secondary}; font-weight: 600; }}
    QLabel#MessageTime {{ font-size: 11px; }}
    QLabel#DirectoryStatus {{ color: {c.text_secondary}; background: {c.surface_subtle}; border-radius: 6px; padding: 6px 8px; }}
    QLabel#RecalledMessage {{ color: {c.text_tertiary}; font-style: italic; }}
    QLabel#Danger {{ color: {c.danger}; }}
    QLabel#DetailsSectionTitle {{ font-size: 13px; font-weight: 600; color: {c.text_secondary}; }}
    QLabel#MemberName, QLabel#ConversationTitle {{ font-weight: 600; }}
    QLabel#ConversationTime {{ color: {c.text_tertiary}; font-size: 12px; }}
    QLabel#UnreadBadge {{ background: {c.danger}; color: {c.surface}; border-radius: 11px; font-size: 11px; font-weight: 700; }}
    QLabel#RoleBadge {{ color: {c.text_secondary}; background: {c.surface_subtle}; border-radius: 8px; padding: 3px 7px; font-size: 12px; }}
    QLabel#AgentBadge {{ color: {c.agent}; background: {c.agent_subtle}; border-radius: 8px; padding: 3px 7px; font-size: 12px; font-weight: 600; }}
    QLabel#AgentBadge[online="false"] {{ color: {c.text_secondary}; background: {c.surface_subtle}; }}
    QLabel#MessageSender {{ color: {c.text_secondary}; font-size: 12px; font-weight: 600; }}
    QLabel#AgentMessageSender {{ color: {c.agent}; font-size: 12px; font-weight: 600; }}
    QLabel#AgentActivity {{ color: {c.text_secondary}; padding: 3px 5px; }}
    QLabel#SystemMessage {{ color: {c.text_tertiary}; background: {c.surface_subtle}; border-radius: 8px; padding: 6px 12px; }}
    QFrame#MessageBubble {{ border: 1px solid {c.border}; border-radius: 10px; }}
    QFrame#MessageBubble[messageRole="incoming"] {{ background: {c.message_incoming}; }}
    QFrame#MessageBubble[messageRole="outgoing"] {{ background: {c.message_outgoing}; }}
    QFrame#MessageBubble[messageRole="agent"] {{ background: {c.message_agent}; border-color: {c.message_agent_border}; }}
    QFrame#MessageBubble[messageRole="card"] {{ background: transparent; border: none; }}
    QFrame#MessageBubble[messageType="sticker"] {{ background: transparent; border: none; border-radius: 0; }}
    QFrame#MessageBubble QLabel#AgentMarkdownContent, QFrame#MessageBubble QLabel#MarkdownMessageContent,
    QFrame#MessageBubble QLabel#PlainMessageContent {{ color: {c.text_primary}; font-size: 14px; font-weight: 400; }}
    QLabel#MessageImage {{ color: {c.text_secondary}; background: {c.surface_subtle}; border-radius: 8px; padding: 8px; }}
    QLabel#MessageSticker {{ color: {c.text_secondary}; background: transparent; border: none; padding: 0; }}
    QFrame#AgentCard {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 12px; }}
    QFrame#AgentCard QWidget#AgentCardBody, QFrame#AgentCard QWidget#AgentCardFooter {{ background: transparent; border: none; }}
    QFrame#CardDivider {{ border: none; border-top: 1px solid {c.border}; max-height: 1px; }}
    QLabel#CardTitle {{ font-size: 15px; font-weight: 600; color: {c.text_primary}; }}
    QLabel#CardBadge {{ color: {c.agent}; background: {c.agent_subtle}; border-radius: 8px; padding: 2px 6px; font-size: 11px; font-weight: 600; }}
    QLabel#CardMeta {{ color: {c.text_tertiary}; font-size: 11px; }}
    QLabel#CardBody {{ color: {c.text_primary}; font-size: 13px; }}
    QLabel#CardSectionTitle {{ color: {c.text_secondary}; font-size: 13px; font-weight: 500; }}
    QLabel#SummaryPoint, QLabel#CardPendingPoint {{ color: {c.text_primary}; font-size: 13px; }}
    QLabel#CardPendingPoint {{ color: {c.text_secondary}; }}
    QLabel#CardBulletMark {{ color: {c.agent}; font-size: 13px; font-weight: 600; }}
    QLabel#CardEmpty {{ color: {c.text_secondary}; font-size: 12px; }}
    QFrame#CardTaskRow {{ background: transparent; border: none; border-bottom: 1px solid {c.border}; border-radius: 0; }}
    QLabel#CardTaskTitle {{ color: {c.text_primary}; font-size: 13px; font-weight: 600; }}
    QLabel#CardTaskMeta {{ color: {c.text_tertiary}; font-size: 11px; }}
    QPushButton {{ min-height: 34px; padding: 0 14px; border: 1px solid {c.border}; border-radius: 8px; background: {c.surface}; color: {c.text_primary}; }}
    QPushButton:hover {{ background: {c.surface_subtle}; border-color: {c.border_strong}; }}
    QPushButton:pressed {{ background: {c.primary_subtle}; }}
    QPushButton:focus {{ border: 2px solid {c.primary}; }}
    QPushButton:disabled {{ color: {c.text_disabled}; background: {c.surface_subtle}; }}
    QPushButton#Primary {{ color: {c.surface}; background: {c.primary}; border-color: {c.primary}; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {c.primary_hover}; }}
    QPushButton#Primary:pressed {{ background: {c.primary_pressed}; }}
    QPushButton#Primary:disabled {{ color: {c.text_disabled}; background: {c.surface_subtle}; border-color: {c.border}; }}
    QPushButton#Ghost {{ border: none; background: transparent; }}
    QPushButton#Ghost:hover {{ background: {c.primary_subtle}; }}
    QPushButton#CardAction {{ min-height: 24px; padding: 0 6px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: {c.text_secondary}; font-size: 12px; }}
    QPushButton#CardAction:hover {{ background: {c.surface_subtle}; color: {c.text_primary}; }}
    QPushButton#CardAction:pressed {{ background: {c.primary_subtle}; }}
    QPushButton#CardAction:focus {{ border: 1px solid {c.primary}; }}
    QPushButton#CardAction:disabled {{ color: {c.text_disabled}; }}
    QPushButton#QuotedMessage {{ min-height: 28px; padding: 4px 8px; border: 1px solid {c.border}; border-radius: 8px; background: {c.surface_subtle}; color: {c.text_secondary}; text-align: left; }}
    QPushButton#QuotedMessage:hover {{ background: {c.primary_subtle}; color: {c.text_primary}; }}
    QPushButton#NewMessagesBadge {{ min-height: 22px; padding: 3px 11px; border: 1px solid {c.primary}; border-radius: 13px; background: {c.primary}; color: {c.surface}; font-size: 12px; font-weight: 600; }}
    QPushButton#NewMessagesBadge:hover {{ background: {c.primary_hover}; border-color: {c.primary_hover}; }}
    QPushButton#NewMessagesBadge:pressed {{ background: {c.primary_pressed}; border-color: {c.primary_pressed}; }}
    QPushButton#NewMessagesBadge:focus {{ border: 2px solid {c.surface}; }}
    QPushButton#QuotedMessage:focus {{ border: 2px solid {c.primary}; }}
    QPushButton#ComposerTool {{ min-height: 30px; padding: 0 10px; border: none; background: transparent; color: {c.text_secondary}; }}
    QPushButton#ComposerTool:hover {{ color: {c.text_primary}; background: {c.surface_subtle}; }}
    QPushButton#SegmentedTab {{ min-height: 32px; border: none; background: transparent; color: {c.text_secondary}; }}
    QPushButton#SegmentedTab:hover {{ background: {c.surface_subtle}; color: {c.text_primary}; }}
    QPushButton#SegmentedTab:checked {{ background: {c.primary_subtle}; color: {c.primary}; font-weight: 600; }}
    QToolButton#NavItem {{ color: {c.text_secondary}; background: transparent; border: 1px solid transparent; border-radius: 10px; padding: 4px 2px 3px 2px; font-size: 12px; font-weight: 400; }}
    QToolButton#NavItem:hover {{ color: {c.text_primary}; background: {c.surface_subtle}; }}
    QToolButton#NavItem:pressed {{ color: {c.primary_pressed}; background: {c.primary_subtle}; }}
    QToolButton#NavItem:checked {{ color: {c.primary}; background: {c.primary_subtle}; }}
    QToolButton#NavItem:checked:hover {{ color: {c.primary_hover}; background: {c.primary_subtle}; }}
    QToolButton#NavItem:focus {{ border: 1px solid transparent; border-bottom: 2px solid {c.primary}; }}
    QToolButton#NavItem:disabled {{ color: {c.text_disabled}; background: transparent; }}
    QPushButton#DangerButton {{ color: {c.danger}; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{ color: {c.text_primary}; background: {c.surface}; border: 1px solid {c.border}; border-radius: 8px; padding: 8px 10px; selection-background-color: {c.primary}; selection-color: {c.surface}; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 2px solid {c.primary}; }}
    QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {{ color: {c.text_disabled}; background: {c.surface_subtle}; }}
    QTextEdit#ComposerInput {{ background: {c.surface_subtle}; border-color: {c.border}; border-radius: 8px; padding: 10px 12px; }}
    QTextEdit#ComposerInput:focus {{ background: {c.surface}; border: 2px solid {c.primary}; }}
    QListWidget, QTreeWidget, QTableWidget {{ color: {c.text_primary}; background: transparent; border: none; outline: none; alternate-background-color: {c.surface_subtle}; }}
    QListWidget#MentionCandidates, QListWidget#SearchResults {{ background: {c.surface_elevated}; border: 1px solid {c.border_strong}; border-radius: 8px; }}
    QListWidget#StickerGrid {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 8px; }}
    QListWidget::item, QTreeWidget::item {{ padding: 8px; border-radius: 8px; }}
    QListWidget#DirectoryList::item {{ min-height: 40px; padding: 6px 8px; }}
    QListWidget#SettingsNav::item {{ min-height: 32px; padding: 6px 10px; }}
    QListWidget#DirectoryList::item:disabled {{ min-height: 22px; color: {c.text_disabled}; font-size: 12px; font-weight: 600; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background: {c.surface_subtle}; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background: {c.primary_subtle}; color: {c.text_primary}; }}
    QTableWidget {{ gridline-color: {c.border}; }}
    QHeaderView::section {{ background: {c.surface_subtle}; color: {c.text_primary}; border: none; border-bottom: 1px solid {c.border}; padding: 8px; font-weight: 600; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px; }}
    QScrollBar::handle:vertical {{ background: {c.scrollbar}; border-radius: 4px; min-height: 28px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 3px; }}
    QScrollBar::handle:horizontal {{ background: {c.scrollbar}; border-radius: 4px; min-width: 28px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QDialog, QMessageBox {{ background: {c.surface_elevated}; color: {c.text_primary}; }}
    QToolTip {{ background: {c.surface_elevated}; color: {c.text_primary}; border: 1px solid {c.border_strong}; padding: 6px; }}
    QMenu {{ background: {c.surface_elevated}; color: {c.text_primary}; border: 1px solid {c.border_strong}; border-radius: 10px; padding: 6px; }}
    QMenu#MessageActionsMenu {{ border-radius: 10px; }}
    QMenu::item {{ padding: 8px 20px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c.primary_subtle}; }}
    QMenu::item:disabled {{ color: {c.text_disabled}; }}
    QComboBox QAbstractItemView {{ background: {c.surface_elevated}; color: {c.text_primary}; selection-background-color: {c.primary_subtle}; selection-color: {c.text_primary}; }}
    """


def stylesheet(tokens: ThemeTokens | None = None) -> str:
    return build_stylesheet(tokens or current_tokens())


class ThemeManager(QObject):
    """Own the persisted user mode and atomically apply the effective Qt theme."""

    themeAboutToChange = Signal(object, object)
    themeChanged = Signal(object)
    SETTINGS_KEY = "ui/theme_mode"

    def __init__(
        self,
        app: QApplication,
        settings: QSettings | None = None,
        *,
        system_scheme_getter: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(app)
        self.app = app
        self.settings = settings or QSettings()
        self._system_scheme_getter = system_scheme_getter or self._qt_system_scheme
        self._applying = False
        self._refresh_pending = False
        self._revision = 0
        self._mode = self._read_mode()
        variant = self._resolve_variant(self._mode)
        self._snapshot = ThemeSnapshot(self._mode, variant, TOKENS_BY_VARIANT[variant], self._revision)
        self._style_hints = QGuiApplication.styleHints()
        if hasattr(self._style_hints, "colorSchemeChanged"):
            self._style_hints.colorSchemeChanged.connect(self._schedule_system_refresh)

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def snapshot(self) -> ThemeSnapshot:
        return self._snapshot

    @property
    def tokens(self) -> ThemeTokens:
        return self._snapshot.tokens

    def apply_initial_theme(self) -> ThemeSnapshot:
        self._apply(self._resolve_variant(self._mode), force=True)
        return self._snapshot

    def set_mode(self, mode: ThemeMode | str) -> None:
        try:
            parsed = mode if isinstance(mode, ThemeMode) else ThemeMode(str(mode))
        except ValueError:
            parsed = ThemeMode.SYSTEM
        if parsed == self._mode:
            if parsed is ThemeMode.SYSTEM:
                self.refresh_system_theme()
            return
        self._mode = parsed
        self.settings.setValue(self.SETTINGS_KEY, parsed.value)
        self.settings.sync()
        self._apply(self._resolve_variant(parsed), force=True)

    def refresh_system_theme(self) -> None:
        if self._mode is ThemeMode.SYSTEM:
            self._apply(self._resolve_variant(self._mode))

    def dispose(self) -> None:
        if hasattr(self._style_hints, "colorSchemeChanged"):
            try:
                self._style_hints.colorSchemeChanged.disconnect(self._schedule_system_refresh)
            except (RuntimeError, TypeError):
                pass
        self.setParent(None)
        self.deleteLater()

    def _schedule_system_refresh(self, *_args: object) -> None:
        if self._mode is not ThemeMode.SYSTEM or self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._flush_system_refresh)

    def _flush_system_refresh(self) -> None:
        self._refresh_pending = False
        self.refresh_system_theme()

    def _read_mode(self) -> ThemeMode:
        raw = str(self.settings.value(self.SETTINGS_KEY, ThemeMode.SYSTEM.value))
        try:
            return ThemeMode(raw)
        except ValueError:
            return ThemeMode.SYSTEM

    def _qt_system_scheme(self) -> object:
        return QGuiApplication.styleHints().colorScheme()

    def _resolve_variant(self, mode: ThemeMode) -> ThemeVariant:
        if mode is ThemeMode.LIGHT:
            return ThemeVariant.LIGHT
        if mode is ThemeMode.DARK:
            return ThemeVariant.DARK
        scheme = self._system_scheme_getter()
        if scheme == Qt.ColorScheme.Dark or str(scheme).lower().endswith("dark"):
            return ThemeVariant.DARK
        return ThemeVariant.LIGHT

    def _apply(self, variant: ThemeVariant, *, force: bool = False) -> None:
        if self._applying:
            return
        if not force and variant is self._snapshot.variant and self._mode is self._snapshot.mode:
            return
        old = self._snapshot
        self._revision += 1
        new = ThemeSnapshot(self._mode, variant, TOKENS_BY_VARIANT[variant], self._revision)
        self.themeAboutToChange.emit(old, new)
        self._applying = True
        try:
            self.app.setPalette(build_palette(new.tokens))
            self.app.setStyleSheet(build_stylesheet(new.tokens))
            self._snapshot = new
        finally:
            self._applying = False
        self.themeChanged.emit(new)


_active_manager: ThemeManager | None = None


def install_theme_manager(
    app: QApplication,
    settings: QSettings | None = None,
    *,
    system_scheme_getter: Callable[[], object] | None = None,
) -> ThemeManager:
    global _active_manager
    if _active_manager is not None:
        try:
            _active_manager.dispose()
        except RuntimeError:
            pass
    _active_manager = ThemeManager(app, settings, system_scheme_getter=system_scheme_getter)
    _active_manager.apply_initial_theme()
    return _active_manager


def active_theme_manager() -> ThemeManager | None:
    return _active_manager


def current_tokens() -> ThemeTokens:
    return _active_manager.tokens if _active_manager is not None else LIGHT_TOKENS


def reset_theme_manager() -> None:
    """Release the active manager; intended for deterministic application/test teardown."""
    global _active_manager
    manager = _active_manager
    _active_manager = None
    if manager is not None:
        try:
            manager.dispose()
        except RuntimeError:
            pass


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(value: str) -> float:
        color = QColor(value)
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = tuple(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        )
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    first, second = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (first + 0.05) / (second + 0.05)
