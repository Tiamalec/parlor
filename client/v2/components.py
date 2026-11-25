"""Small shared visual building blocks for the Parlor workspace."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import lucide_icon
from .theme import active_theme_manager, display_font


class PageHeader(QWidget):
    """Consistent page title, supporting copy and right-aligned actions."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        eyebrow: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self.eyebrow = QLabel(eyebrow)
        self.eyebrow.setObjectName("PageEyebrow")
        self.eyebrow.setVisible(bool(eyebrow))
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        self.title.setFont(display_font())
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        text_column.addWidget(self.eyebrow)
        text_column.addWidget(self.title)
        text_column.addWidget(self.subtitle)
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(8)
        root.addLayout(text_column, 1)
        root.addLayout(self.actions, 0)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return widget


class SectionHeader(QWidget):
    def __init__(
        self,
        title: str,
        meta: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("SectionTitle")
        self.meta = QLabel(meta)
        self.meta.setObjectName("SectionMeta")
        row.addWidget(self.title)
        row.addWidget(self.meta)
        row.addStretch(1)


class IconButton(QToolButton):
    """Uniform icon-only action with tooltip, accessible name and focus state."""

    def __init__(
        self,
        icon_name: str,
        tooltip: str,
        *,
        size: int = 32,
        icon_size: int = 18,
        checkable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.setObjectName("IconButton")
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCheckable(checkable)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh_icon()
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(self.refresh_icon)

    def refresh_icon(self, *_args: object) -> None:
        self.setIcon(lucide_icon(self.icon_name, self.iconSize().width()))


class StatusBadge(QLabel):
    def __init__(self, text: str = "", kind: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("StatusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.setProperty("statusKind", kind)
        self.style().unpolish(self)
        self.style().polish(self)


class SegmentedTabs(QWidget):
    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentedTabs")
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(3, 3, 3, 3)
        self._layout.setSpacing(3)
        self._group.idClicked.connect(self.currentChanged.emit)

    def add_item(self, text: str, data: Any = None) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("SegmentedTab")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if data is not None:
            button.setProperty("segmentData", data)
        index = len(self._buttons)
        self._buttons.append(button)
        self._group.addButton(button, index)
        self._layout.addWidget(button, 1)
        if index == 0:
            button.setChecked(True)
        return button

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:
        return self._group.checkedId()

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)
            self.currentChanged.emit(index)

    def currentData(self) -> Any:
        index = self.currentIndex()
        if 0 <= index < len(self._buttons):
            return self._buttons[index].property("segmentData")
        return None

    def setCurrentData(self, data: Any) -> bool:
        for index, button in enumerate(self._buttons):
            if button.property("segmentData") == data:
                self.setCurrentIndex(index)
                return True
        return False

    def button(self, index: int) -> QPushButton | None:
        return self._buttons[index] if 0 <= index < len(self._buttons) else None


class FilterBar(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.layout.addWidget(widget, stretch)
        return widget

    def add_stretch(self) -> None:
        self.layout.addStretch(1)


class InlineNotice(QLabel):
    def __init__(self, text: str = "", kind: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("InlineNotice")
        self.setWordWrap(True)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.setProperty("noticeKind", kind)
        self.style().unpolish(self)
        self.style().polish(self)

    def show_message(self, text: str, kind: str = "neutral") -> None:
        self.setText(text)
        self.set_kind(kind)
        self.setVisible(bool(text))


class StatTile(QFrame):
    def __init__(self, label: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self.value = QLabel(value)
        self.value.setObjectName("MetricValue")
        self.caption = QLabel(label)
        self.caption.setObjectName("Muted")
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set_value(self, value: str) -> None:
        self.value.setText(value)
