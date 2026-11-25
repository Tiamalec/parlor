"""Semantic Light/Dark/System theme infrastructure for the V2 Qt client."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Callable

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
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
    on_primary: str
    brand_panel: str
    on_brand_panel: str
    brand_accent: str
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
    canvas="#F3EFE7",
    surface="#FBF8F2",
    surface_subtle="#EFE9DD",
    surface_elevated="#FFFDF8",
    text_primary="#232A25",
    text_secondary="#4F5A52",
    text_tertiary="#666E66",
    text_disabled="#9A9A8F",
    border="#DED5C6",
    border_strong="#C8BCA9",
    primary="#2E5E4E",
    primary_hover="#264F42",
    primary_pressed="#1E4036",
    primary_subtle="#E4EEE8",
    on_primary="#FFFDF8",
    brand_panel="#2E5E4E",
    on_brand_panel="#FFFDF8",
    brand_accent="#D9C3A7",
    agent="#7A4B5A",
    agent_subtle="#F3E7E9",
    success="#2F6B4F",
    warning="#8A5A17",
    danger="#A13F3F",
    overlay_scrim="rgba(23, 27, 24, 0.48)",
    scrollbar="#C3B9A8",
    message_incoming="#FFFDF8",
    message_outgoing="#E1EBE4",
    message_agent="#F5EDEE",
    message_agent_border="#E3D2D5",
    mention_user_bg="#E4EEE8",
    mention_user_fg="#244A3E",
    mention_agent_bg="#F3E7E9",
    mention_agent_fg="#6B3F4C",
    code_bg="#1F2823",
    code_fg="#F1ECE2",
    avatar_user="#2E5E4E",
    avatar_text="#FFFDF8",
    switch_off="#C8BCA9",
    switch_disabled="#DED5C6",
    switch_knob="#FFFDF8",
)


DARK_TOKENS = ThemeTokens(
    canvas="#171B18",
    surface="#1E231F",
    surface_subtle="#252B26",
    surface_elevated="#2B322C",
    text_primary="#F1ECE2",
    text_secondary="#C4C0B6",
    text_tertiary="#A7A49A",
    text_disabled="#6E756D",
    border="#3A413A",
    border_strong="#4C544C",
    primary="#8FBFA9",
    primary_hover="#A8D0BD",
    primary_pressed="#74A590",
    primary_subtle="#24362D",
    on_primary="#171B18",
    brand_panel="#24362D",
    on_brand_panel="#F1ECE2",
    brand_accent="#D3A7B4",
    agent="#D3A7B4",
    agent_subtle="#36262B",
    success="#7FBF9B",
    warning="#D6A45C",
    danger="#E08B86",
    overlay_scrim="rgba(0, 0, 0, 0.64)",
    scrollbar="#4A524A",
    message_incoming="#232A25",
    message_outgoing="#263A30",
    message_agent="#30262A",
    message_agent_border="#4A3A40",
    mention_user_bg="#24362D",
    mention_user_fg="#A8D0BD",
    mention_agent_bg="#36262B",
    mention_agent_fg="#E0B7C2",
    code_bg="#0F1310",
    code_fg="#EDE8DD",
    avatar_user="#8FBFA9",
    avatar_text="#171B18",
    switch_off="#4A524A",
    switch_disabled="#3A413A",
    switch_knob="#F1ECE2",
)


TOKENS_BY_VARIANT = {ThemeVariant.LIGHT: LIGHT_TOKENS, ThemeVariant.DARK: DARK_TOKENS}


@dataclass(frozen=True, slots=True)
class ThemeMetrics:
    """Frozen layout and type measurements shared by QSS and page layouts."""

    space_xs: int = 4
    space_sm: int = 8
    space_md: int = 12
    space_lg: int = 16
    space_xl: int = 24
    space_2xl: int = 32
    space_3xl: int = 40
    radius_sm: int = 6
    radius_md: int = 8
    radius_lg: int = 12
    radius_xl: int = 16
    control_compact: int = 28
    control_default: int = 36
    control_primary: int = 40
    nav_row: int = 40
    sidebar_wide: int = 236
    sidebar_medium: int = 76
    sidebar_compact: int = 64
    page_margin_wide: int = 24
    page_margin_compact: int = 16
    chat_split_breakpoint: int = 1040
    sidebar_label_breakpoint: int = 1180
    sidebar_medium_breakpoint: int = 900
    details_dock_breakpoint: int = 1360
    font_micro: int = 11
    font_caption: int = 12
    font_body: int = 14
    font_section: int = 17
    font_page: int = 26
    font_page_compact: int = 22
    font_hero: int = 34
    font_metric: int = 30
    font_display_family: str = "Iowan Old Style, Palatino Linotype, Songti SC, STSong, Noto Serif CJK SC, Source Han Serif SC, SimSun, serif"
    font_ui_family: str = "PingFang SC, Hiragino Sans GB, Microsoft YaHei UI, Noto Sans CJK SC, system-ui, sans-serif"
    font_mono_family: str = "SF Mono, Menlo, Consolas, Liberation Mono, monospace"


METRICS = ThemeMetrics()


@dataclass(frozen=True, slots=True)
class ThemeSnapshot:
    mode: ThemeMode
    variant: ThemeVariant
    tokens: ThemeTokens
    metrics: ThemeMetrics
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
        QPalette.ColorRole.HighlightedText: tokens.on_primary,
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


def display_font(size: int = METRICS.font_page, weight: int = QFont.Weight.DemiBold) -> QFont:
    """Return the serif display face with deterministic system fallbacks."""

    font = QFont()
    font.setFamilies([part.strip() for part in METRICS.font_display_family.split(",")])
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def ui_font(size: int = METRICS.font_body, weight: int = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies([part.strip() for part in METRICS.font_ui_family.split(",")])
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def build_stylesheet(c: ThemeTokens) -> str:
    dark = c is DARK_TOKENS
    chevron = "chevron-down-dark" if dark else "chevron-down-light"
    check = "check-dark" if dark else "check-light"
    return f"""
    * {{ font-size: 14px; color: {c.text_primary}; }}
    QWidget {{ font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Noto Sans CJK SC", system-ui, sans-serif; }}
    QMainWindow, QWidget#AppRoot, QWidget[surfaceRole="canvas"] {{ background: {c.canvas}; }}
    QWidget[surfaceRole="surface"] {{ background: {c.surface}; }}
    QWidget[surfaceRole="subtle"] {{ background: {c.surface_subtle}; }}
    QWidget[surfaceRole="elevated"] {{ background: {c.surface_elevated}; }}
    QFrame#Surface, QWidget#Surface {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 12px; }}
    QFrame#DetailsPanel {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 12px; }}
    QFrame#DetailsOverview {{ background: {c.surface_subtle}; border: none; border-radius: 10px; }}
    QFrame#MemberRow {{ background: transparent; border: none; border-radius: 8px; }}
    QFrame#MemberRow:hover, QFrame#ConversationCard:hover {{ background: {c.surface_subtle}; }}
    QFrame#ConversationCard {{ background: transparent; border: none; border-left: 3px solid transparent; border-radius: 8px; }}
    QFrame#ConversationCard[selected="true"] {{ background: {c.primary_subtle}; border-left: 3px solid {c.primary}; }}
    QFrame#ProfileCard {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 12px; }}
    QFrame#ProfilePane {{ background: transparent; border: none; }}
    QFrame#ProfilePopover {{ background: {c.surface_elevated}; border: 1px solid {c.border_strong}; border-radius: 12px; }}
    QScrollArea#ProfileCardScroll, QWidget#ProfileCardViewport {{ background: transparent; border: none; }}
    QFrame#TaskRow {{ background: transparent; border: none; border-bottom: 1px solid {c.border}; border-radius: 0; }}
    QFrame#TaskRow:hover {{ background: {c.surface_subtle}; }}
    QFrame#TodoWorkspace {{ background: {c.surface}; border: none; border-radius: 0; }}
    QLabel#TodoPageTitle {{ color: {c.text_primary}; font-size: 26px; font-weight: 700; }}
    QLabel#TodoSummary {{ color: {c.text_secondary}; font-size: 13px; padding-left: 4px; }}
    QLineEdit#TodoQuickInput {{ min-height: 26px; padding: 8px 12px; background: {c.surface}; border: 1px solid {c.border_strong}; border-radius: 8px; font-size: 14px; }}
    QLineEdit#TodoQuickInput:focus {{ border: 2px solid {c.primary}; padding: 7px 11px; }}
    QPushButton#TodoAddButton {{ min-height: 28px; padding: 8px 20px; color: {c.surface}; background: {c.primary}; border: 1px solid {c.primary}; border-radius: 8px; font-weight: 600; }}
    QPushButton#TodoAddButton:hover {{ background: {c.primary_hover}; border-color: {c.primary_hover}; }}
    QPushButton#TodoAddButton:pressed {{ background: {c.primary_pressed}; border-color: {c.primary_pressed}; }}
    QPushButton#TodoAddButton:disabled {{ color: {c.text_disabled}; background: {c.surface_subtle}; border-color: {c.border}; }}
    QPushButton#TodoChoice {{ min-height: 36px; padding: 0 31px 0 14px; text-align: left; color: {c.text_primary}; background: {c.surface}; border: 1px solid {c.border_strong}; border-radius: 18px; font-size: 13px; }}
    QPushButton#TodoChoice:hover {{ background: {c.surface_subtle}; border-color: {c.primary}; }}
    QPushButton#TodoChoice:pressed {{ background: {c.primary_subtle}; }}
    QPushButton#TodoChoice:focus {{ border: 2px solid {c.primary}; padding: 0 30px 0 13px; }}
    QPushButton#TodoChoice:disabled {{ color: {c.text_disabled}; background: {c.surface_subtle}; border-color: {c.border}; }}
    QPushButton#TodoStatusChoice {{ color: {c.text_secondary}; }}
    QMenu#TodoChoiceMenu {{ background: {c.surface_elevated}; color: {c.text_primary}; border: 1px solid {c.border_strong}; border-radius: 10px; padding: 6px; }}
    QFrame#TodoRow {{ background: {c.surface}; border: none; border-bottom: 1px solid {c.border}; }}
    QFrame#TodoRow:hover {{ background: {c.surface_subtle}; }}
    QFrame#TodoRow[selected="true"] {{ background: {c.primary_subtle}; border: none; border-radius: 8px; }}
    QPushButton#TodoTitle {{ min-height: 22px; padding: 0; background: transparent; border: 1px solid transparent; color: {c.text_primary}; text-align: left; font-size: 14px; font-weight: 600; }}
    QPushButton#TodoTitle:hover {{ color: {c.primary}; }}
    QPushButton#TodoTitle:focus {{ border: 1px solid {c.primary}; border-radius: 4px; }}
    QFrame#TodoRow[completed="true"] QPushButton#TodoTitle {{ color: {c.text_tertiary}; }}
    QLabel#TodoMeta, QLabel#TodoSource, QLabel#TodoCreatedAt {{ color: {c.text_tertiary}; font-size: 12px; background: transparent; }}
    QLabel#TodoMeta[state="overdue"] {{ color: {c.danger}; }}
    QLabel#TodoRowIcon, QLabel#TodoFieldIcon, QLabel#TodoFlag {{ background: transparent; border: none; }}
    QFrame#TodoDetail {{ background: {c.surface}; border: none; border-left: 1px solid {c.border}; }}
    QScrollArea#TodoListScroll, QScrollArea#TodoDetailScroll {{ background: {c.surface}; border: none; }}
    QLabel#TodoCalendarMonth {{ color: {c.text_primary}; font-size: 14px; font-weight: 600; }}
    QCalendarWidget {{ background: {c.surface_elevated}; color: {c.text_primary}; }}
    QCalendarWidget QAbstractItemView {{ background: {c.surface_elevated}; color: {c.text_primary}; selection-background-color: {c.primary_subtle}; selection-color: {c.primary}; border: none; outline: none; }}
    QFrame#TodoSection {{ background: {c.surface}; border: none; }}
    QPushButton#TodoSectionToggle {{ color: {c.text_primary}; background: transparent; border: 1px solid transparent; padding: 0; font-size: 15px; font-weight: 600; text-align: left; }}
    QPushButton#TodoSectionToggle:hover {{ color: {c.primary}; }}
    QPushButton#TodoSectionToggle:focus {{ border: 1px solid {c.primary}; border-radius: 4px; }}
    QLabel#TodoSectionCount {{ color: {c.text_secondary}; background: {c.surface_subtle}; border-radius: 10px; padding: 3px 8px; font-size: 12px; }}
    QLabel#TodoPriorityBadge {{ background: {c.surface_subtle}; border-radius: 9px; padding: 3px 8px; font-size: 12px; font-weight: 600; }}
    QLabel#TodoPriorityBadge[priority="high"] {{ color: {c.danger}; }}
    QLabel#TodoPriorityBadge[priority="normal"] {{ color: {c.warning}; }}
    QLabel#TodoPriorityBadge[priority="low"] {{ color: {c.primary}; }}
    QLabel#TodoDoneBadge {{ color: {c.success}; background: {c.surface_subtle}; border-radius: 9px; padding: 3px 8px; font-size: 12px; }}
    QLineEdit#TodoDetailTitle {{ color: {c.text_primary}; background: transparent; border: 1px solid transparent; padding: 2px 0; min-height: 38px; font-size: 20px; font-weight: 700; }}
    QLineEdit#TodoDetailTitle:hover {{ border-bottom: 1px solid {c.border_strong}; }}
    QLineEdit#TodoDetailTitle:focus {{ border: 1px solid {c.primary}; border-radius: 4px; padding: 1px 4px; }}
    QTextEdit#TodoDescription {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 8px; padding: 8px; }}
    QLineEdit#TodoDateInput {{ min-height: 24px; padding: 7px 10px; }}
    QLabel#TodoFieldLabel {{ color: {c.text_secondary}; font-size: 13px; font-weight: 600; background: transparent; }}
    QPushButton#TodoLink {{ color: {c.primary}; background: transparent; border: none; padding: 0; text-align: left; font-size: 13px; }}
    QPushButton#TodoLink:hover {{ color: {c.primary_hover}; }}
    QPushButton#TodoLink:focus {{ border-bottom: 2px solid {c.primary}; }}
    QToolButton#TodoIconButton {{ color: {c.text_secondary}; background: transparent; border: 1px solid transparent; border-radius: 6px; min-width: 32px; min-height: 32px; }}
    QToolButton#TodoIconButton:hover {{ background: {c.surface_subtle}; }}
    QToolButton#TodoIconButton:pressed {{ background: {c.primary_subtle}; }}
    QToolButton#TodoIconButton:focus {{ border: 2px solid {c.primary}; }}
    QToolButton#TodoIconButton:disabled {{ background: transparent; }}
    QLabel#TodoEmpty {{ color: {c.text_secondary}; font-size: 14px; background: transparent; }}
    QLabel#TodoEmptyIcon {{ background: transparent; }}
    QLabel#TodoFeedback {{ color: {c.text_secondary}; background: {c.surface_subtle}; border: 1px solid {c.border}; border-radius: 8px; padding: 7px 10px; font-size: 12px; }}
    QLabel#TodoFeedback[state="error"] {{ color: {c.danger}; }}
    QLabel#TodoFeedback[state="offline"] {{ color: {c.warning}; }}
    QFrame#TodoDetailFooter {{ background: {c.surface}; border: none; border-top: 1px solid {c.border}; }}
    QFrame#SettingsSection {{ background: transparent; border: none; border-bottom: 1px solid {c.border}; }}
    QFrame#SettingsRow {{ background: transparent; border: none; }}
    QFrame#SettingsSidebar {{ background: transparent; border: none; border-right: 1px solid {c.border}; }}
    QListWidget#SettingsNav {{ background: transparent; border: none; padding: 0 14px 0 0; }}
    QListWidget#SettingsNav::item {{ min-height: 38px; padding: 7px 12px; border-radius: 8px; color: {c.text_secondary}; }}
    QListWidget#SettingsNav::item:hover {{ background: {c.surface_subtle}; color: {c.text_primary}; }}
    QListWidget#SettingsNav::item:selected {{ background: {c.primary_subtle}; color: {c.primary}; font-weight: 600; }}
    QFrame#Sidebar, QFrame#Rail {{ background: {c.surface}; border: none; border-right: 1px solid {c.border}; }}
    QFrame#ConversationPane {{ background: {c.surface}; border: none; border-right: 1px solid {c.border}; border-radius: 0; }}
    QFrame#ChatSurface {{ background: {c.surface}; border: none; border-radius: 0; }}
    QFrame#SidebarBrand {{ background: transparent; border: none; }}
    QLabel#SidebarWordmark {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 21px; font-weight: 600; color: {c.text_primary}; }}
    QLabel#SidebarMeta {{ color: {c.text_tertiary}; font-size: 11px; }}
    QLabel#SidebarSection {{ color: {c.text_tertiary}; font-size: 11px; font-weight: 600; padding: 0 12px; }}
    QFrame#SidebarUser {{ background: {c.surface_subtle}; border: 1px solid {c.border}; border-radius: 10px; }}
    QLabel#SidebarUserName {{ color: {c.text_primary}; font-size: 13px; font-weight: 600; }}
    QLabel#SidebarUserRole {{ color: {c.text_tertiary}; font-size: 11px; }}
    QToolButton#SidebarAction {{ color: {c.text_secondary}; background: transparent; border: 1px solid transparent; border-radius: 8px; min-width: 30px; min-height: 30px; }}
    QToolButton#SidebarAction:hover {{ color: {c.text_primary}; background: {c.surface}; }}
    QToolButton#SidebarAction:focus {{ border: 2px solid {c.primary}; }}
    QFrame#DetailsScrim {{ background: {c.overlay_scrim}; border: none; }}
    QWidget#ChatHeader {{ background: {c.surface}; border-bottom: 1px solid {c.border}; }}
    QWidget#Composer {{ background: {c.surface}; border-top: 1px solid {c.border}; }}
    QFrame#ContactsDirectory {{ background: {c.surface}; border: none; border-right: 1px solid {c.border}; border-radius: 0; }}
    QStackedWidget#ContactDetails {{ background: transparent; border: none; border-radius: 0; }}
    QStackedWidget#SettingsContent {{ background: transparent; border: none; }}
    QWidget#PageHeader {{ background: transparent; border: none; }}
    QLabel#PageEyebrow {{ color: {c.primary}; font-size: 11px; font-weight: 600; }}
    QLabel#PageTitle {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 26px; font-weight: 600; }}
    QLabel#PageSubtitle {{ color: {c.text_secondary}; font-size: 13px; }}
    QLabel#SectionTitle {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 17px; font-weight: 600; }}
    QLabel#SectionMeta {{ color: {c.text_tertiary}; font-size: 12px; }}
    QFrame#FilterBar {{ background: transparent; border: none; }}
    QFrame#SegmentedTabs, QFrame#AuthModeTrack {{ background: {c.surface_subtle}; border: 1px solid {c.border}; border-radius: 9px; }}
    QPushButton#SegmentedTab {{ min-height: 30px; border: 1px solid transparent; border-radius: 7px; background: transparent; color: {c.text_secondary}; font-size: 13px; }}
    QPushButton#SegmentedTab:checked {{ background: {c.surface_elevated}; border-color: {c.border}; color: {c.primary}; font-weight: 600; }}
    QPushButton#SegmentedTab:focus {{ border: 2px solid {c.primary}; }}
    QToolButton#IconButton {{ color: {c.text_secondary}; background: transparent; border: 1px solid transparent; border-radius: 8px; }}
    QToolButton#IconButton:hover {{ color: {c.text_primary}; background: {c.surface_subtle}; }}
    QToolButton#IconButton:pressed {{ background: {c.primary_subtle}; color: {c.primary_pressed}; }}
    QToolButton#IconButton:checked {{ background: {c.primary_subtle}; color: {c.primary}; }}
    QToolButton#IconButton:focus {{ border: 2px solid {c.primary}; }}
    QLabel#StatusBadge {{ color: {c.text_secondary}; background: {c.surface_subtle}; border: 1px solid {c.border}; border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: 600; }}
    QLabel#StatusBadge[statusKind="success"] {{ color: {c.success}; background: {c.primary_subtle}; border-color: {c.border}; }}
    QLabel#StatusBadge[statusKind="warning"] {{ color: {c.warning}; background: {c.surface_subtle}; }}
    QLabel#StatusBadge[statusKind="danger"] {{ color: {c.danger}; background: {c.surface_subtle}; }}
    QLabel#StatusBadge[statusKind="agent"] {{ color: {c.agent}; background: {c.agent_subtle}; border-color: {c.message_agent_border}; }}
    QFrame#StatTile {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 12px; }}
    QLabel#ProfileName {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 22px; font-weight: 600; }}
    QLabel#MetricValue {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 30px; font-weight: 600; }}
    QLabel#EmptyStateIcon {{ font-size: 42px; }}
    QLabel#EmptyStateWordmark {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 30px; font-weight: 600; }}
    QLabel#EmptyStateTitle {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 20px; font-weight: 600; }}
    QLabel#AuthBrandTile {{ background: {c.primary_subtle}; border: 1px solid {c.border}; border-radius: 12px; }}
    QLabel#AuthWordmark {{ font-size: 16px; font-weight: 700; }}
    QLabel#AuthWordmarkMeta {{ color: {c.text_tertiary}; font-size: 12px; }}
    QLabel#AuthHeroTitle {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 34px; font-weight: 600; }}
    QLabel#AuthHeroText {{ color: {c.text_secondary}; font-size: 14px; }}
    QLabel#AuthFeatureText {{ color: {c.text_secondary}; }}
    QLabel#AuthHeading {{ font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 26px; font-weight: 600; }}
    QWidget#AuthShell {{ background: {c.canvas}; }}
    QWidget#AuthHeroPanel {{ background: {c.brand_panel}; border: none; }}
    QWidget#AuthHeroPanel QLabel#AuthWordmark {{ color: {c.on_brand_panel}; font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 20px; font-weight: 600; }}
    QWidget#AuthHeroPanel QLabel#AuthWordmarkMeta {{ color: {c.brand_accent}; font-size: 11px; }}
    QWidget#AuthHeroPanel QLabel#AuthBrandTile {{ background: transparent; border: none; }}
    QWidget#AuthHeroPanel QLabel#AuthHeroTitle {{ color: {c.on_brand_panel}; font-family: "Iowan Old Style", "Palatino Linotype", "Songti SC", "STSong", "Noto Serif CJK SC", serif; font-size: 38px; font-weight: 600; }}
    QWidget#AuthHeroPanel QLabel#AuthHeroText {{ color: {c.on_brand_panel}; font-size: 14px; }}
    QWidget#AuthHeroPanel QLabel#AuthFeatureText {{ color: {c.on_brand_panel}; font-size: 13px; }}
    QFrame#AuthFormPanel {{ background: {c.canvas}; border: none; }}
    QFrame#AuthFormCard {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 16px; }}
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
    QLabel#NavBadge {{ color: {c.on_primary}; background: {c.danger}; border: none; border-radius: 8px; min-width: 16px; min-height: 16px; padding: 0 4px; font-size: 10px; font-weight: 700; }}
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
    QLabel#UnreadBadge {{ background: {c.danger}; color: {c.on_primary}; border-radius: 11px; font-size: 11px; font-weight: 700; }}
    QLabel#RoleBadge {{ color: {c.text_secondary}; background: {c.surface_subtle}; border-radius: 8px; padding: 3px 7px; font-size: 12px; }}
    QLabel#AgentBadge {{ color: {c.agent}; background: {c.agent_subtle}; border-radius: 8px; padding: 3px 7px; font-size: 12px; font-weight: 600; }}
    QLabel#AgentBadge[online="false"] {{ color: {c.text_secondary}; background: {c.surface_subtle}; }}
    QLabel#MessageSender {{ color: {c.text_secondary}; font-size: 12px; font-weight: 600; }}
    QLabel#AgentMessageSender {{ color: {c.agent}; font-size: 12px; font-weight: 600; }}
    QLabel#AgentActivity {{ color: {c.text_secondary}; padding: 3px 5px; }}
    QLabel#SystemMessage {{ color: {c.text_tertiary}; background: {c.surface_subtle}; border-radius: 8px; padding: 6px 12px; }}
    QFrame#MessageBubble {{ border: 1px solid {c.border}; border-radius: 12px; }}
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
    QLabel#CardTaskDescription {{ color: {c.text_secondary}; font-size: 12px; }}
    QWidget#CardTaskContent, QWidget#CardTaskPriorityRow {{ background: transparent; border: none; }}
    QLabel#CardTaskPriority {{ color: {c.text_secondary}; font-size: 11px; font-weight: 600; }}
    QLabel#CardTaskPriority[priority="high"] {{ color: {c.danger}; }}
    QLabel#CardTaskPriority[priority="normal"] {{ color: {c.warning}; }}
    QLabel#CardTaskPriority[priority="low"] {{ color: {c.primary}; }}
    QLabel#CardTaskPriorityFlag {{ background: transparent; }}
    QLabel#CardTaskPriorityBasis {{ color: {c.warning}; background: {c.surface_subtle}; border-radius: 7px; padding: 1px 5px; font-size: 10px; }}
    QLabel#CardTaskStatus {{ color: {c.text_tertiary}; background: {c.surface_subtle}; border-radius: 7px; padding: 1px 6px; font-size: 10px; }}
    QLabel#CardTaskSyncState {{ color: {c.success}; font-size: 11px; font-weight: 600; }}
    QFrame#CardImportNotice {{ background: {c.surface_subtle}; border: none; border-radius: 7px; }}
    QLabel#CardImportFeedback {{ color: {c.text_secondary}; background: transparent; font-size: 11px; }}
    QFrame#CardImportNotice[state="success"] QLabel#CardImportFeedback {{ color: {c.success}; }}
    QFrame#CardImportNotice[state="failure"] QLabel#CardImportFeedback {{ color: {c.danger}; }}
    QFrame#CardImportNotice[state="partial"] QLabel#CardImportFeedback {{ color: {c.warning}; }}
    QFrame#CardImportNotice[state="pending"] QLabel#CardImportFeedback {{ color: {c.primary}; }}
    QPushButton#CardImportAction {{ min-height: 28px; padding: 0 10px; border: 1px solid {c.primary}; border-radius: 7px; background: {c.primary_subtle}; color: {c.primary}; font-size: 12px; font-weight: 600; }}
    QPushButton#CardImportAction:hover {{ background: {c.primary}; color: {c.on_primary}; }}
    QPushButton#CardImportAction:disabled {{ color: {c.text_disabled}; background: {c.surface_subtle}; border-color: {c.border}; }}
    QPushButton#CardTaskTodoAction {{ min-height: 22px; padding: 0 4px; border: none; border-radius: 5px; background: transparent; color: {c.primary}; font-size: 11px; }}
    QPushButton#CardTaskTodoAction:hover {{ background: {c.primary_subtle}; }}
    QPushButton {{ min-height: 34px; padding: 0 14px; border: 1px solid {c.border}; border-radius: 8px; background: {c.surface}; color: {c.text_primary}; }}
    QPushButton:hover {{ background: {c.surface_subtle}; border-color: {c.border_strong}; }}
    QPushButton:pressed {{ background: {c.primary_subtle}; }}
    QPushButton:focus {{ border: 2px solid {c.primary}; }}
    QPushButton:disabled {{ color: {c.text_disabled}; background: {c.surface_subtle}; }}
    QPushButton#Primary {{ color: {c.on_primary}; background: {c.primary}; border-color: {c.primary}; font-weight: 600; }}
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
    QPushButton#NewMessagesBadge {{ min-height: 22px; padding: 3px 11px; border: 1px solid {c.primary}; border-radius: 13px; background: {c.primary}; color: {c.on_primary}; font-size: 12px; font-weight: 600; }}
    QPushButton#NewMessagesBadge:hover {{ background: {c.primary_hover}; border-color: {c.primary_hover}; }}
    QPushButton#NewMessagesBadge:pressed {{ background: {c.primary_pressed}; border-color: {c.primary_pressed}; }}
    QPushButton#NewMessagesBadge:focus {{ border: 2px solid {c.surface}; }}
    QPushButton#QuotedMessage:focus {{ border: 2px solid {c.primary}; }}
    QPushButton#ComposerTool {{ min-height: 30px; padding: 0 10px; border: none; background: transparent; color: {c.text_secondary}; }}
    QPushButton#ComposerTool:hover {{ color: {c.text_primary}; background: {c.surface_subtle}; }}
    QPushButton#SegmentedTab {{ min-height: 32px; border: none; background: transparent; color: {c.text_secondary}; }}
    QPushButton#SegmentedTab:hover {{ background: {c.surface_subtle}; color: {c.text_primary}; }}
    QPushButton#SegmentedTab:checked {{ background: {c.primary_subtle}; color: {c.primary}; font-weight: 600; }}
    QToolButton#NavItem {{ color: {c.text_secondary}; background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 0 12px; font-size: 13px; font-weight: 400; text-align: left; }}
    QToolButton#NavItem:hover {{ color: {c.text_primary}; background: {c.surface_subtle}; }}
    QToolButton#NavItem:pressed {{ color: {c.primary_pressed}; background: {c.primary_subtle}; }}
    QToolButton#NavItem:checked {{ color: {c.primary}; background: {c.primary_subtle}; font-weight: 600; }}
    QToolButton#NavItem:checked:hover {{ color: {c.primary_hover}; background: {c.primary_subtle}; }}
    QToolButton#NavItem:focus {{ border: 2px solid {c.primary}; }}
    QToolButton#NavItem[compact="true"] {{ padding: 2px 0; text-align: center; font-size: 11px; }}
    QToolButton#NavItem:disabled {{ color: {c.text_disabled}; background: transparent; }}
    QPushButton#DangerButton {{ color: {c.danger}; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox, QDateTimeEdit, QTimeEdit {{ color: {c.text_primary}; background: {c.surface}; border: 1px solid {c.border}; border-radius: 8px; padding: 8px 10px; selection-background-color: {c.primary}; selection-color: {c.on_primary}; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus, QTimeEdit:focus {{ border: 2px solid {c.primary}; }}
    QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {{ color: {c.text_disabled}; background: {c.surface_subtle}; }}
    QComboBox {{ padding-right: 30px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; background: transparent; }}
    QComboBox::down-arrow {{ image: url(:/icons/{chevron}.svg); width: 13px; height: 13px; }}
    QCheckBox {{ spacing: 9px; color: {c.text_primary}; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c.border_strong}; border-radius: 4px; background: {c.surface}; }}
    QCheckBox::indicator:hover {{ border-color: {c.primary}; }}
    QCheckBox::indicator:checked {{ background: {c.primary}; border-color: {c.primary}; image: url(:/icons/{check}.svg); }}
    QCheckBox::indicator:disabled {{ background: {c.surface_subtle}; border-color: {c.border}; }}
    QTextEdit#ComposerInput {{ background: {c.surface_subtle}; border-color: {c.border}; border-radius: 8px; padding: 10px 12px; }}
    QTextEdit#ComposerInput:focus {{ background: {c.surface}; border: 2px solid {c.primary}; }}
    QListWidget, QTreeWidget, QTableWidget {{ color: {c.text_primary}; background: transparent; border: none; outline: none; alternate-background-color: {c.surface_subtle}; }}
    QListWidget::indicator {{ width: 16px; height: 16px; border: 1px solid {c.border_strong}; border-radius: 4px; background: {c.surface}; }}
    QListWidget::indicator:hover {{ border-color: {c.primary}; }}
    QListWidget::indicator:checked {{ background: {c.primary}; border-color: {c.primary}; image: url(:/icons/{check}.svg); }}
    QListWidget::indicator:disabled {{ background: {c.surface_subtle}; border-color: {c.border}; }}
    QListWidget#MentionCandidates, QListWidget#SearchResults {{ background: {c.surface_elevated}; border: 1px solid {c.border_strong}; border-radius: 8px; }}
    QListWidget#StickerGrid {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 8px; }}
    QListWidget::item, QTreeWidget::item {{ padding: 8px; border-radius: 8px; }}
    QListWidget#DirectoryList::item {{ min-height: 44px; padding: 6px 8px; border-radius: 8px; }}
    QListWidget#SettingsNav::item {{ min-height: 32px; padding: 6px 10px; }}
    QListWidget#DirectoryList::item:disabled {{ min-height: 22px; color: {c.text_disabled}; font-size: 12px; font-weight: 600; }}
    QListWidget#AdminList {{ background: transparent; border: none; }}
    QListWidget#AdminList::item {{ min-height: 32px; padding: 6px 8px; border-bottom: 1px solid {c.border}; border-radius: 0; }}
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
        self._snapshot = ThemeSnapshot(self._mode, variant, TOKENS_BY_VARIANT[variant], METRICS, self._revision)
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
        new = ThemeSnapshot(self._mode, variant, TOKENS_BY_VARIANT[variant], METRICS, self._revision)
        self.themeAboutToChange.emit(old, new)
        self._applying = True
        try:
            self.app.setPalette(build_palette(new.tokens))
            self.app.setFont(ui_font(METRICS.font_body))
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
