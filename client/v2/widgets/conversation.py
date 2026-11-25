"""Conversation list and empty states."""

from __future__ import annotations

from typing import Any
from .avatar import Avatar
from client.models import ConversationSummary
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from PySide6.QtCore import Qt, Signal
from ..theme import ThemeSnapshot, active_theme_manager
from ..icons import brand_logo_icon
import time


class ConversationCard(QFrame):
    activated = Signal(object)

    def __init__(self, summary: ConversationSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = summary
        self._draft_preview = ""
        self._preview_full = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("ConversationCard")
        self.setProperty("selected", False)
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(11)
        self._avatar = Avatar(summary.title, 40, group=summary.key.kind == "group")
        row.addWidget(self._avatar)
        text = QVBoxLayout()
        top = QHBoxLayout()
        self._title = QLabel()
        self._title.setObjectName("ConversationTitle")
        self._title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._title.setMinimumWidth(0)
        top.addWidget(self._title, 1)
        self._clock = QLabel()
        self._clock.setObjectName("ConversationTime")
        self._clock.setFixedWidth(46)
        self._clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self._clock)
        text.addLayout(top)
        bottom = QHBoxLayout()
        self._preview = QLabel()
        self._preview.setObjectName("Muted")
        self._preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._preview.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        bottom.addWidget(self._preview, 1)
        self._badge = QLabel()
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFixedSize(22, 22)
        self._badge.setObjectName("UnreadBadge")
        bottom.addWidget(self._badge)
        text.addLayout(bottom)
        row.addLayout(text, 1)
        self.update_summary(summary)

    def update_summary(self, summary: ConversationSummary) -> None:
        previous_title = self.summary.title
        self.summary = summary
        if summary.title != previous_title:
            self._avatar.set_avatar(summary.title, group=summary.key.kind == "group")
        self._title.setAccessibleName(summary.title)
        self._refresh_title()
        self._clock.setText(
            time.strftime("%H:%M", time.localtime(summary.timestamp)) if summary.timestamp else ""
        )
        if summary.unread_count:
            self._badge.setText(str(min(summary.unread_count, 99)))
            self._badge.setAccessibleName(f"{summary.unread_count} 条未读")
            self._badge.show()
        else:
            self._badge.clear()
            self._badge.hide()
        self._refresh_preview()

    def set_avatar(
        self,
        name: str,
        image_data: str | None = None,
        *,
        group: bool | None = None,
    ) -> None:
        self._avatar.set_avatar(name, image_data, group=group)

    def _refresh_title(self) -> None:
        available = max(0, self.width() - 24 - 40 - 11 - 46 - 6 - 2)
        title = self.summary.title
        if available <= 0 or self._title.width() <= 0:
            self._title.setText(title)
            return
        elided = self._title.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, available)
        if self._title.text() != elided:
            self._title.setText(elided)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._refresh_title()
        self._elide_preview()

    def set_draft_preview(self, preview: str) -> None:
        clean = " ".join(str(preview).split())
        if clean == self._draft_preview:
            return
        self._draft_preview = clean
        self._refresh_preview()

    def _default_preview(self) -> str:
        return self.summary.preview or ("群聊" if self.summary.key.kind == "group" else "开始聊天")

    def _refresh_preview(self) -> None:
        if self._draft_preview:
            self._preview_full = f"[草稿] {self._draft_preview}"
            self._preview.setAccessibleName("未发送草稿")
            style_name = "DraftPreview"
        else:
            self._preview_full = self._default_preview()
            self._preview.setAccessibleName("")
            style_name = "Muted"
        self._preview.setText(self._preview_full)
        if self._preview.objectName() != style_name:
            self._preview.setObjectName(style_name)
            self._preview.style().unpolish(self._preview)
            self._preview.style().polish(self._preview)
            self._preview.update()
        self._elide_preview()

    def _elide_preview(self) -> None:
        text = self._preview_full
        available = max(0, self.width() - 24 - 40 - 11 - 22 - 8)
        if available <= 0 or not text:
            return
        elided = self._preview.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, available)
        if self._preview.text() != elided:
            self._preview.setText(elided)

    def set_selected(self, selected: bool) -> None:
        if bool(self.property("selected")) == selected:
            return
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.summary.key)

class BrandLockup(QWidget):
    """Horizontal brand mark and wordmark used by the empty chat canvas."""

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(80, 80)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAccessibleName("Parlor")
        self.wordmark = QLabel("Parlor")
        self.wordmark.setObjectName("EmptyStateWordmark")
        self.wordmark.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.wordmark, 0, Qt.AlignmentFlag.AlignVCenter)
        self._refresh_icon()
        manager = active_theme_manager()
        if manager:
            manager.themeChanged.connect(self._refresh_icon)

    def _refresh_icon(self, _snapshot: ThemeSnapshot | None = None) -> None:
        self.icon_label.setPixmap(brand_logo_icon(72).pixmap(72, 72))

class EmptyState(QWidget):
    def __init__(
        self,
        title: str,
        description: str,
        icon_text: str = "◇",
        *,
        mark: QWidget | None = None,
        body_width: int | None = None,
    ) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if mark is not None:
            icon = mark
        else:
            icon = QLabel(icon_text)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setObjectName("EmptyStateIcon")
        heading = QLabel(title)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setObjectName("EmptyStateTitle")
        body = QLabel(description)
        body.setObjectName("Muted")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        if body_width:
            body.setMinimumWidth(body_width)
            body.setMaximumWidth(body_width)
        layout.addWidget(icon)
        layout.addWidget(heading)
        layout.addWidget(body)
