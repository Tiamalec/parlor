"""Message timeline widgets."""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QImage, QKeyEvent, QMovie, QPixmap, QTextDocument
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from ..agent_cards import SummaryCard, TodoCard
from ..theme import ThemeSnapshot, active_theme_manager
import base64
from .rendering import markdown_to_safe_html, mentions_to_safe_html
import math
from urllib.parse import urlsplit


def message_card_kind(message: dict[str, Any]) -> str:
    """Return the explicit structured-card kind carried by server metadata."""
    card = message.get("card")
    if not isinstance(card, dict):
        return ""
    kind = str(card.get("kind") or "")
    return kind if kind in {"summary", "todo"} else ""

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
        if self.agent and self._content_format == "markdown":
            content.linkActivated.connect(self._open_source_link)
        return content

    @staticmethod
    def _open_source_link(value: str) -> None:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            QDesktopServices.openUrl(QUrl(value))

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
