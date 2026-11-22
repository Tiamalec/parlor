"""Theme-aware Lucide icons backed by the V2 Qt resource bundle."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QFile, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import icons_rc  # noqa: F401  # registers :/icons resources
from .theme import ThemeVariant, active_theme_manager, current_tokens


NAVIGATION_ICONS = {
    "messages": "message-circle",
    "contacts": "users",
    "ai": "bot",
    "admin": "shield",
    "settings": "settings-2",
}


def _theme_revision() -> int:
    manager = active_theme_manager()
    return manager.snapshot.revision if manager is not None else 0


@lru_cache(maxsize=256)
def _svg_bytes(name: str) -> bytes:
    resource = QFile(f":/icons/{name}.svg")
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        raise ValueError(f"Unknown icon resource: {name}")
    try:
        return bytes(resource.readAll())
    finally:
        resource.close()


@lru_cache(maxsize=512)
def _render_pixmap(name: str, color: str, logical_size: int, dpr: int, revision: int) -> QPixmap:
    del revision  # retained in the cache key so old theme pixmaps cannot be reused
    svg = _svg_bytes(name).replace(b"currentColor", color.encode("ascii"))
    renderer = QSvgRenderer(QByteArray(svg))
    pixel_size = logical_size * dpr
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(float(dpr))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, logical_size, logical_size))
    painter.end()
    return pixmap


def lucide_icon(name: str, logical_size: int = 20) -> QIcon:
    """Return a two-DPR icon with semantic normal, active, selected and disabled states."""

    tokens = current_tokens()
    revision = _theme_revision()
    icon = QIcon()
    states = (
        (QIcon.Mode.Normal, QIcon.State.Off, tokens.text_secondary),
        (QIcon.Mode.Active, QIcon.State.Off, tokens.text_primary),
        (QIcon.Mode.Selected, QIcon.State.Off, tokens.primary),
        (QIcon.Mode.Disabled, QIcon.State.Off, tokens.text_disabled),
        (QIcon.Mode.Normal, QIcon.State.On, tokens.primary),
        (QIcon.Mode.Active, QIcon.State.On, tokens.primary_hover),
        (QIcon.Mode.Selected, QIcon.State.On, tokens.primary),
        (QIcon.Mode.Disabled, QIcon.State.On, tokens.text_disabled),
    )
    for mode, state, color in states:
        for dpr in (1, 2):
            icon.addPixmap(_render_pixmap(name, color, logical_size, dpr, revision), mode, state)
    return icon


def lucide_icon_with_color(name: str, color: str, logical_size: int = 20) -> QIcon:
    """Return a single-color icon for surfaces that need a specific token color."""
    revision = _theme_revision()
    icon = QIcon()
    for dpr in (1, 2):
        icon.addPixmap(_render_pixmap(name, color, logical_size, dpr, revision))
    return icon


@lru_cache(maxsize=32)
def _brand_icon(name: str, logical_size: int, revision: int) -> QIcon:
    icon = QIcon()
    for dpr in (1, 2):
        icon.addPixmap(_render_pixmap(name, "#000000", logical_size, dpr, revision))
    return icon


def brand_logo_icon(logical_size: int = 30) -> QIcon:
    """Return the two-colour chat-room mark for the current theme."""
    manager = active_theme_manager()
    dark = manager is not None and manager.snapshot.variant == ThemeVariant.DARK
    name = "brand-logo-dark" if dark else "brand-logo-light"
    return _brand_icon(name, logical_size, _theme_revision())


def brand_app_icon(logical_size: int = 64) -> QIcon:
    """Return the app/tray icon: the mark on its Agent-purple tile."""
    return _brand_icon("brand-app-icon", logical_size, _theme_revision())
