"""Avatar widgets."""

from __future__ import annotations

from typing import Any
from client.profile import ProfileViewModel
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import QRect, QTimer, Qt, Signal
from ..theme import ThemeSnapshot, active_theme_manager, current_tokens
import base64
from ..icons import lucide_icon_with_color


def initials(name: str) -> str:
    clean = name.strip()
    return (clean[:2] if clean else "?").upper()

def avatar_image(image_data: str | None) -> QImage:
    image = QImage()
    if image_data:
        try:
            image = QImage.fromData(base64.b64decode(image_data, validate=True))
        except (ValueError, TypeError):
            pass
    return image

def avatar_pixmap(
    name: str,
    size: int,
    image_data: str | None = None,
    *,
    agent: bool = False,
    group: bool = False,
) -> QPixmap:
    tokens = current_tokens()
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    image = avatar_image(image_data)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(
        QColor(tokens.agent if agent else tokens.surface_subtle if group else tokens.avatar_user)
    )
    painter.setPen(
        QPen(QColor(tokens.border_strong), 1) if group and not agent else QPen(Qt.PenStyle.NoPen)
    )
    painter.drawRoundedRect(0, 0, size, size, size / 2, size / 2)
    painter.setPen(Qt.PenStyle.NoPen)
    if not image.isNull():
        scaled = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        clip = QPainterPath()
        clip.addEllipse(0, 0, size, size)
        painter.setClipPath(clip)
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    elif group or agent:
        icon_size = max(14, int(size * 0.5))
        icon = lucide_icon_with_color(
            "users" if group else "bot",
            tokens.primary if group else tokens.on_primary,
            icon_size,
        )
        icon.paint(
            painter,
            QRect((size - icon_size) // 2, (size - icon_size) // 2, icon_size, icon_size),
            Qt.AlignmentFlag.AlignCenter,
        )
    else:
        painter.setPen(QColor(tokens.avatar_text))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(11, size // 3))
        painter.setFont(font)
        painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, initials(name))
    painter.end()
    return canvas

class Avatar(QLabel):
    def __init__(
        self,
        name: str = "",
        size: int = 38,
        *,
        agent: bool = False,
        group: bool = False,
        image_data: str | None = None,
    ) -> None:
        super().__init__()
        self._size = size
        self._agent = agent
        self._group = group
        self._name = name
        self._image_data = image_data
        self._has_pixmap = False
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_avatar(name, image_data, agent=agent, group=group)
        manager = active_theme_manager()
        if manager:
            manager.themeChanged.connect(self._theme_changed)

    def set_avatar(
        self,
        name: str,
        image_data: str | None = None,
        *,
        agent: bool | None = None,
        group: bool | None = None,
    ) -> None:
        next_agent = self._agent if agent is None else agent
        next_group = self._group if group is None else group
        if (
            self._has_pixmap
            and self._name == name
            and self._image_data == image_data
            and self._agent == next_agent
            and self._group == next_group
        ):
            return
        if agent is not None:
            self._agent = agent
        if group is not None:
            self._group = group
        self._name = name
        self._image_data = image_data
        self.setPixmap(
            avatar_pixmap(name, self._size, image_data, agent=self._agent, group=self._group)
        )
        self._has_pixmap = True
        # Avatars are rebuilt while conversations switch. A tooltip attached to
        # a newly created avatar under a stationary pointer makes Windows flash
        # a small tooltip window, so keep the name as accessibility text only.
        self.setAccessibleName(name)

    def _theme_changed(self, _snapshot: ThemeSnapshot) -> None:
        self.setPixmap(
            avatar_pixmap(
                self._name, self._size, self._image_data, agent=self._agent, group=self._group
            )
        )

class UserAvatar(Avatar):
    """Identity-aware avatar that uses the shared profile and fetch policy."""

    activated = Signal(object, object)
    profile_hovered = Signal(object, object)
    hover_left = Signal(object)
    context_requested = Signal(object, object)
    HOVER_DELAY_MS = 420

    def __init__(
        self,
        service: "ChatService",
        profile: ProfileViewModel,
        size: int = 38,
        *,
        clickable: bool = False,
        hoverable: bool = True,
    ) -> None:
        self.service = service
        self.profile = profile
        self.clickable = clickable
        self.hoverable = hoverable
        super().__init__(profile.display_name, size, agent=profile.kind == "agent")
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(self.HOVER_DELAY_MS)
        self._hover_timer.timeout.connect(self._emit_hover)
        self._hover_armed = False
        if hoverable:
            self.setMouseTracking(True)
        self.refresh(profile)

    def refresh(self, profile: ProfileViewModel | None = None) -> None:
        if profile is not None:
            self.profile = profile
        image_data = self.service.avatar_data(self.profile)
        self.set_avatar(
            self.profile.display_name,
            image_data,
            agent=self.profile.kind == "agent",
        )
        if self.profile.avatar_load_failed or (image_data is not None and avatar_image(image_data).isNull()):
            self.setAccessibleName(f"{self.profile.display_name}（头像暂时无法加载）")

    def mousePressEvent(self, event: Any) -> None:
        super().mousePressEvent(event)
        if self.clickable and event.button() == Qt.MouseButton.LeftButton:
            self._hover_timer.stop()
            self.activated.emit(self.profile, self)

    def enterEvent(self, event: Any) -> None:
        super().enterEvent(event)
        # A re-render can place a new avatar under a stationary pointer, which
        # produces a synthetic enter event. Arm hover only after real pointer
        # movement so that doesn't flash the profile popover.
        self._hover_armed = False
        self._hover_timer.stop()

    def mouseMoveEvent(self, event: Any) -> None:
        super().mouseMoveEvent(event)
        if self.hoverable:
            self._hover_armed = True
            self._hover_timer.start()

    def leaveEvent(self, event: Any) -> None:
        self._hover_timer.stop()
        self._hover_armed = False
        self.hover_left.emit(self.profile)
        super().leaveEvent(event)

    def _emit_hover(self) -> None:
        if self.hoverable and self._hover_armed and self.underMouse():
            self.profile_hovered.emit(self.profile, self)

    def contextMenuEvent(self, event: Any) -> None:
        self._hover_timer.stop()
        self.context_requested.emit(self.profile, self)
        event.accept()
