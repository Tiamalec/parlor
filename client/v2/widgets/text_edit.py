"""Mention-aware text editor."""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import QByteArray, QMimeData, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QTextEdit, QWidget
from ..theme import ThemeSnapshot, active_theme_manager, current_tokens
import json
import re


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
