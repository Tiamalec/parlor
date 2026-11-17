"""Reusable widgets for the Chatroom V2 interface."""

from __future__ import annotations

import base64
import html
import json
import math
import re
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QMimeData, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from client.models import ConversationSummary
from client.profile import ProfileViewModel

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


def avatar_pixmap(name: str, size: int, image_data: str | None = None, *, agent: bool = False) -> QPixmap:
    tokens = current_tokens()
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    image = avatar_image(image_data)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(tokens.agent if agent else tokens.avatar_user))
    painter.drawRoundedRect(0, 0, size, size, size / 2, size / 2)
    if not image.isNull():
        scaled = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        clip = QPainterPath()
        clip.addEllipse(0, 0, size, size)
        painter.setClipPath(clip)
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
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


def agent_message_role(source: str) -> str:
    if "待办提取" in source or "明确任务" in source:
        return "todo"
    if "群聊总结" in source or "定时总结" in source or "待确认事项" in source:
        return "summary"
    return "answer"


class Avatar(QLabel):
    def __init__(
        self, name: str = "", size: int = 38, *, agent: bool = False, image_data: str | None = None
    ) -> None:
        super().__init__()
        self._size = size
        self._agent = agent
        self._name = name
        self._image_data = image_data
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_avatar(name, image_data, agent=agent)
        manager = active_theme_manager()
        if manager:
            manager.themeChanged.connect(self._theme_changed)

    def set_avatar(self, name: str, image_data: str | None = None, *, agent: bool | None = None) -> None:
        if agent is not None:
            self._agent = agent
        self._name = name
        self._image_data = image_data
        self.setPixmap(avatar_pixmap(name, self._size, image_data, agent=self._agent))
        self.setToolTip(name)

    def _theme_changed(self, _snapshot: ThemeSnapshot) -> None:
        self.setPixmap(avatar_pixmap(self._name, self._size, self._image_data, agent=self._agent))


class UserAvatar(Avatar):
    """Identity-aware avatar that uses the shared profile and fetch policy."""

    activated = Signal(object, object)

    def __init__(self, service: "ChatService", profile: ProfileViewModel, size: int = 38, *, clickable: bool = False) -> None:
        self.service = service
        self.profile = profile
        self.clickable = clickable
        super().__init__(profile.display_name, size, agent=profile.kind == "agent")
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
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
            self.setToolTip(f"{self.profile.display_name}（头像暂时无法加载）")

    def mousePressEvent(self, event: Any) -> None:
        super().mousePressEvent(event)
        if self.clickable and event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.profile, self)


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
        self._composing = bool(event.preeditString())
        if self._composing:
            self.mention_closed.emit()
        elif event.commitString() and self.textCursor().hasSelection():
            self.setTextCursor(self._expanded_selection(self.textCursor()))
        super().inputMethodEvent(event)
        if not self._composing:
            QTimer.singleShot(0, self._update_mention_query)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.mention_popup_active and not self._composing:
            key_map = {
                Qt.Key.Key_Up: -1,
                Qt.Key.Key_Down: 1,
                Qt.Key.Key_Return: 0,
                Qt.Key.Key_Enter: 0,
                Qt.Key.Key_Escape: 2,
            }
            if event.key() in key_map:
                self.mention_key.emit(key_map[event.key()])
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
        match = re.search(r"(?:^|\s)@([^\s@]{0,30})$", before)
        if not match:
            self._trigger_start = None
            self.mention_closed.emit()
            return
        self._trigger_start = cursor.position() - len(match.group(1).encode("utf-16-le")) // 2 - 1
        self.mention_query.emit(match.group(1))

    def insert_mention(self, member: dict[str, Any]) -> None:
        if self._trigger_start is None:
            return
        display = str(member.get("username", ""))
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

    def __init__(self, summary: ConversationSummary) -> None:
        super().__init__()
        self.summary = summary
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("ConversationCard")
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 9, 10, 9)
        row.setSpacing(10)
        row.addWidget(Avatar(summary.title))
        text = QVBoxLayout()
        top = QHBoxLayout()
        title = QLabel(summary.title)
        title.setObjectName("ConversationTitle")
        top.addWidget(title)
        top.addStretch()
        clock = QLabel(time.strftime("%H:%M", time.localtime(summary.timestamp)) if summary.timestamp else "")
        clock.setObjectName("ConversationTime")
        top.addWidget(clock)
        text.addLayout(top)
        bottom = QHBoxLayout()
        preview = QLabel(summary.preview or ("群聊" if summary.key.kind == "group" else "开始聊天"))
        preview.setObjectName("Muted")
        preview.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        bottom.addWidget(preview, 1)
        if summary.unread_count:
            badge = QLabel(str(min(summary.unread_count, 99)))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(22, 22)
            badge.setObjectName("UnreadBadge")
            bottom.addWidget(badge)
        text.addLayout(bottom)
        row.addLayout(text, 1)

    def mousePressEvent(self, event: Any) -> None:
        super().mousePressEvent(event)
        self.activated.emit(self.summary.key)


class MessageBubble(QFrame):
    recall_requested = Signal(int)
    media_requested = Signal(int)

    def __init__(self, message: dict[str, Any], *, mine: bool) -> None:
        super().__init__()
        self.message = message
        self.mine = mine
        self.setObjectName("MessageBubble")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.agent = message.get("message_type") == "agent" or str(message.get("sender_name", "")).lower() == "agent"
        self._raw_content = str(message.get("content", message.get("text", "")))
        self._content_format = str(message.get("content_format", "plain"))
        self.setProperty("messageRole", "agent" if self.agent else "outgoing" if mine else "incoming")
        self.setMaximumWidth(620 if self.agent else 540)
        role = agent_message_role(self._raw_content) if self.agent else ""
        if self.agent and (role in {"summary", "todo"} or "```" in self._raw_content):
            self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11 if self.agent else 12, 8 if self.agent else 9, 11 if self.agent else 12, 7 if self.agent else 9)
        layout.setSpacing(4)
        sender = str(message.get("sender_name", message.get("sender", "未知用户")))
        if not mine or self.agent:
            role_label = {"summary": " · 总结", "todo": " · 待办", "answer": ""}.get(role, "")
            name = QLabel(f"AI Agent{role_label}" if self.agent else sender)
            name.setObjectName("AgentMessageSender" if self.agent else "MessageSender")
            layout.addWidget(name)
        if message.get("recalled"):
            content = QLabel("消息已撤回")
            content.setObjectName("RecalledMessage")
            layout.addWidget(content)
        elif message.get("message_type") == "image":
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            raw = message.get("image_data")
            if raw:
                try:
                    pixmap = QPixmap.fromImage(QImage.fromData(base64.b64decode(str(raw))))
                    image.setPixmap(pixmap.scaled(320, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                except (ValueError, TypeError):
                    image.setText("图片加载失败")
            else:
                image.setText(f"🖼  {message.get('image_name', '点击加载图片')}")
                image.setCursor(Qt.CursorShape.PointingHandCursor)
                image.mousePressEvent = lambda _event: self._request_media()
            layout.addWidget(image)
        else:
            content = QLabel()
            self.content_label = content
            self._render_text_content()
            content.setWordWrap(True)
            content.setSizePolicy(
                QSizePolicy.Policy.Expanding if self.agent and self._content_format == "markdown" else QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Preferred,
            )
            content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
            layout.addWidget(content)
        stamp = float(message.get("created_at", message.get("timestamp", 0)) or 0)
        if stamp > 10_000_000_000:
            stamp /= 1000
        footer = QLabel(time.strftime("%H:%M", time.localtime(stamp)) if stamp else "")
        footer.setAlignment(Qt.AlignmentFlag.AlignRight)
        footer.setObjectName("MessageTime")
        layout.addWidget(footer)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        manager = active_theme_manager()
        if manager:
            manager.themeChanged.connect(self._theme_changed)

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

    def _menu(self, point: Any) -> None:
        menu = QMenu(self)
        if self.mine and not self.message.get("recalled") and self.message.get("id", self.message.get("message_id")):
            recall = menu.addAction("撤回消息")
            selected = menu.exec(self.mapToGlobal(point))
            if selected == recall:
                self.recall_requested.emit(int(self.message.get("id", self.message.get("message_id"))))


class EmptyState(QWidget):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("💬")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setObjectName("EmptyStateIcon")
        heading = QLabel(title)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setObjectName("EmptyStateTitle")
        body = QLabel(description)
        body.setObjectName("Muted")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(icon)
        layout.addWidget(heading)
        layout.addWidget(body)

