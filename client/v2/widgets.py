"""Reusable widgets for the Chatroom V2 interface."""

from __future__ import annotations

import base64
import html
import json
import math
import re
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMimeData, QRect, QRectF, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QMovie,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from client.models import ConversationSummary
from client.profile import ProfileViewModel

from .agent_cards import SummaryCard, TodoCard
from .icons import brand_logo_icon, lucide_icon_with_color
from .theme import ThemeSnapshot, active_theme_manager, current_tokens

if TYPE_CHECKING:
    from client.service import ChatService


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
    elif group:
        icon_size = max(14, int(size * 0.5))
        icon = lucide_icon_with_color("users", tokens.primary, icon_size)
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
        painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, "✦" if agent else initials(name))
    painter.end()
    return canvas


def mentions_to_safe_html(source: str, mentions: list[dict[str, Any]]) -> str:
    """Escape plain text and highlight validated mention ranges without parsing Markdown."""
    tokens = current_tokens()
    parts: list[str] = [f'<span style="color:{tokens.text_primary};">']
    cursor = 0
    for mention in sorted(mentions, key=lambda item: int(item.get("start", -1))):
        start = int(mention.get("start", -1))
        length = int(mention.get("length", 0))
        if start < cursor or length <= 0 or start + length > len(source):
            continue
        parts.append(html.escape(source[cursor:start]).replace("\n", "<br>"))
        token = html.escape(source[start:start + length])
        is_agent = mention.get("kind") == "agent"
        foreground = tokens.mention_agent_fg if is_agent else tokens.mention_user_fg
        background = tokens.mention_agent_bg if is_agent else tokens.mention_user_bg
        parts.append(
            f'<span style="color:{foreground};background-color:{background};font-weight:600;">{token}</span>'
        )
        cursor = start + length
    parts.append(html.escape(source[cursor:]).replace("\n", "<br>"))
    parts.append("</span>")
    return "".join(parts)


def markdown_to_safe_html(source: str) -> str:
    """Render compact chat Markdown without embedded HTML or remote images."""
    tokens = current_tokens()
    document = QTextDocument()
    features = (
        QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        | QTextDocument.MarkdownFeature.MarkdownNoHTML
    )
    document.setMarkdown(source, features)
    rendered = re.sub(r"<img\b[^>]*>", "", document.toHtml(), flags=re.IGNORECASE)
    rendered = rendered.replace("font-family:;", "font-family:'Segoe UI','Microsoft YaHei UI',sans-serif;")
    rendered = rendered.replace("font-weight:400;", f"color:{tokens.text_primary}; font-size:14px; font-weight:400;", 1)
    rendered = rendered.replace("font-size:xx-large", "font-size:16px")
    rendered = rendered.replace("font-size:x-large", "font-size:15px")
    rendered = rendered.replace("font-size:large", "font-size:14px")
    rendered = re.sub(
        r'<h([1-6]) style=" ([^"]*)margin-bottom:0px;([^"]*)">',
        lambda match: (
            f'<h{match.group(1)} style=" {match.group(2)}'
            f"margin-bottom:5px;{match.group(3)}\">"
        ),
        rendered,
    )
    rendered = re.sub(
        r'<p style=" ([^"]*)margin-bottom:0px;([^"]*)">',
        r'<p style=" \1margin-bottom:6px;\2">',
        rendered,
    )
    rendered = re.sub(
        r'<li style=" ([^"]*)margin-bottom:0px;([^"]*)">',
        r'<li style=" \1margin-bottom:2px;\2">',
        rendered,
    )
    rendered = re.sub(
        r'<pre style=" ([^"]*)">',
        f'<table width="100%" cellspacing="0" cellpadding="6" bgcolor="{tokens.code_bg}"><tr><td><pre style=" color:{tokens.code_fg}; margin-top:0px; margin-bottom:0px; \\1">',
        rendered,
    )
    rendered = rendered.replace("</pre>", "</pre></td></tr></table>")
    rendered = rendered.replace(
        "</head>",
        f"<style>a {{ color: {tokens.primary}; }} code {{ color: {tokens.code_fg}; }}</style></head>",
    )
    return rendered


def message_card_kind(message: dict[str, Any]) -> str:
    """Return the explicit structured-card kind carried by server metadata."""
    card = message.get("card")
    if not isinstance(card, dict):
        return ""
    kind = str(card.get("kind") or "")
    return kind if kind in {"summary", "todo"} else ""


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


class MentionTextEdit(QTextEdit):
    """Composer that keeps stable mention IDs in QTextDocument character metadata."""

    mention_query = Signal(str)
    mention_closed = Signal()
    mention_key = Signal(int)
    send_requested = Signal()
    composition_state_changed = Signal(bool)
    MENTION_PROPERTY = int(QTextFormat.Property.UserProperty) + 1
    MENTION_MIME = "application/x-chatroom-mentions+json"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._trigger_start: int | None = None
        self._composing = False
        self.mention_popup_active = False
        self.textChanged.connect(lambda: QTimer.singleShot(0, self._update_mention_query))
        self.cursorPositionChanged.connect(self._update_mention_query)
        manager = active_theme_manager()
        if manager:
            manager.themeChanged.connect(self._theme_changed)

    def inputMethodEvent(self, event: Any) -> None:
        was_composing = self._composing
        self._composing = bool(event.preeditString())
        if self._composing:
            self.mention_closed.emit()
        elif event.commitString() and self.textCursor().hasSelection():
            self.setTextCursor(self._expanded_selection(self.textCursor()))
        super().inputMethodEvent(event)
        if was_composing != self._composing:
            self.composition_state_changed.emit(self._composing)
        if not self._composing:
            QTimer.singleShot(0, self._update_mention_query)

    @property
    def is_composing(self) -> bool:
        return self._composing

    def set_content(self, text: str, mentions: Any = None) -> None:
        """Replace the document while preserving styled mention fragments."""
        self._trigger_start = None
        self.clear()
        if not text:
            return
        if not mentions:
            self.setPlainText(text)
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cursor)
            return
        payload = json.dumps(
            {"text": text, "mentions": list(mentions)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        mime = QMimeData()
        mime.setData(self.MENTION_MIME, QByteArray(payload))
        self.insertFromMimeData(mime)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.mention_popup_active and not self._composing:
            key_map = {
                Qt.Key.Key_Up: -1,
                Qt.Key.Key_Down: 1,
                Qt.Key.Key_Escape: 2,
            }
            if event.modifiers() == Qt.KeyboardModifier.NoModifier:
                key_map[Qt.Key.Key_Return] = 0
                key_map[Qt.Key.Key_Enter] = 0
            if event.key() in key_map:
                self.mention_key.emit(key_map[event.key()])
                event.accept()
                return
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and not self._composing
        ):
            enter_to_send = QSettings().value("chat/enter_to_send", True, type=bool)
            modifiers = event.modifiers()
            if (
                (enter_to_send and modifiers == Qt.KeyboardModifier.NoModifier)
                or (not enter_to_send and modifiers in {
                    Qt.KeyboardModifier.ControlModifier,
                    Qt.KeyboardModifier.MetaModifier,
                })
            ):
                self.send_requested.emit()
                event.accept()
                return
        cursor = self.textCursor()
        if event.key() in {Qt.Key.Key_Backspace, Qt.Key.Key_Delete}:
            if cursor.hasSelection():
                self.setTextCursor(self._expanded_selection(cursor))
            else:
                target = cursor.position() - 1 if event.key() == Qt.Key.Key_Backspace else cursor.position()
                mention_range = self._mention_range_at(target)
                if mention_range:
                    cursor.setPosition(mention_range[0])
                    cursor.setPosition(mention_range[1], QTextCursor.MoveMode.KeepAnchor)
                    cursor.removeSelectedText()
                    self.setTextCursor(cursor)
                    event.accept()
                    QTimer.singleShot(0, self._update_mention_query)
                    return
        elif cursor.hasSelection() and event.text():
            self.setTextCursor(self._expanded_selection(cursor))
        super().keyPressEvent(event)
        QTimer.singleShot(0, self._update_mention_query)

    def _update_mention_query(self) -> None:
        if self._composing:
            return
        cursor = self.textCursor()
        before = self.toPlainText()[: self._utf16_to_py_index(self.toPlainText(), cursor.position())]
        match = re.search(r"@([^\s@]{0,30})$", before)
        if not match:
            self._trigger_start = None
            self.mention_closed.emit()
            return
        self._trigger_start = cursor.position() - len(match.group(1).encode("utf-16-le")) // 2 - 1
        if self._mention_range_at(self._trigger_start):
            self._trigger_start = None
            self.mention_closed.emit()
            return
        self.mention_query.emit(match.group(1))

    def insert_mention(self, member: dict[str, Any]) -> None:
        if self._trigger_start is None:
            return
        display = str(member.get("nickname") or member.get("display_name") or member.get("username", ""))
        metadata = json.dumps(
            {
                "member_id": str(member.get("member_id", member.get("id", ""))),
                "kind": str(member.get("kind", "user")),
                "display": display,
            },
            ensure_ascii=False,
        )
        cursor = self.textCursor()
        cursor.setPosition(self._trigger_start)
        cursor.setPosition(self.textCursor().position(), QTextCursor.MoveMode.KeepAnchor)
        mention_format = self._mention_format(metadata, str(member.get("kind", "user")))
        cursor.insertText(f"@{display}", mention_format)
        cursor.insertText(" ", QTextCharFormat())
        self.setTextCursor(cursor)
        self._trigger_start = None
        self.mention_closed.emit()

    def createMimeDataFromSelection(self) -> QMimeData:
        mime = super().createMimeDataFromSelection()
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return mime
        text = self.toPlainText()
        start = self._utf16_to_py_index(text, cursor.selectionStart())
        end = self._utf16_to_py_index(text, cursor.selectionEnd())
        selected_mentions = []
        for mention in self.mentions():
            mention_end = int(mention["start"]) + int(mention["length"])
            if int(mention["start"]) >= start and mention_end <= end:
                selected_mentions.append({**mention, "start": int(mention["start"]) - start})
        payload = json.dumps(
            {"text": text[start:end], "mentions": selected_mentions},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        mime.setData(self.MENTION_MIME, QByteArray(payload))
        return mime

    def insertFromMimeData(self, source: QMimeData) -> None:
        if not source.hasFormat(self.MENTION_MIME):
            super().insertFromMimeData(source)
            return
        try:
            payload = json.loads(bytes(source.data(self.MENTION_MIME)).decode("utf-8"))
            text = str(payload["text"])
            mentions = payload.get("mentions", [])
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            super().insertFromMimeData(source)
            return
        cursor = self._expanded_selection(self.textCursor())
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        offset = 0
        for mention in sorted(mentions, key=lambda item: int(item.get("start", -1))):
            start = int(mention.get("start", -1))
            length = int(mention.get("length", 0))
            token = text[start:start + length] if start >= 0 else ""
            if start < offset or not token or token != f"@{mention.get('display', '')}":
                continue
            cursor.insertText(text[offset:start], QTextCharFormat())
            metadata = json.dumps(
                {key: mention.get(key) for key in ("member_id", "kind", "display")},
                ensure_ascii=False,
            )
            cursor.insertText(token, self._mention_format(metadata, str(mention.get("kind", "user"))))
            offset = start + length
        cursor.insertText(text[offset:], QTextCharFormat())
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        QTimer.singleShot(0, self._update_mention_query)

    def cut(self) -> None:
        if self.textCursor().hasSelection():
            self.setTextCursor(self._expanded_selection(self.textCursor()))
        super().cut()

    def mentions(self) -> list[dict[str, Any]]:
        text = self.toPlainText()
        results: list[dict[str, Any]] = []
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    raw = fragment.charFormat().property(self.MENTION_PROPERTY)
                    if raw:
                        try:
                            metadata = json.loads(str(raw))
                        except (TypeError, json.JSONDecodeError):
                            metadata = None
                        if isinstance(metadata, dict):
                            start = self._utf16_to_py_index(text, fragment.position())
                            end = self._utf16_to_py_index(text, fragment.position() + fragment.length())
                            token = text[start:end]
                            expected = f"@{metadata.get('display', '')}"
                            if token == expected:
                                results.append({**metadata, "start": start, "length": len(token)})
                iterator += 1
            block = block.next()
        return results

    def _mention_format(self, metadata: str, kind: str) -> QTextCharFormat:
        tokens = current_tokens()
        mention_format = QTextCharFormat()
        mention_format.setForeground(QColor(tokens.mention_agent_fg if kind == "agent" else tokens.mention_user_fg))
        mention_format.setBackground(QColor(tokens.mention_agent_bg if kind == "agent" else tokens.mention_user_bg))
        mention_format.setFontWeight(QFont.Weight.DemiBold)
        mention_format.setProperty(self.MENTION_PROPERTY, metadata)
        return mention_format

    def _theme_changed(self, _snapshot: ThemeSnapshot) -> None:
        active = self.textCursor()
        position, anchor = active.position(), active.anchor()
        fragments: list[tuple[int, int, str, str]] = []
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                raw = fragment.charFormat().property(self.MENTION_PROPERTY) if fragment.isValid() else None
                if raw:
                    try:
                        metadata = json.loads(str(raw))
                    except (TypeError, json.JSONDecodeError):
                        metadata = None
                    if isinstance(metadata, dict):
                        fragments.append(
                            (fragment.position(), fragment.position() + fragment.length(), str(raw), str(metadata.get("kind", "user")))
                        )
                iterator += 1
            block = block.next()
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for start, end, metadata, kind in fragments:
            edit.setPosition(start)
            edit.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            edit.mergeCharFormat(self._mention_format(metadata, kind))
        edit.endEditBlock()
        restored = self.textCursor()
        restored.setPosition(anchor)
        restored.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(restored)

    def _mention_fragments(self) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().property(self.MENTION_PROPERTY):
                    ranges.append((fragment.position(), fragment.position() + fragment.length()))
                iterator += 1
            block = block.next()
        return ranges

    def _mention_range_at(self, position: int) -> tuple[int, int] | None:
        if position < 0:
            return None
        return next((item for item in self._mention_fragments() if item[0] <= position < item[1]), None)

    def _expanded_selection(self, cursor: QTextCursor) -> QTextCursor:
        if not cursor.hasSelection():
            return cursor
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        for mention_start, mention_end in self._mention_fragments():
            if start < mention_end and end > mention_start:
                start = min(start, mention_start)
                end = max(end, mention_end)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        return cursor

    @staticmethod
    def _utf16_to_py_index(text: str, position: int) -> int:
        units = 0
        for index, character in enumerate(text):
            if units >= position:
                return index
            units += len(character.encode("utf-16-le")) // 2
        return len(text)


class ConversationCard(QFrame):
    activated = Signal(object)

    def __init__(self, summary: ConversationSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = summary
        self._draft_preview = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("ConversationCard")
        self.setProperty("selected", False)
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)
        self._avatar = Avatar(summary.title, 36, group=summary.key.kind == "group")
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
        self._clock.setFixedWidth(42)
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
        available = max(0, self.width() - 20 - 36 - 10 - 42 - 6 - 2)
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
            self._preview.setText(f"[草稿] {self._draft_preview}")
            self._preview.setAccessibleName("未发送草稿")
            style_name = "DraftPreview"
        else:
            self._preview.setText(self._default_preview())
            self._preview.setAccessibleName("")
            style_name = "Muted"
        if self._preview.objectName() != style_name:
            self._preview.setObjectName(style_name)
            self._preview.style().unpolish(self._preview)
            self._preview.style().polish(self._preview)
            self._preview.update()

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


class MediaLabel(QLabel):
    """Keyboard-accessible media target with a stable Qt activation signal."""

    activated = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.activated.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MessageBubble(QFrame):
    recall_requested = Signal(int)
    reply_requested = Signal(int)
    edit_requested = Signal(int)
    quote_requested = Signal(int)
    media_requested = Signal(int)
    sources_requested = Signal(list)
    todo_import_requested = Signal(int, list)
    todo_open_requested = Signal(int)

    def __init__(
        self,
        message: dict[str, Any],
        *,
        mine: bool,
        can_recall: bool = False,
        density: str = "default",
    ) -> None:
        super().__init__()
        self.message = message
        self.mine = mine
        self.can_recall = can_recall
        self.setObjectName("MessageBubble")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.message_type = str(message.get("message_type") or "text")
        self.setProperty("messageType", self.message_type)
        self.card_kind = "" if message.get("recalled") else message_card_kind(message)
        if self.card_kind:
            self.setProperty("messageType", self.card_kind)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.agent = (
            self.message_type == "agent"
            or message.get("sender_kind") == "agent"
            or str(message.get("sender_name", "")).lower() in {"agent", "ai agent"}
            or bool(self.card_kind)
        )
        self._raw_content = str(message.get("content", message.get("text", "")))
        self._content_format = str(message.get("content_format", "plain"))
        self.setProperty(
            "messageRole",
            "card" if self.card_kind else "agent" if self.agent else "outgoing" if mine else "incoming",
        )
        self.setMaximumWidth(620 if self.agent else 540)
        if self.agent and "```" in self._raw_content and not self.card_kind:
            self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        if self.message_type == "sticker" or self.card_kind:
            layout.setContentsMargins(0, 0, 0, 0)
        elif density == "compact":
            layout.setContentsMargins(
                9 if self.agent else 10,
                5 if self.agent else 6,
                9 if self.agent else 10,
                5 if self.agent else 6,
            )
        else:
            layout.setContentsMargins(
                11 if self.agent else 12,
                8 if self.agent else 9,
                11 if self.agent else 12,
                7 if self.agent else 9,
            )
        layout.setSpacing(2 if density == "compact" else 4)
        if message.get("reply_to_id"):
            quote = QPushButton(str(message.get("reply_preview") or "点击查看原消息")[:80])
            quote.setObjectName("QuotedMessage")
            quote.setAccessibleName("定位引用消息")
            quote.clicked.connect(lambda: self.quote_requested.emit(int(message["reply_to_id"])))
            layout.addWidget(quote)
        if message.get("recalled"):
            renderer = self._build_recalled_content
        elif self.card_kind:
            renderer = self._build_agent_card
        else:
            renderer = {
                "image": lambda: self._build_media_content(is_sticker=False),
                "sticker": lambda: self._build_media_content(is_sticker=True),
                "text": self._build_text_content,
                "agent": self._build_text_content,
            }.get(self.message_type, self._build_text_content)
        layout.addWidget(renderer())
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        for child in self.findChildren(QWidget):
            if not isinstance(child, (QLabel, QPushButton)):
                continue
            child.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            child.customContextMenuRequested.connect(
                lambda point, widget=child: self._menu(self.mapFromGlobal(widget.mapToGlobal(point)))
            )
        manager = active_theme_manager()
        if manager:
            manager.themeChanged.connect(self._theme_changed)

    def _build_agent_card(self) -> QWidget:
        card = self.message.get("card")
        if not isinstance(card, dict):
            return self._build_text_content()
        widget = SummaryCard(card) if self.card_kind == "summary" else TodoCard(card)
        widget.sources_requested.connect(self.sources_requested.emit)
        if isinstance(widget, TodoCard):
            widget.import_requested.connect(self.todo_import_requested.emit)
            widget.todo_requested.connect(self.todo_open_requested.emit)
        self.agent_card = widget
        return widget

    def _build_recalled_content(self) -> QLabel:
        content = QLabel("消息已撤回")
        content.setObjectName("RecalledMessage")
        return content

    def _build_text_content(self) -> QLabel:
        content = QLabel()
        self.content_label = content
        self._render_text_content()
        content.setWordWrap(True)
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding
            if self.agent and self._content_format == "markdown"
            else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        content.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        return content

    def _build_media_content(self, *, is_sticker: bool) -> QLabel:
        image = MediaLabel()
        image.setObjectName("MessageSticker" if is_sticker else "MessageImage")
        image.setAccessibleName("预览表情包" if is_sticker else "预览图片")
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        raw = self.message.get("image_data")
        if raw:
            try:
                data = base64.b64decode(str(raw), validate=True)
                decoded = QImage.fromData(data)
                if decoded.isNull():
                    self._set_media_retry(image)
                else:
                    limit = 180 if is_sticker else 320
                    size = decoded.size().scaled(
                        limit,
                        limit if is_sticker else 240,
                        Qt.AspectRatioMode.KeepAspectRatio,
                    )
                    if self.message.get("image_mime") == "image/gif":
                        self._media_buffer = QBuffer(self)
                        self._media_buffer.setData(QByteArray(data))
                        self._media_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                        self._movie = QMovie(self._media_buffer, b"gif", self)
                        self._movie.setScaledSize(size)
                        image.setMovie(self._movie)
                        self._movie.start()
                    else:
                        image.setPixmap(
                            QPixmap.fromImage(decoded).scaled(
                                size,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        )
                    image.setCursor(Qt.CursorShape.PointingHandCursor)
                    image.activated.connect(
                        lambda value=data: self._preview_media(value)
                    )
            except (ValueError, TypeError):
                self._set_media_retry(image)
        else:
            label = (
                self.message.get("sticker_name")
                if is_sticker
                else self.message.get("image_name")
            )
            image.setText(
                "媒体加载失败，点击重试"
                if self.message.get("image_missing")
                else f"{label or ('表情包' if is_sticker else '图片')} · 点击加载"
            )
            image.setCursor(Qt.CursorShape.PointingHandCursor)
            image.activated.connect(self._request_media)
        return image

    def _set_media_retry(self, image: MediaLabel) -> None:
        image.setText("媒体加载失败，点击重试")
        image.setCursor(Qt.CursorShape.PointingHandCursor)
        image.activated.connect(self._request_media)

    def _render_text_content(self) -> None:
        content = getattr(self, "content_label", None)
        if content is None:
            return
        if self._content_format == "markdown":
            content.setObjectName("AgentMarkdownContent" if self.agent else "MarkdownMessageContent")
            content.setTextFormat(Qt.TextFormat.RichText)
            content.setOpenExternalLinks(False)
            rendered = markdown_to_safe_html(self._raw_content)
            content.setText(rendered)
            if self.minimumWidth() > 0:
                measure = QTextDocument()
                measure.setDocumentMargin(0)
                measure.setHtml(rendered)
                margins = self.layout().contentsMargins()
                measure.setTextWidth(self.minimumWidth() - margins.left() - margins.right())
                content.setMinimumHeight(math.ceil(measure.size().height()) + 18)
            return
        content.setObjectName("PlainMessageContent")
        mentions = self.message.get("mentions")
        if isinstance(mentions, list) and mentions:
            content.setTextFormat(Qt.TextFormat.RichText)
            content.setOpenExternalLinks(False)
            content.setText(mentions_to_safe_html(self._raw_content, mentions))
        else:
            content.setTextFormat(Qt.TextFormat.PlainText)
            content.setText(self._raw_content)

    def _theme_changed(self, _snapshot: ThemeSnapshot) -> None:
        self._render_text_content()
        self.update()

    def _request_media(self) -> None:
        message_id = self.message.get("id", self.message.get("message_id"))
        if message_id is not None:
            self.media_requested.emit(int(message_id))

    def _preview_media(self, raw: bytes) -> None:
        self._create_media_preview(raw).exec()

    def _create_media_preview(self, raw: bytes) -> QDialog:
        dialog = QDialog(self)
        dialog.setObjectName("MediaPreview")
        dialog.setWindowTitle(str(self.message.get("sticker_name") or self.message.get("image_name") or "媒体预览"))
        preview = QLabel(dialog)
        preview.setObjectName("MediaPreviewContent")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(dialog)
        layout.addWidget(preview)
        image = QImage.fromData(raw)
        if self.message.get("image_mime") == "image/gif":
            buffer = QBuffer(dialog)
            buffer.setData(QByteArray(raw))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            movie = QMovie(buffer, b"gif", dialog)
            movie.setScaledSize(image.size().scaled(480, 400, Qt.AspectRatioMode.KeepAspectRatio))
            preview.setMovie(movie)
            movie.start()
            dialog._media_buffer = buffer  # type: ignore[attr-defined]
            dialog._media_movie = movie  # type: ignore[attr-defined]
        else:
            preview.setPixmap(QPixmap.fromImage(image).scaled(480, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        close_button = QPushButton("关闭")
        close_button.setObjectName("Ghost")
        close_button.setAccessibleName("关闭媒体预览")
        close_button.clicked.connect(dialog.accept)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(close_button)
        layout.addLayout(actions)
        return dialog

    def _menu(self, point: Any) -> None:
        menu = self._create_menu()
        selected = menu.exec(self.mapToGlobal(point))
        if selected is None:
            return
        message_id = int(self.message.get("id", self.message.get("message_id", 0)) or 0)
        if selected.text() == "引用回复":
            self.reply_requested.emit(message_id)
        elif selected.text() == "定位引用消息":
            self.quote_requested.emit(int(self.message["reply_to_id"]))
        elif selected.text() == "加载媒体":
            self._request_media()
        elif selected.text() == "预览媒体":
            try:
                self._preview_media(base64.b64decode(str(self.message.get("image_data") or ""), validate=True))
            except (ValueError, TypeError):
                self._request_media()
        elif selected and selected.text() == "编辑消息":
            self.edit_requested.emit(message_id)
        elif selected and selected.text() == "撤回消息":
            self.recall_requested.emit(message_id)

    def _create_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setObjectName("MessageActionsMenu")
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        message_id = self.message.get("id", self.message.get("message_id"))
        if not message_id:
            return menu
        if not self.message.get("recalled"):
            menu.addAction("引用回复")
            if self.message.get("reply_to_id"):
                menu.addAction("定位引用消息")
            if self.message.get("message_type") in {"image", "sticker"}:
                menu.addAction("预览媒体" if self.message.get("image_data") else "加载媒体")
        if self.mine and self.message.get("message_type") == "text" and not self.message.get("recalled"):
            menu.addAction("编辑消息")
        if self.can_recall and not self.message.get("recalled"):
            menu.addAction("撤回消息")
        return menu


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
        icon_text: str = "💬",
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

