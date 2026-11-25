"""Surface helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QScrollArea, QWidget
from PySide6.QtCore import Qt


def configure_surface(widget: QWidget, role: str) -> None:
    """Make a widget paint an explicit semantic surface instead of its system palette."""
    if role not in {"surface", "canvas", "subtle", "elevated"}:
        raise ValueError(f"unsupported surface role: {role}")
    widget.setProperty("surfaceRole", role)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

def configure_scroll_surface(scroll: QScrollArea, content: QWidget, role: str = "surface") -> None:
    """Give a scroll area, its viewport, and its content one explicit surface."""
    for widget in (scroll, scroll.viewport(), content):
        configure_surface(widget, role)
