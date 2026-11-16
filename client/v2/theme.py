"""V2 design tokens and shared Qt stylesheet."""

from __future__ import annotations


COLORS = {
    "canvas": "#F4F6FA",
    "surface": "#FFFFFF",
    "surface_soft": "#F8FAFD",
    "primary": "#4F7CFF",
    "primary_hover": "#416DE8",
    "primary_soft": "#EAF0FF",
    "agent": "#7657E8",
    "agent_soft": "#F0ECFF",
    "text": "#1D2433",
    "text_muted": "#667085",
    "border": "#E4E8F0",
    "success": "#26A269",
    "warning": "#D97706",
    "danger": "#D64545",
    "bubble_in": "#FFFFFF",
    "bubble_out": "#DDE8FF",
}


def stylesheet() -> str:
    c = COLORS
    return f"""
    * {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei UI", sans-serif;
        font-size: 14px;
        color: {c['text']};
    }}
    QMainWindow, QWidget#AppRoot {{ background: {c['canvas']}; }}
    QFrame#Surface, QWidget#Surface {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 14px;
    }}
    QFrame#DetailsPanel {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 14px;
    }}
    QFrame#DetailsOverview {{ background: {c['surface_soft']}; border: none; border-radius: 12px; }}
    QFrame#MemberRow {{ background: transparent; border: none; border-radius: 10px; }}
    QFrame#MemberRow:hover {{ background: {c['surface_soft']}; }}
    QFrame#ProfileCard {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
    QFrame#ProfilePane {{ background: transparent; border: none; }}
    QFrame#ProfilePopover {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
    QFrame#TaskRow {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 11px; }}
    QFrame#Rail {{ background: #EEF2F8; border: none; }}
    QLabel#PageTitle {{ font-size: 20px; font-weight: 700; }}
    QLabel#SectionTitle {{ font-size: 16px; font-weight: 650; }}
    QLabel#ProfileName {{ font-size: 20px; font-weight: 700; }}
    QLabel#TaskStatus {{ color: {c['text_muted']}; background: {c['surface_soft']}; border-radius: 8px; padding: 5px 8px; }}
    QLabel#Muted {{ color: {c['text_muted']}; }}
    QLabel#Danger {{ color: {c['danger']}; }}
    QLabel#DetailsSectionTitle {{ font-size: 13px; font-weight: 650; color: {c['text_muted']}; }}
    QLabel#MemberName {{ font-weight: 600; }}
    QLabel#RoleBadge {{ color: {c['text_muted']}; background: #EEF1F6; border-radius: 8px; padding: 3px 7px; font-size: 12px; }}
    QLabel#AgentBadge {{ color: {c['agent']}; background: {c['agent_soft']}; border-radius: 8px; padding: 3px 7px; font-size: 12px; font-weight: 650; }}
    QPushButton {{
        min-height: 34px;
        padding: 0 14px;
        border: 1px solid {c['border']};
        border-radius: 9px;
        background: {c['surface']};
    }}
    QPushButton:hover {{ background: {c['surface_soft']}; border-color: #CDD5E1; }}
    QPushButton:pressed {{ background: #EDF1F7; }}
    QPushButton:disabled {{ color: #98A2B3; background: #F2F4F7; }}
    QPushButton#Primary {{ color: white; background: {c['primary']}; border-color: {c['primary']}; font-weight: 650; }}
    QPushButton#Primary:hover {{ background: {c['primary_hover']}; }}
    QPushButton#Ghost {{ border: none; background: transparent; }}
    QPushButton#Ghost:hover {{ background: {c['primary_soft']}; }}
    QPushButton#Nav {{ border: none; background: transparent; min-height: 52px; border-radius: 12px; padding: 4px; }}
    QPushButton#Nav:checked {{ color: {c['primary']}; background: {c['surface']}; font-weight: 650; }}
    QPushButton#DangerButton {{ color: {c['danger']}; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 9px;
        padding: 8px 10px;
        selection-background-color: {c['primary']};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c['primary']}; }}
    QListWidget, QTreeWidget, QTableWidget {{
        background: transparent;
        border: none;
        outline: none;
        alternate-background-color: {c['surface_soft']};
    }}
    QListWidget::item, QTreeWidget::item {{ padding: 8px; border-radius: 8px; }}
    QListWidget#DirectoryList::item {{ min-height: 34px; }}
    QListWidget#DirectoryList::item:disabled {{ min-height: 22px; color: {c['text_muted']}; font-size: 12px; font-weight: 650; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background: {c['primary_soft']}; color: {c['text']}; }}
    QHeaderView::section {{ background: {c['surface_soft']}; border: none; border-bottom: 1px solid {c['border']}; padding: 8px; font-weight: 650; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px; }}
    QScrollBar::handle:vertical {{ background: #C9D1DE; border-radius: 4px; min-height: 28px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QToolTip {{ background: {c['text']}; color: white; border: none; padding: 6px; }}
    QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; padding: 6px; }}
    QMenu::item {{ padding: 8px 20px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c['primary_soft']}; }}
    """

