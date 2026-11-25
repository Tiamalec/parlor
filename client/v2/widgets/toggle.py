"""Toggle switch."""

from __future__ import annotations

from typing import Any
from PySide6.QtWidgets import QAbstractButton, QWidget
from PySide6.QtGui import QColor, QPainter
from PySide6.QtCore import QRectF, Qt
from ..theme import ThemeSnapshot, active_theme_manager, current_tokens


class ToggleSwitch(QAbstractButton):
    """Compact, theme-compatible boolean switch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        manager = active_theme_manager()
        if manager:
            manager.themeChanged.connect(self._theme_changed)

    def _theme_changed(self, _snapshot: ThemeSnapshot) -> None:
        self.update()

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        tokens = current_tokens()
        if not self.isEnabled():
            track = QColor(tokens.switch_disabled)
        else:
            track = QColor(tokens.primary if self.isChecked() else tokens.switch_off)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 2, 42, 20), 10, 10)
        painter.setBrush(QColor(tokens.switch_knob))
        knob_x = 21 if self.isChecked() else 3
        painter.drawEllipse(QRectF(knob_x, 4, 16, 16))
        painter.end()
