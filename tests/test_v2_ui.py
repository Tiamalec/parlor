from __future__ import annotations

import os
import base64
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QPoint, QSize, Qt
    from PySide6.QtGui import QColor, QImage, QPalette
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QScrollArea, QToolButton, QWidget
except ImportError:  # pragma: no cover - optional until V2 dependencies are installed
    QApplication = None  # type: ignore[assignment]

from client.models import ConversationKey, ConversationSummary


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class V2UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()
        for widget in self.app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_agent_source_links_open_only_http_urls(self) -> None:
        from client.v2.widgets import MessageBubble, markdown_to_safe_html

        bubble = MessageBubble(
            {"message_type": "agent", "content_format": "markdown",
             "content": "来源：[官方资料](https://example.org/docs)"},
            mine=False,
        )
        self.assertIn('href="https://example.org/docs"', markdown_to_safe_html(bubble._raw_content))
        with patch("client.v2.widgets.message.QDesktopServices.openUrl") as opener:
            bubble.content_label.linkActivated.emit("https://example.org/docs")
            bubble.content_label.linkActivated.emit("file:///etc/passwd")
            bubble.content_label.linkActivated.emit("javascript:alert(1)")
            bubble.content_label.linkActivated.emit("https://[invalid")
        opener.assert_called_once()
        self.assertEqual(opener.call_args.args[0].toString(), "https://example.org/docs")

    def test_chat_quote_marker_and_search_navigation(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.widgets import MessageBubble

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.members_by_group["7"] = [{"id": 2, "member_id": "user:2", "kind": "user", "username": "bob"}]
        store.state.messages[key] = [
            {"id": 1, "conversation_type": "group", "conversation_id": "7", "sender_id": 2,
             "sender_name": "bob", "message_type": "text", "content": "original"},
            {"id": 2, "conversation_type": "group", "conversation_id": "7", "sender_id": 1,
             "sender_name": "alice", "message_type": "text", "content": "answer",
             "reply_to_id": 1, "reply_preview": "bob: original", "edited": True},
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(key, "群聊")
        pane.show()
        self.app.processEvents()
        bubbles = pane.findChildren(MessageBubble)
        self.assertEqual(len(bubbles), 2)
        self.assertTrue(any("已编辑" in label.text() for label in pane.findChildren(QLabel, "MessageTime")))
        self.assertIsNone(bubbles[1].findChild(QLabel, "MessageTime"))
        self.assertIsNone(bubbles[0].findChild(QLabel, "MessageSender"))
        quote = next(button for button in bubbles[1].findChildren(QPushButton) if button.accessibleName() == "定位引用消息")
        self.assertEqual(quote.text(), "bob: original")
        self.assertEqual([action.text() for action in bubbles[0]._create_menu().actions()], ["引用回复"])
        self.assertEqual(bubbles[0].content_label.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
        quote.click()
        self.app.processEvents()
        self.assertEqual(pane._focus_message_id, None)
        pane._reply_to(1)
        self.assertEqual(pane._reply_to_id, 1)
        self.assertEqual(pane.compose.mentions()[0]["member_id"], "user:2")
        store.state.search_results[key] = [store.state.messages[key][0]]
        pane.update_state("search", store.state)
        pane._open_search_result(pane.search_results.item(0))
        self.app.processEvents()
        self.assertIsNone(pane._focus_message_id)
        pane.service.load_result = Mock(return_value="history-request")
        pane._focus_quoted_message(44)
        pane.service.load_result.assert_called_once_with(key, 44)
        self.assertEqual(pane._focus_message_id, 44)
        store.handle_event({"type": "history.result", "payload": {"conversation_type": "group",
                            "conversation_id": "7", "messages": [{"id": 44, "conversation_type": "group",
                            "conversation_id": "7", "sender_id": 2, "sender_name": "bob",
                            "message_type": "text", "content": "older"}]}})
        pane.update_state("history", store.state)
        self.app.processEvents()
        self.assertIsNone(pane._focus_message_id)

    def test_empty_chat_state_uses_centered_line_icon(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.widgets import BrandLockup, EmptyState

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.resize(900, 620)
        pane.show()
        pane.render_messages(force=True)
        self.app.processEvents()
        empty = pane.findChild(EmptyState)
        self.assertIsNotNone(empty)
        assert empty is not None
        glyph = empty.findChild(BrandLockup)
        self.assertIsNotNone(glyph)
        assert glyph is not None
        self.assertEqual(glyph.wordmark.text(), "Parlor")
        self.assertFalse(glyph.icon_label.pixmap().isNull())
        self.assertFalse(any("💬" in label.text() for label in empty.findChildren(QLabel)))
        empty_center = empty.mapToGlobal(empty.rect().center())
        viewport = pane.scroll.viewport()
        viewport_center = viewport.mapToGlobal(viewport.rect().center())
        self.assertLessEqual(abs(empty_center.x() - viewport_center.x()), 3)
        self.assertLessEqual(abs(empty_center.y() - viewport_center.y()), 3)

    def test_private_receipt_typing_and_blocked_composer(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        store.state.capabilities["private_chat_v2"] = True
        key = ConversationKey("private", "dm:1:2")
        store.state.conversations = [ConversationSummary(key, "bob")]
        store.state.messages[key] = [{
            "id": 9, "conversation_type": "private", "conversation_id": key.identifier,
            "sender_id": 1, "sender_name": "alice", "message_type": "text",
            "content": "hello", "created_at": 1000,
        }]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(key, "bob")
        pane.show()
        self.app.processEvents()
        self.assertTrue(any("已发送" in label.text() for label in pane.findChildren(QLabel, "MessageTime")))
        store.handle_event({"type": "private.read", "payload": {
            "conversation_id": key.identifier, "reader_id": 2, "message_id": 9,
        }})
        pane.update_state("private_read", store.state)
        self.app.processEvents()
        self.assertTrue(any("已读" in label.text() for label in pane.findChildren(QLabel, "MessageTime")))
        store.handle_event({"type": "private.typing", "payload": {
            "conversation_id": key.identifier, "user_id": 2, "active": True,
        }})
        pane.update_state("private_typing", store.state)
        self.assertIn("对方正在输入", pane.subtitle.text())
        store.handle_event({"type": "private.state", "payload": {
            "conversation_id": key.identifier, "can_send": False, "blocked_by_me": False,
        }})
        pane.update_state("conversations", store.state)
        self.assertFalse(pane.compose.isEnabled())
        self.assertFalse(pane.send.isEnabled())
        self.assertIn("不可发送", pane.subtitle.text())
        pane._peer_typing_timer.stop()

    def test_private_read_waits_until_latest_message_is_viewed(self) -> None:
        from client.network import NetworkClient
        from client.v2.application import MainWindow

        with patch.object(NetworkClient, "start"):
            window = MainWindow("127.0.0.1", 9)
        window.poll.stop()
        window.show()
        window.isActiveWindow = Mock(return_value=True)
        state = window.store.state
        state.authenticated = True
        state.current_user = {"id": 1}
        key = ConversationKey("private", "dm:1:2")
        state.current_conversation = key
        state.messages[key] = [
            {"id": index, "conversation_type": "private", "conversation_id": key.identifier,
             "sender_id": 2, "sender_name": "bob", "message_type": "text", "content": "long message " * 6}
            for index in range(1, 81)
        ]
        window.stack.setCurrentWidget(window.workspace)
        window.workspace.chat.set_conversation(key, "bob")
        self.app.processEvents()
        self.app.processEvents()
        scrollbar = window.workspace.chat.scroll.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        window.service.request = Mock(return_value="read-1")
        scrollbar.setValue(0)
        window._mark_conversation_read(state)
        window.service.request.assert_not_called()
        scrollbar.setValue(scrollbar.maximum())
        window._mark_conversation_read(state)
        window.service.request.assert_called_once()
        self.assertEqual(window.service.request.call_args.args[0], "history.read")
        window.close()

    def test_group_message_metadata_is_outside_bubble_and_consecutive_sender_is_grouped(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.widgets import MessageBubble

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.members_by_group["7"] = [
            {"id": 2, "member_id": "user:2", "kind": "user", "username": "bob"},
            {"id": 3, "member_id": "user:3", "kind": "user", "username": "carol"},
        ]
        store.state.messages[key] = [
            {"id": 1, "sender_id": 2, "sender_name": "bob", "message_type": "text",
             "content": "第一条", "created_at": 1000},
            {"id": 2, "sender_id": 2, "sender_name": "bob", "message_type": "text",
             "content": "连续一条", "created_at": 1040},
            {"id": 3, "sender_id": 3, "sender_name": "carol", "message_type": "text",
             "content": "换人", "created_at": 1050},
        ]

        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(key, "群聊")
        pane.show()
        self.app.processEvents()

        bubbles = pane.findChildren(MessageBubble)
        self.assertEqual(len(bubbles), 3)
        self.assertTrue(all(bubble.findChild(QLabel, "MessageSender") is None for bubble in bubbles))
        sender_labels = pane.findChildren(QLabel, "MessageSender")
        self.assertEqual([label.text() for label in sender_labels], ["bob", "carol"])
        rows = pane.findChildren(QWidget, "MessageRow")
        self.assertEqual([bool(row.property("grouped")) for row in rows], [False, True, False])
        self.assertEqual(len(rows[0].findChildren(QLabel, "MessageTime")), 1)
        self.assertEqual(len(rows[1].findChildren(QLabel, "MessageTime")), 1)

    def test_switching_long_group_history_keeps_rows_unclipped(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.widgets import MessageBubble, UserAvatar

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        short = ConversationKey("group", "short")
        long = ConversationKey("group", "long")
        store.state.messages[short] = []
        store.state.messages[long] = [
            {"id": index, "sender_id": index % 3 + 1, "sender_name": "member",
             "message_type": "text", "content": "群聊消息 " * 12,
             "created_at": 1000 + index * 300}
            for index in range(1, 81)
        ]
        store.state.messages[long].append({
            "id": 81, "sender_id": None, "sender_name": "AI Agent",
            "sender_kind": "agent", "message_type": "agent", "content": "总结",
            "card": {"kind": "summary", "title": "群聊总结", "parse_error": True},
            "created_at": 26000,
        })
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.resize(760, 620)
        pane.show()
        for key in (short, long, short, long):
            pane.set_conversation(key, "群聊")
            self.app.processEvents()

        rows = pane.findChildren(QWidget, "MessageRow")
        self.assertEqual(len(rows), 81)
        self.assertGreater(pane.scroll.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(pane.message_content.height(), pane.messages.minimumSize().height())
        for row in rows:
            bubble = row.findChild(MessageBubble)
            avatar = row.findChild(UserAvatar)
            self.assertIsNotNone(bubble)
            self.assertIsNotNone(avatar)
            self.assertGreaterEqual(row.height(), row.minimumHeight())
            self.assertGreaterEqual(row.height(), bubble.height())
            self.assertGreaterEqual(row.height(), avatar.height())

    def test_sticker_uses_standalone_renderer_not_text_bubble(self) -> None:
        from client.v2.widgets import MessageBubble

        sticker = MessageBubble(
            {
                "id": 4,
                "message_type": "sticker",
                "sticker_name": "开心",
                "image_mime": "image/png",
                "image_data": base64.b64encode(
                    base64.b64decode(
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                    )
                ).decode("ascii"),
            },
            mine=False,
        )
        self.assertEqual(sticker.property("messageType"), "sticker")
        self.assertIsNotNone(sticker.findChild(QLabel, "MessageSticker"))
        self.assertIsNone(sticker.findChild(QLabel, "PlainMessageContent"))
        self.assertEqual(sticker.layout().contentsMargins().left(), 0)

    def test_media_click_opens_preview_or_requests_missing_data(self) -> None:
        from PySide6.QtTest import QTest

        from client.v2.widgets import MessageBubble

        encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        for message_type, object_name in (("image", "MessageImage"), ("sticker", "MessageSticker")):
            bubble = MessageBubble(
                {
                    "id": 10,
                    "message_type": message_type,
                    "image_mime": "image/png",
                    "image_data": encoded,
                },
                mine=False,
            )
            bubble._preview_media = Mock()
            bubble.show()
            self.app.processEvents()
            QTest.mouseClick(
                bubble.findChild(QLabel, object_name), Qt.MouseButton.LeftButton
            )
            bubble._preview_media.assert_called_once()

        preview = bubble._create_media_preview(base64.b64decode(encoded))
        close_preview = next(
            button
            for button in preview.findChildren(QPushButton)
            if button.accessibleName() == "关闭媒体预览"
        )
        preview.show()
        self.app.processEvents()
        close_preview.click()
        self.assertFalse(preview.isVisible())

        missing = MessageBubble(
            {"id": 11, "message_type": "image", "image_name": "missing.png"},
            mine=False,
        )
        requested: list[int] = []
        missing.media_requested.connect(requested.append)
        missing.show()
        self.app.processEvents()
        QTest.mouseClick(
            missing.findChild(QLabel, "MessageImage"), Qt.MouseButton.LeftButton
        )
        self.assertEqual(requested, [11])

    def test_sticker_panel_exposes_recent_favorites_imported_and_send(self) -> None:
        import tempfile
        from pathlib import Path

        from client.v2.stickers import LocalStickerLibrary, StickerPanel

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "happy.png"
            source.write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
            )
            library = LocalStickerLibrary(Path(temp) / "library")
            sticker = library.import_file(source)
            library.set_favorite(sticker["id"], True)
            library.mark_used(sticker["id"])
            panel = StickerPanel(library)

            self.assertEqual(
                [panel.tabs.tabText(index) for index in range(panel.tabs.count())],
                ["最近使用", "收藏/默认", "我的表情"],
            )
            self.assertEqual(panel.lists["recent"].count(), 1)
            self.assertEqual(panel.lists["favorites"].count(), 1)
            self.assertEqual(panel.lists["mine"].count(), 1)
            self.assertEqual(panel.close_button.text(), "关闭")
            panel.show()
            self.app.processEvents()
            panel.close_button.click()
            self.assertFalse(panel.isVisible())
            selected: list[dict] = []
            panel.sticker_selected.connect(selected.append)
            panel._emit_sticker(sticker)
            self.assertEqual(selected[0]["id"], sticker["id"])

    def test_chat_pane_sends_imported_sticker_and_records_recent_use(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from PySide6.QtTest import QTest

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.stickers import LocalStickerLibrary

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "happy.png"
            source.write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
            )
            library = LocalStickerLibrary(Path(temp) / "library")
            sticker = library.import_file(source)
            store = AppStore()
            store.state.current_user = {"id": 1}
            service = ChatService(NetworkClient("127.0.0.1", 9), store)
            service.send_sticker = Mock(return_value="sticker-request")
            pane = ChatPane(service, store)
            pane.key = ConversationKey("group", "7")
            pane._sticker_library = library
            pane._reply_to_id = 12

            pane.show()
            self.app.processEvents()
            QTest.mouseClick(pane.sticker, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertIsNotNone(pane._sticker_panel)
            self.assertTrue(pane._sticker_panel.isVisible())
            pane._sticker_panel.close_button.click()
            with patch(
                "client.v2.pages.chat_page.QFileDialog.getOpenFileName",
                return_value=("", ""),
            ) as chooser:
                QTest.mouseClick(pane.image, Qt.MouseButton.LeftButton)
                chooser.assert_called_once()

            pane._send_sticker(sticker)

            service.send_sticker.assert_called_once()
            args = service.send_sticker.call_args.args
            self.assertEqual(args[0], pane.key)
            self.assertEqual(args[1:3], (sticker["id"], "happy"))
            self.assertEqual(args[-1], 12)
            self.assertEqual(library.list("recent")[0]["id"], sticker["id"])
            self.assertIsNone(pane._reply_to_id)

    def test_historical_image_loads_after_reopening_chat(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.widgets import MessageBubble

        store = AppStore()
        store.state.connected = True
        store.state.authenticated = True
        store.state.current_user = {"id": 1}
        key = ConversationKey("group", "7")
        store.state.messages[key] = [{"id": 8, "conversation_type": "group", "conversation_id": "7",
                                     "sender_id": 2, "sender_name": "bob", "message_type": "image",
                                     "image_name": "photo.png", "event_id": 10}]
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        service.fetch_media = Mock(return_value="media-request")
        pane = ChatPane(service, store)
        pane.set_conversation(key, "群聊")
        pane.show()
        self.app.processEvents()
        service.fetch_media.assert_called_once_with(8)
        from PySide6.QtCore import QBuffer, QIODevice

        pixels = QImage(2, 2, QImage.Format.Format_ARGB32)
        pixels.fill(Qt.GlobalColor.blue)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(pixels.save(buffer, "PNG"))
        image_data = base64.b64encode(bytes(buffer.data())).decode("ascii")
        store.handle_event({"type": "media.result", "payload": {"message": {"id": 8,
                            "conversation_type": "group", "conversation_id": "7", "image_data": image_data}}})
        pane.update_state("media", store.state)
        image = pane.findChild(MessageBubble).findChild(QLabel, "MessageImage")
        self.assertIsNotNone(image.pixmap())
        self.assertFalse(image.pixmap().isNull())

    def test_chat_follows_new_messages_and_badges_history_reading(self) -> None:
        from PySide6.QtTest import QTest

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.messages[key] = [
            {"id": message_id, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text",
             "content": f"message {message_id}"}
            for message_id in range(1, 31)
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.resize(640, 360)
        pane.show()
        self.app.processEvents()
        pane.set_conversation(key, "群聊")
        self.app.processEvents()
        QTest.qWait(10)
        self.app.processEvents()

        bar = pane.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(bar.value(), bar.maximum())

        def add_message(message_id: int, sender_id: int = 2) -> None:
            store.state.messages[key].append(
                {"id": message_id, "conversation_type": "group", "conversation_id": "7",
                 "sender_id": sender_id, "sender_name": "bob" if sender_id == 2 else "alice",
                 "message_type": "text", "content": f"message {message_id}"}
            )
            pane.update_state("messages", store.state)
            self.app.processEvents()
            QTest.qWait(10)
            self.app.processEvents()

        bar.setValue(0)
        self.app.processEvents()
        add_message(31)
        self.assertTrue(pane.new_messages_badge.isVisible())
        self.assertEqual(pane.new_messages_badge.text(), "1 条新消息")
        self.assertFalse(pane.new_messages_badge.icon().isNull())
        self.assertGreater(pane.new_messages_badge.x(), pane.scroll.width() // 2)
        self.assertGreaterEqual(
            pane.new_messages_badge.y() + pane.new_messages_badge.height(),
            pane.scroll.height() - 20,
        )
        self.assertEqual(bar.value(), 0)

        add_message(32)
        self.assertEqual(pane.new_messages_badge.text(), "2 条新消息")
        self.assertEqual(bar.value(), 0)

        pane.new_messages_badge.click()
        self.app.processEvents()
        QTest.qWait(300)
        self.app.processEvents()
        self.assertFalse(pane.new_messages_badge.isVisible())
        self.assertEqual(bar.value(), bar.maximum())

        add_message(33)
        self.assertFalse(pane.new_messages_badge.isVisible())
        self.assertEqual(bar.value(), bar.maximum())

        bar.setValue(0)
        self.app.processEvents()
        pane.compose.setPlainText("hello")
        pane.service.send_text = Mock(return_value="send-request")
        pane._send()
        self.app.processEvents()
        QTest.qWait(10)
        self.app.processEvents()
        self.assertEqual(bar.value(), bar.maximum())
        self.assertFalse(pane.new_messages_badge.isVisible())

        add_message(34, sender_id=1)
        self.assertFalse(pane.new_messages_badge.isVisible())
        self.assertEqual(bar.value(), bar.maximum())

        bar.setValue(0)
        self.app.processEvents()
        add_message(35)
        self.assertTrue(pane.new_messages_badge.isVisible())
        bar.setValue(bar.maximum())
        self.app.processEvents()
        self.assertFalse(pane.new_messages_badge.isVisible())
        self.assertEqual(pane._pending_new_messages, 0)

        for message_id in range(36, 41):
            add_message(message_id)
        self.assertFalse(pane.new_messages_badge.isVisible())
        self.assertEqual(bar.value(), bar.maximum())

    def test_new_message_badge_ignores_layout_and_duplicate_events(self) -> None:
        from PySide6.QtTest import QTest

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.messages[key] = [
            {"id": message_id, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text",
             "content": f"message {message_id}"}
            for message_id in range(1, 31)
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.resize(640, 360)
        pane.show()
        self.app.processEvents()
        pane.set_conversation(key, "群聊")
        self.app.processEvents()
        QTest.qWait(40)
        self.app.processEvents()
        bar = pane.scroll.verticalScrollBar()
        bar.setValue(0)
        self.app.processEvents()

        def render(topic: str = "messages") -> None:
            pane.update_state(topic, store.state)
            self.app.processEvents()
            QTest.qWait(20)
            self.app.processEvents()

        store.state.messages[key].append(
            {"id": 31, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text", "content": "new"}
        )
        render()
        self.assertEqual(pane._pending_new_messages, 1)
        self.assertEqual(pane.new_messages_badge.text(), "1 条新消息")

        render()
        self.assertEqual(pane._pending_new_messages, 1)
        store.state.messages[key][-1]["content"] = "edited"
        render()
        self.assertEqual(pane._pending_new_messages, 1)
        store.state.messages[key][-1]["recalled"] = True
        store.state.messages[key][-1]["content"] = ""
        render()
        self.assertEqual(pane._pending_new_messages, 1)

        store.state.messages[key].append(
            {"id": 32, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "image", "image_name": "p.png"}
        )
        render()
        self.assertEqual(pane._pending_new_messages, 2)
        store.state.messages[key][-1]["image_data"] = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        render("media")
        self.assertEqual(pane._pending_new_messages, 2)

        store.state.messages[key].append(
            {"id": 33, "conversation_type": "group", "conversation_id": "7",
             "sender_id": "agent_taffy", "sender_agent_id": "agent_taffy", "sender_kind": "agent",
             "sender_name": "永雏塔菲", "message_type": "agent", "content_format": "markdown", "content": "a"}
        )
        render("agent_result")
        self.assertEqual(pane._pending_new_messages, 3)
        for chunk in ("ab", "abc", "abcd"):
            store.state.messages[key][-1]["content"] = chunk
            render("agent_result")
        self.assertEqual(pane._pending_new_messages, 3)

        store.state.messages[key].insert(
            0,
            {"id": 5, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text", "content": "older"},
        )
        render("history")
        self.assertEqual(pane._pending_new_messages, 3)
        self.assertEqual(pane.new_messages_badge.text(), "3 条新消息")

        store.state.messages[key].append(
            {"id": 40, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text", "content": "after reconnect"}
        )
        render("auth")
        self.assertEqual(pane._pending_new_messages, 4)
        render("auth")
        self.assertEqual(pane._pending_new_messages, 4)

    def test_five_new_messages_while_reading_history(self) -> None:
        from PySide6.QtTest import QTest

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.messages[key] = [
            {"id": message_id, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text",
             "content": f"message {message_id}"}
            for message_id in range(1, 31)
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.resize(640, 360)
        pane.show()
        self.app.processEvents()
        pane.set_conversation(key, "群聊")
        self.app.processEvents()
        QTest.qWait(40)
        self.app.processEvents()
        bar = pane.scroll.verticalScrollBar()
        bar.setValue(0)
        self.app.processEvents()

        for message_id in range(31, 36):
            store.state.messages[key].append(
                {"id": message_id, "conversation_type": "group", "conversation_id": "7",
                 "sender_id": 2, "sender_name": "bob", "message_type": "text",
                 "content": f"new {message_id}"}
            )
        pane.update_state("messages", store.state)
        self.app.processEvents()
        QTest.qWait(40)
        self.app.processEvents()

        self.assertTrue(pane.new_messages_badge.isVisible())
        self.assertEqual(pane.new_messages_badge.text(), "5 条新消息")
        self.assertEqual(bar.value(), 0)

        pane.new_messages_badge.click()
        self.app.processEvents()
        QTest.qWait(300)
        self.app.processEvents()
        self.assertFalse(pane.new_messages_badge.isVisible())
        self.assertEqual(bar.value(), bar.maximum())

    def test_send_failure_keeps_input_and_reports_error(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        service.send_text = Mock(return_value=None)
        pane = ChatPane(service, store)
        pane.set_conversation(key, "群聊")
        pane.show()
        self.app.processEvents()

        pane.compose.setPlainText("hello")
        store.state.last_error = "未连接到聊天服务器"
        pane._send()
        self.assertEqual(pane.compose.toPlainText(), "hello")
        self.assertIn("发送失败", pane.subtitle.text())
        pane._send_feedback_timer.stop()

    def test_history_load_preserves_visible_message_position(self) -> None:
        from PySide6.QtTest import QTest

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.messages[key] = [
            {"id": message_id, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text",
             "content": f"message {message_id}"}
            for message_id in range(100, 130)
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.resize(640, 360)
        pane.show()
        self.app.processEvents()
        pane.set_conversation(key, "群聊")
        self.app.processEvents()
        QTest.qWait(40)
        self.app.processEvents()
        bar = pane.scroll.verticalScrollBar()
        bar.setValue(500)
        self.app.processEvents()

        viewport = pane.scroll.viewport()
        rows = [
            (row.mapTo(viewport, QPoint(0, 0)).y(), message_id)
            for message_id, row in pane._message_rows.items()
        ]
        positive = [item for item in rows if item[0] >= 0]
        before_y, anchor_id = min(positive) if positive else min(rows, key=lambda item: abs(item[0]))

        for message_id in range(50, 70):
            store.state.messages[key].append(
                {"id": message_id, "conversation_type": "group", "conversation_id": "7",
                 "sender_id": 2, "sender_name": "bob", "message_type": "text",
                 "content": f"older {message_id}"}
            )
        store.state.messages[key].sort(key=lambda item: int(item["id"]))
        pane.update_state("history", store.state)
        self.app.processEvents()
        QTest.qWait(50)
        self.app.processEvents()

        after_y = pane._message_rows[anchor_id].mapTo(viewport, QPoint(0, 0)).y()
        self.assertLessEqual(abs(after_y - before_y), 2)
        self.assertEqual(pane._pending_new_messages, 0)
        self.assertFalse(pane.new_messages_badge.isVisible())

    def test_conversation_switch_keeps_independent_new_message_state(self) -> None:
        from PySide6.QtTest import QTest

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        first = ConversationKey("group", "7")
        second = ConversationKey("group", "8")
        store.state.messages[first] = [
            {"id": message_id, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text",
             "content": f"first {message_id}"}
            for message_id in range(1, 31)
        ]
        store.state.messages[second] = [
            {"id": message_id, "conversation_type": "group", "conversation_id": "8",
             "sender_id": 3, "sender_name": "carol", "message_type": "text",
             "content": f"second {message_id}"}
            for message_id in range(1, 10)
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.resize(640, 360)
        pane.show()
        self.app.processEvents()
        pane.set_conversation(first, "群聊 A")
        self.app.processEvents()
        QTest.qWait(40)
        self.app.processEvents()
        bar = pane.scroll.verticalScrollBar()
        bar.setValue(300)
        self.app.processEvents()

        store.state.messages[first].append(
            {"id": 31, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text", "content": "new"}
        )
        pane.update_state("messages", store.state)
        self.app.processEvents()
        QTest.qWait(30)
        self.app.processEvents()
        self.assertEqual(pane._pending_new_messages, 1)
        self.assertTrue(pane.new_messages_badge.isVisible())

        pane.set_conversation(second, "群聊 B")
        self.app.processEvents()
        QTest.qWait(40)
        self.app.processEvents()
        self.assertEqual(pane._pending_new_messages, 0)
        self.assertFalse(pane.new_messages_badge.isVisible())
        self.assertEqual(bar.value(), bar.maximum())

        store.state.messages[first].append(
            {"id": 32, "conversation_type": "group", "conversation_id": "7",
             "sender_id": 2, "sender_name": "bob", "message_type": "text", "content": "new 2"}
        )
        pane.set_conversation(first, "群聊 A")
        self.app.processEvents()
        QTest.qWait(60)
        self.app.processEvents()
        self.assertEqual(pane._pending_new_messages, 2)
        self.assertTrue(pane.new_messages_badge.isVisible())
        self.assertEqual(pane.new_messages_badge.text(), "2 条新消息")
        self.assertLess(bar.value(), bar.maximum())

        pane.set_conversation(second, "群聊 B")
        self.app.processEvents()
        QTest.qWait(40)
        self.app.processEvents()
        self.assertEqual(pane._pending_new_messages, 0)
        self.assertFalse(pane.new_messages_badge.isVisible())

    def test_ai_menu_lists_group_agents_and_inserts_selected_mention(self) -> None:
        from PySide6.QtCore import QBuffer, QIODevice

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.group_agents["7"] = [
            {"id": "agent", "member_id": "agent:agent", "name": "AI Agent",
             "username": "AI Agent", "display_name": "AI Agent", "kind": "agent", "enabled": True},
            {"id": "agent_taffy", "member_id": "agent:agent_taffy", "name": "永雏塔菲",
             "username": "永雏塔菲", "display_name": "永雏塔菲", "kind": "agent",
             "enabled": True, "builtin": True, "avatar_version": 1, "avatar_mime": "image/png"},
            {"id": "agent_off", "member_id": "agent:agent_off", "name": "离线 Agent",
             "username": "离线 Agent", "display_name": "离线 Agent", "kind": "agent", "enabled": False},
        ]
        avatar = QImage(2, 2, QImage.Format.Format_ARGB32)
        avatar.fill(QColor("#ff2d55"))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(avatar.save(buffer, "PNG"))
        store.state.agent_avatar_cache["agent_taffy"] = {
            "agent_id": "agent_taffy",
            "avatar_version": 1,
            "avatar_mime": "image/png",
            "image_data": base64.b64encode(bytes(buffer.data())).decode("ascii"),
        }
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(key, "群聊")
        pane.show()
        self.app.processEvents()

        pane._populate_ai_menu()
        labels = [action.text() for action in pane.ai_menu.actions()]
        self.assertIn("总结最近消息", labels)
        self.assertIn("提取待办", labels)
        self.assertIn("向 AI Agent 提问", labels)
        self.assertIn("向 永雏塔菲 提问 · 预设", labels)
        self.assertNotIn("向 离线 Agent 提问", labels)

        target = next(action for action in pane.ai_menu.actions() if action.text() == "向 永雏塔菲 提问 · 预设")
        self.assertFalse(target.icon().isNull())
        color = target.icon().pixmap(22, 22).toImage().pixelColor(11, 11)
        self.assertGreater(color.red(), 200)
        self.assertLess(color.green(), 100)
        target.trigger()
        self.app.processEvents()
        self.assertEqual(pane.compose.toPlainText(), "@永雏塔菲 ")
        mentions = pane.compose.mentions()
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0]["member_id"], "agent:agent_taffy")
        self.assertEqual(mentions[0]["kind"], "agent")

        store.state.group_agents.pop("7")
        store.state.members_by_group["7"] = [
            {"id": "agent", "member_id": "agent:agent", "username": "AI Agent", "kind": "agent"},
            {"id": 2, "member_id": "user:2", "username": "bob", "kind": "user"},
        ]
        pane._populate_ai_menu()
        labels = [action.text() for action in pane.ai_menu.actions()]
        self.assertIn("向 AI Agent 提问", labels)

        pane.set_conversation(ConversationKey("private", "2"), "bob")
        pane._populate_ai_menu()
        empty = pane.ai_menu.actions()[-1]
        self.assertEqual(empty.text(), "当前会话暂无可用 Agent")
        self.assertFalse(empty.isEnabled())

    def test_auth_page_and_conversation_card_layout(self) -> None:
        from client.v2.application import AuthPage
        from client.v2.widgets import ConversationCard

        auth = AuthPage("127.0.0.1", 8765)
        auth.resize(1180, 760)
        auth.show()
        self.app.processEvents()
        self.assertTrue(auth.hero.isVisible())
        self.assertFalse(auth.brand_tile.pixmap().isNull())
        self.assertEqual(auth.submit.text(), "登录")
        self.assertEqual(auth.heading.text(), "欢迎回来")
        self.assertEqual(auth.password.echoMode(), QLineEdit.EchoMode.Password)
        auth.reveal_action.trigger()
        self.assertEqual(auth.password.echoMode(), QLineEdit.EchoMode.Normal)
        auth.reveal_action.trigger()
        self.assertEqual(auth.password.echoMode(), QLineEdit.EchoMode.Password)
        auth.register_mode.click()
        self.assertEqual(auth.submit.text(), "创建账号")
        self.assertEqual(auth.heading.text(), "创建账号")
        auth.resize(760, 600)
        self.app.processEvents()
        self.assertFalse(auth.hero.isVisible())
        auth.close()
        card = ConversationCard(
            ConversationSummary(ConversationKey("group", "1"), "项目组", "最后消息", 1, 3)
        )
        card.resize(320, 64)
        card.show()
        self.app.processEvents()
        self.assertEqual(card._title.text(), "项目组")
        self.assertGreater(card._title.width(), 0)
        self.assertTrue(card._avatar._group)
        self.assertEqual(card.height(), 72)
        card.set_selected(True)
        self.assertTrue(card.property("selected"))
        card.close()

    def test_private_conversation_card_uses_cached_user_avatar(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ConversationPane

        encoded = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        store = AppStore()
        store.state.current_user = {"id": 1, "username": "alice"}
        store.state.users = [
            {
                "id": 2,
                "username": "摸底鳄鱼",
                "display_name": "摸底鳄鱼",
                "avatar_version": 1,
                "avatar_mime": "image/png",
            }
        ]
        store.state.avatar_cache[2] = {
            "user_id": 2,
            "avatar_version": 1,
            "image_data": encoded,
        }
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        pane = ConversationPane(service, store)
        key = ConversationKey("private", "dm:1:2")
        pane.set_conversations([ConversationSummary(key, "摸底鳄鱼")])
        self.app.processEvents()

        card = pane._cards[key]
        self.assertEqual(card._avatar._image_data, encoded)
        self.assertFalse(card._avatar.pixmap().isNull())
        pane.close()

    def test_workspace_responsive_minimum(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import Workspace

        store = AppStore()
        network = NetworkClient("127.0.0.1", 9)
        workspace = Workspace(ChatService(network, store), store, "127.0.0.1", 9)
        workspace.resize(720, 520)
        workspace.show()
        self.app.processEvents()
        self.assertFalse(workspace.conversations.isVisible())
        self.assertTrue(workspace.chat.conversation_list_button.isVisible())
        workspace.chat.conversation_list_button.click()
        self.app.processEvents()
        self.assertTrue(workspace.conversations.isVisible())
        self.assertFalse(workspace.chat.isVisible())
        workspace.close()

    def test_workspace_navigation_rail_is_compact_accessible_and_stateful(self) -> None:
        from PySide6.QtTest import QTest

        from client.models import AppState
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import Workspace

        store = AppStore()
        workspace = Workspace(
            ChatService(NetworkClient("127.0.0.1", 9), store),
            store,
            "127.0.0.1",
            9,
        )
        workspace.resize(860, 600)
        workspace.show()
        self.app.processEvents()

        self.assertEqual(workspace.rail.width(), 64)
        self.assertIsNone(workspace.rail.findChild(QLabel, "BrandMark"))
        self.assertEqual([button.text() for button in workspace.nav_buttons], ["消息", "通讯", "AI 任务", "待办", "管理", "设置"])
        self.assertTrue(all(isinstance(button, QToolButton) for button in workspace.nav_buttons))
        for button in workspace.nav_buttons:
            self.assertEqual(button.size(), QSize(52, 56))
            self.assertEqual(button.iconSize(), QSize(20, 20))
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.accessibleName(), button.text())
            self.assertTrue(button.toolTip())
            self.assertNotEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)

        upper_buttons = workspace.nav_buttons[:4]
        self.assertEqual(len({button.geometry().center().x() for button in workspace.nav_buttons}), 1)
        self.assertEqual(
            [upper_buttons[index + 1].y() - upper_buttons[index].y() for index in range(3)],
            [60, 60, 60],
        )
        geometry_before_selection = [button.geometry() for button in workspace.nav_buttons]

        workspace.nav_buttons[0].setFocus()
        QTest.keyClick(workspace.nav_buttons[0], Qt.Key.Key_Down)
        self.assertEqual(workspace.pages.currentIndex(), 1)
        self.assertTrue(workspace.nav_buttons[1].hasFocus())

        for index in (2, 3, 0):
            workspace.nav_buttons[index].click()
            self.assertEqual(workspace.pages.currentIndex(), index)
            self.assertTrue(workspace.nav_buttons[index].isChecked())
            self.assertEqual([button.geometry() for button in workspace.nav_buttons], geometry_before_selection)

        user_state = AppState(
            current_user={"id": 1, "role": "user"},
            conversations=[
                ConversationSummary(ConversationKey("group", "1"), "项目组", unread_count=3),
                ConversationSummary(ConversationKey("private", "2"), "bob", unread_count=2),
            ],
        )
        workspace.update_state("conversations", user_state)
        self.assertFalse(workspace.nav_buttons[4].isVisible())
        self.assertEqual(workspace.nav_buttons[0].badge.text(), "5")
        self.assertTrue(workspace.nav_buttons[0].badge.isVisible())

        admin_state = AppState(current_user={"id": 1, "role": "admin"})
        workspace.update_state("auth", admin_state)
        self.assertTrue(workspace.nav_buttons[4].isVisible())
        self.assertGreater(workspace.nav_buttons[5].y(), workspace.nav_buttons[4].y() + 60)

        workspace.resize(1180, 760)
        self.app.processEvents()
        self.assertEqual(workspace.rail.width(), 236)
        self.assertTrue(workspace.sidebar_wordmark.isVisible())
        self.assertTrue(workspace.user_card.isVisible())
        workspace.close()

    def test_scroll_surfaces_override_dark_system_palette(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import AICenterPage, ChatPane, ConversationPane
        from client.v2.theme import LIGHT_TOKENS, stylesheet
        from client.v2.widgets import configure_scroll_surface

        original_palette = QPalette(self.app.palette())
        original_stylesheet = self.app.styleSheet()
        dark_palette = QPalette(original_palette)
        dark_palette.setColor(QPalette.ColorRole.Window, QColor("#333333"))
        dark_palette.setColor(QPalette.ColorRole.Base, QColor("#333333"))
        self.app.setPalette(dark_palette)
        self.app.setStyleSheet(stylesheet())
        try:
            for role, expected in (("surface", LIGHT_TOKENS.surface), ("canvas", LIGHT_TOKENS.canvas)):
                area = QScrollArea()
                content = QWidget()
                content.resize(100, 60)
                configure_scroll_surface(area, content, role)
                area.setWidget(content)
                area.resize(120, 80)
                area.show()
                self.app.processEvents()
                image = QImage(content.size(), QImage.Format.Format_ARGB32)
                image.fill(Qt.GlobalColor.transparent)
                content.render(image)
                self.assertEqual(image.pixelColor(image.width() // 2, image.height() // 2).name().upper(), expected)
                area.close()
                area.deleteLater()

            store = AppStore()
            service = ChatService(NetworkClient("127.0.0.1", 9), store)
            conversations = ConversationPane()
            chat = ChatPane(service, store)
            tasks = AICenterPage(service, store)
            self.assertEqual(conversations.content.property("surfaceRole"), "surface")
            self.assertEqual(conversations.scroll.viewport().property("surfaceRole"), "surface")
            self.assertEqual(chat.property("surfaceRole"), "canvas")
            self.assertEqual(chat.message_content.property("surfaceRole"), "surface")
            self.assertEqual(chat.details.scroll_content.property("surfaceRole"), "surface")
            self.assertEqual(tasks.property("surfaceRole"), "canvas")
            self.assertEqual(tasks.task_content.property("surfaceRole"), "canvas")
            self.assertEqual(tasks.scroll.viewport().property("surfaceRole"), "canvas")
            for widget in (conversations, chat, tasks):
                widget.close()
                widget.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        finally:
            self.app.setPalette(original_palette)
            self.app.setStyleSheet(original_stylesheet)

    def test_message_bubble_uses_explicit_content_format(self) -> None:
        from client.v2.theme import current_tokens
        from client.v2.widgets import MessageBubble, markdown_to_safe_html

        symbols = "*不是斜体* #不是标题 _保持下划线_ <b>不是 HTML</b>"
        plain = MessageBubble({"message_type": "text", "content": symbols}, mine=True)
        self.assertEqual(plain.content_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(plain.content_label.text(), symbols)
        self.assertEqual(plain.content_label.objectName(), "PlainMessageContent")

        agent_plain = MessageBubble(
            {"sender_kind": "agent", "content_format": "plain", "content": "# 不是标题\n*保持原文*"},
            mine=False,
        )
        self.assertTrue(agent_plain.agent)
        self.assertEqual(agent_plain.content_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(agent_plain.content_label.text(), "# 不是标题\n*保持原文*")

        markdown = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "markdown",
                "content": "# 总结\n\n- **完成**联调\n\n```text\nstatus=ok\n```\n\n| 项目 | 状态 |\n| --- | --- |\n| TCP | 完成 |",
            },
            mine=False,
        )
        self.assertEqual(markdown.content_label.textFormat(), Qt.TextFormat.RichText)
        rendered = markdown.content_label.text()
        self.assertIn("<h1", rendered)
        self.assertIn("<pre", rendered)
        self.assertIn("<table", rendered)
        self.assertIn("font-weight:700", rendered)
        self.assertNotIn("font-size:xx-large", rendered)
        self.assertIn("font-size:16px", rendered)
        self.assertIn(f'bgcolor="{current_tokens().code_bg.lower()}"', rendered.lower())
        self.assertEqual(markdown.content_label.objectName(), "AgentMarkdownContent")
        self.assertEqual(markdown.maximumWidth(), 620)
        self.assertEqual(markdown.minimumWidth(), 440)
        self.assertEqual(markdown.layout().contentsMargins().left(), 11)

        scoped = markdown_to_safe_html("普通正文\n\n## 小标题")
        self.assertIn("font-weight:400", scoped)
        self.assertNotIn("font-size:x-large", scoped)

    def test_markdown_renderer_blocks_html_and_images(self) -> None:
        from client.v2.widgets import MessageBubble

        bubble = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "markdown",
                "content": "<script>alert(1)</script>\n\n![远程图](https://example.com/a.png)",
            },
            mine=False,
        )
        rendered = bubble.content_label.text().lower()
        self.assertNotIn("<script", rendered)
        self.assertNotIn("<img", rendered)
        self.assertFalse(bubble.content_label.openExternalLinks())

    def test_mention_editor_keeps_stable_id_and_plain_message_highlights_safely(self) -> None:
        from client.v2.widgets import MentionTextEdit, MessageBubble

        editor = MentionTextEdit()
        queries: list[str] = []
        editor.mention_query.connect(queries.append)
        editor.insertPlainText("你好 @bo")
        self.app.processEvents()
        self.assertEqual(queries[-1], "bo")
        editor.insert_mention(
            {"id": 2, "member_id": "user:2", "username": "bob01", "kind": "user"}
        )
        editor.insertPlainText("请确认")
        self.assertEqual(editor.toPlainText(), "你好 @bob01 请确认")
        mentions = editor.mentions()
        self.assertEqual(mentions[0]["member_id"], "user:2")
        self.assertEqual(editor.toPlainText()[mentions[0]["start"]:][: mentions[0]["length"]], "@bob01")

        bubble = MessageBubble(
            {
                "message_type": "text",
                "content": "<b>普通</b> @bob01",
                "mentions": [{**mentions[0], "start": 10}],
            },
            mine=False,
        )
        self.assertEqual(bubble.content_label.textFormat(), Qt.TextFormat.RichText)
        rendered = bubble.content_label.text()
        self.assertIn("&lt;b&gt;普通&lt;/b&gt;", rendered)
        self.assertIn("font-weight:600", rendered)

    def test_mention_popup_works_without_space_before_at(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.widgets import MentionTextEdit

        for prefix in ("明天联系", "hello", "😀", "请看，"):
            editor = MentionTextEdit()
            queries: list[str] = []
            editor.mention_query.connect(queries.append)
            editor.insertPlainText(f"{prefix}@")
            self.app.processEvents()
            self.assertEqual(queries[-1], "")
            editor.insertPlainText("bo")
            self.app.processEvents()
            self.assertEqual(queries[-1], "bo")
            editor.insert_mention(
                {"id": 2, "member_id": "user:2", "username": "bob01", "kind": "user"}
            )
            self.assertEqual(editor.toPlainText(), f"{prefix}@bob01 ")
            self.assertEqual(editor.mentions()[0]["start"], len(prefix))
            queries.clear()
            editor.set_content(f"{prefix}@bob01", [{
                "member_id": "user:2", "kind": "user", "display": "bob01",
                "start": len(prefix), "length": 6,
            }])
            self.app.processEvents()
            self.assertEqual(queries, [])
            editor.close()

        store = AppStore()
        store.state.current_user = {"id": 1, "username": "alice"}
        store.state.members_by_group["7"] = [
            {"id": 2, "member_id": "user:2", "kind": "user", "username": "bob01"}
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(ConversationKey("group", "7"), "群聊")
        pane.show()
        pane.compose.insertPlainText("明天联系@")
        self.app.processEvents()
        self.assertTrue(pane.mention_list.isVisible())
        pane.compose.insertPlainText("bo")
        self.app.processEvents()
        self.assertEqual(pane.mention_list.currentItem().text(), "bob01")
        pane.close()

    def test_chat_pane_filters_members_and_accepts_agent_with_keyboard(self) -> None:
        from PySide6.QtTest import QTest

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.members_by_group["7"] = [
            {"id": 2, "member_id": "user:2", "username": "小明", "kind": "user"},
            {"id": "agent", "member_id": "agent", "username": "AI Agent", "kind": "agent"},
        ]
        store.state.messages[ConversationKey("group", "7")] = [
            {
                "id": 9,
                "sender_id": "agent",
                "sender_kind": "agent",
                "sender_name": "AI Agent",
                "message_type": "agent",
                "content_format": "markdown",
                "content": "## 结论\n\n可以开始验收。",
            }
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(ConversationKey("group", "7"), "项目组")
        pane.compose.insertPlainText("@AI")
        self.app.processEvents()
        self.assertEqual(pane.mention_list.count(), 1)
        self.assertEqual(pane.mention_list.item(0).data(Qt.ItemDataRole.UserRole)["member_id"], "agent")
        QTest.keyClick(pane.compose, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(pane.compose.toPlainText(), "@AI Agent ")
        self.assertEqual(pane.compose.mentions()[0]["member_id"], "agent")

    def test_details_panel_uses_member_rows_and_responsive_overlay(self) -> None:
        from PySide6.QtTest import QTest

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.details import MemberRow

        store = AppStore()
        store.state.members_by_group["7"] = [
            {
                "id": 1,
                "username": "名字很长也应完整保留的群主",
                "kind": "user",
                "group_role": "owner",
                "online": True,
                "avatar_version": 1,
                "available_actions": [],
            },
            {
                "id": "agent",
                "username": "AI Agent",
                "kind": "agent",
                "group_role": "agent",
                "online": True,
                "available_actions": [],
            },
        ]
        store.state.group_permissions["7"] = {
            "can_manage_members": True,
            "can_manage_schedule": True,
        }
        store.state.schedules_by_group["7"] = {
            "group_id": 7,
            "interval_minutes": 120,
            "enabled": True,
        }
        store.state.avatar_cache[1] = {
            "user_id": 1,
            "avatar_version": 1,
            "image_data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        }
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.resize(800, 600)
        pane.show()
        pane.set_conversation(ConversationKey("group", "7"), "项目组")
        pane.details_button.click()
        self.app.processEvents()

        self.assertTrue(pane.details.isVisible())
        self.assertTrue(pane.details_scrim.isVisible())
        self.assertEqual(pane.root_layout.indexOf(pane.details), -1)
        self.assertGreaterEqual(pane.details.width(), 300)
        self.assertLessEqual(pane.details.width(), 320)
        self.assertTrue(pane.details.schedule.toggle.isChecked())
        self.assertEqual(pane.details.schedule.interval.currentData(), 120)
        first_row = pane.details.members.rows.itemAt(0).widget()
        self.assertIsInstance(first_row, MemberRow)
        self.assertEqual(first_row.member["group_role"], "owner")
        self.assertEqual(first_row.profile.group_role, "owner")
        self.assertIsNotNone(first_row.profile.image_data)
        first_row.activated.emit(first_row.member, first_row.avatar)
        self.app.processEvents()
        self.assertTrue(pane.details.members.popover.isVisible())
        QTest.keyClick(pane.details.members.popover, Qt.Key.Key_Escape)
        self.assertFalse(pane.details.members.popover.isVisible())

        pane.resize(1200, 600)
        self.app.processEvents()
        self.assertGreaterEqual(pane.root_layout.indexOf(pane.details), 0)
        self.assertFalse(pane.details_scrim.isVisible())
        pane.close()

    def test_profile_resolver_prefers_newest_identity_and_cached_avatar(self) -> None:
        from client.models import AppState
        from client.profile import resolve_profile

        state = AppState(
            users=[{"id": 2, "username": "bob01", "avatar_version": 3, "online": False}],
            members_by_group={
                "7": [
                    {
                        "id": 2,
                        "username": "Bob",
                        "avatar_version": 2,
                        "online": True,
                        "group_role": "owner",
                        "available_actions": ["mute"],
                    }
                ]
            },
            avatar_cache={2: {"user_id": 2, "avatar_version": 3, "image_data": "image"}},
        )
        profile = resolve_profile(state, 2, state.members_by_group["7"][0], group_id="7")
        self.assertEqual(profile.user_id, 2)
        self.assertEqual(profile.avatar_version, 3)
        self.assertEqual(profile.image_data, "image")
        self.assertEqual(profile.group_role, "owner")
        self.assertEqual(profile.available_actions, ("mute",))

    def test_private_and_global_profile_do_not_inherit_group_owner_role(self) -> None:
        from client.models import AppState
        from client.profile import resolve_profile
        from client.v2.profile_widgets import profile_role

        state = AppState(
            users=[{"id": 2, "username": "user2", "display_name": "User 2", "role": "user"}],
            members_by_group={
                "7": [{"id": 2, "username": "user2", "group_role": "owner"}]
            },
        )
        private_profile = resolve_profile(
            state, 2, {"id": 2, "username": "user2", "kind": "user"}
        )
        self.assertEqual(private_profile.group_role, "")
        self.assertEqual(profile_role(private_profile), "联系人")

        group_profile = resolve_profile(
            state,
            2,
            {"id": 2, "username": "user2", "group_role": "owner"},
            group_id="7",
        )
        self.assertEqual(group_profile.group_role, "owner")
        self.assertEqual(profile_role(group_profile), "群主")

    def test_private_chat_avatar_profile_card_does_not_show_group_owner(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.widgets import UserAvatar

        store = AppStore()
        store.state.current_user = {"id": 1, "username": "alice"}
        store.state.users = [{"id": 2, "username": "user2", "display_name": "User 2"}]
        store.state.members_by_group["7"] = [
            {"id": 2, "username": "user2", "display_name": "User 2", "group_role": "owner"}
        ]
        key = ConversationKey("private", "dm:1:2")
        store.state.conversations = [ConversationSummary(key, "User 2")]
        store.state.messages[key] = [
            {
                "id": 5,
                "conversation_type": "private",
                "conversation_id": key.identifier,
                "sender_id": 2,
                "sender_name": "User 2",
                "message_type": "text",
                "content": "你好",
            }
        ]
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        pane = ChatPane(service, store)
        pane.resize(900, 600)
        pane.set_conversation(key, "User 2")
        pane.show()
        self.app.processEvents()

        avatar = pane.findChildren(UserAvatar)[0]
        self.assertEqual(avatar.profile.group_role, "")
        pane.profile_popover.show_for(avatar, avatar.profile, pinned=True)
        self.app.processEvents()
        self.assertEqual(pane.profile_popover.card.role.text(), "联系人")
        pane.close()

    def test_profile_popover_scrolls_long_bio_and_avatar_menu_mentions(self) -> None:
        from client.network import NetworkClient
        from client.profile import ProfileViewModel
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.profile_widgets import ProfilePopover
        from client.v2.widgets import UserAvatar

        store = AppStore()
        store.state.current_user = {"id": 1, "username": "alice"}
        store.state.members_by_group["7"] = [
            {"id": 2, "member_id": "user:2", "kind": "user", "username": "bob", "display_name": "小博"}
        ]
        key = ConversationKey("group", "7")
        store.state.messages[key] = [
            {
                "id": 1,
                "conversation_type": "group",
                "conversation_id": "7",
                "sender_id": 2,
                "sender_name": "小博",
                "message_type": "text",
                "content": "你好",
            }
        ]
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        profile = ProfileViewModel(
            user_id=None,
            display_name="千冥爱音",
            agent_id="a1",
            kind="agent",
            online=True,
            agent_model="chatroom-qwen3.5-4b",
            agent_description="开朗、社交力强、爱赶流行，也有点爱面子；关键时刻会努力把大家拉到一起，不轻易放弃。" * 12,
            agent_personality="开朗、爱赶流行、重视伙伴的乐队风格角色。" * 10,
        )
        anchor = QWidget()
        anchor.resize(36, 36)
        anchor.show()
        popover = ProfilePopover(service)
        popover.show_for(anchor, profile, pinned=True)
        self.app.processEvents()
        self.assertLessEqual(popover.height(), 560)
        self.assertGreater(popover.scroll.verticalScrollBar().maximum(), 0)
        popover.hide()

        pane = ChatPane(service, store)
        pane.resize(900, 600)
        pane.set_conversation(key, "项目组")
        pane.show()
        self.app.processEvents()
        avatar = pane.findChildren(UserAvatar)[0]
        menu = pane._avatar_menu(avatar.profile, avatar)
        self.assertIn("提及", [action.text() for action in menu.actions()])
        pane._insert_profile_mention(avatar.profile)
        self.assertEqual(pane.compose.mentions()[0]["member_id"], "user:2")
        pane.close()

    def test_chat_switches_do_not_leave_avatar_tooltips_or_rebuild_members(self) -> None:
        from client.models import AppState
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane
        from client.v2.details import MemberListSection
        from client.v2.widgets import ConversationCard, UserAvatar

        store = AppStore()
        store.state.current_user = {"id": 1, "username": "alice"}
        key = ConversationKey("group", "7")
        store.state.members_by_group["7"] = [
            {"id": 2, "member_id": "user:2", "kind": "user", "username": "bob", "display_name": "小博"}
        ]
        store.state.messages[key] = [
            {
                "id": 1,
                "conversation_type": "group",
                "conversation_id": "7",
                "sender_id": 2,
                "sender_name": "小博",
                "message_type": "text",
                "content": "切换会话时不要闪出小窗口",
            }
        ]
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        pane = ChatPane(service, store)
        pane.resize(900, 600)
        pane.set_conversation(key, "项目组")
        pane.show()
        self.app.processEvents()
        self.assertTrue(pane.eventFilter(QLabel(), QEvent(QEvent.Type.ToolTip)))

        avatars = pane.findChildren(UserAvatar)
        self.assertTrue(avatars)
        self.assertEqual(avatars[0].toolTip(), "")
        self.assertEqual(avatars[0].accessibleName(), avatars[0].profile.display_name)
        self.assertFalse(avatars[0].hoverable)
        pane.profile_popover.show_for(avatars[0], avatars[0].profile, pinned=True)
        self.app.processEvents()
        self.assertTrue(pane.profile_popover.isVisible())
        pane.set_conversation(ConversationKey("group", "8"), "另一个群")
        self.app.processEvents()
        self.assertFalse(pane.profile_popover.isVisible())

        short = ConversationCard(ConversationSummary(key, "短标题"))
        short.show()
        self.app.processEvents()
        short.setFixedWidth(260)
        self.app.processEvents()
        self.assertEqual(short._title.toolTip(), "")
        long_title = "这是一个会被截断的很长很长的群聊标题用于验证提示只在必要时出现"
        long_card = ConversationCard(ConversationSummary(key, long_title))
        long_card.show()
        self.app.processEvents()
        long_card.setFixedWidth(260)
        self.app.processEvents()
        self.assertEqual(long_card._title.toolTip(), "")
        self.assertEqual(long_card._title.accessibleName(), long_title)

        unread = ConversationCard(
            ConversationSummary(key, "群聊", preview="这是一段很长的会话预览" * 4, unread_count=3)
        )
        unread.show()
        self.app.processEvents()
        self.assertEqual(unread._title.toolTip(), "")
        self.assertEqual(unread._preview.toolTip(), "")
        self.assertEqual(unread._badge.toolTip(), "")

        state = AppState()
        state.agent_model = "deepseek-flash"
        state.members_by_group["7"] = [dict(store.state.members_by_group["7"][0])]
        section = MemberListSection(service)
        section.show()
        section.set_members(state.members_by_group["7"], state)
        self.app.processEvents()
        first_row = section.rows.itemAt(0).widget()
        section.popover.show_for(first_row.avatar, first_row.profile, pinned=True)
        self.app.processEvents()
        self.assertTrue(section.popover.isVisible())
        section.set_members([dict(state.members_by_group["7"][0])], state)
        self.assertIs(section.rows.itemAt(0).widget(), first_row)
        self.assertTrue(section.popover.isVisible())
        pane.close()
        section.close()

    def test_contacts_use_selection_details_and_semantic_group_actions(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ContactsPage

        store = AppStore()
        store.state.current_user = {"id": 1, "username": "alice"}
        store.state.users = [
            {"id": 1, "username": "alice"},
            {"id": 2, "username": "bob01", "online": True},
        ]
        store.state.groups = [
            {"id": 1, "name": "大厅", "joined": 1, "member_role": "member"},
            {"id": 2, "name": "项目组", "joined": 0, "member_role": ""},
        ]
        page = ContactsPage(ChatService(NetworkClient("127.0.0.1", 9), store))
        page.resize(680, 520)
        page.show()
        self.app.processEvents()
        page.update_state("auth", store.state)
        self.assertTrue(page.directory.isVisible())
        self.assertFalse(page.details_host.isVisible())
        page._select_user(page.users.item(0))
        self.app.processEvents()
        self.assertFalse(page.directory.isVisible())
        self.assertTrue(page.details_host.isVisible())
        self.assertEqual(page.user_profile.profile.user_id, 2)
        self.assertEqual(page.user_profile.name.text(), "bob01")
        page._select_user(page.users.item(0))
        profile_actions = [
            button for button in page.user_profile.findChildren(QPushButton)
            if button.text() == "发消息" and button.parent() is page.user_profile
        ]
        self.assertEqual(len(profile_actions), 1)
        page.directory_back.click()
        self.assertTrue(page.directory.isVisible())

        available = next(
            page.groups.item(index)
            for index in range(page.groups.count())
            if isinstance(page.groups.item(index).data(Qt.ItemDataRole.UserRole), dict)
            and page.groups.item(index).data(Qt.ItemDataRole.UserRole)["id"] == 2
        )
        page._select_group(available)
        self.assertEqual(page.group_primary.text(), "加入群聊")
        self.assertFalse(page.group_more.isVisible())

        page.search.setText("bob")
        self.assertEqual(page.users.count(), 1)
        self.assertEqual(page.users.item(0).data(Qt.ItemDataRole.UserRole)["id"], 2)
        page.search.setText("missing")
        self.assertEqual(page.users.count(), 1)
        self.assertIsNone(page.users.item(0).data(Qt.ItemDataRole.UserRole))

        page._set_tab(1)
        page.search.setText("项目")
        visible_groups = [
            page.groups.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(page.groups.count())
            if isinstance(page.groups.item(index).data(Qt.ItemDataRole.UserRole), dict)
        ]
        self.assertEqual([group["id"] for group in visible_groups], [2])

    def test_ai_task_center_uses_readable_records_and_result_signal(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import AICenterPage, AgentTaskCard

        store = AppStore()
        key = ConversationKey("group", "7")
        store.state.conversations = [ConversationSummary(key, "项目组")]
        store.state.activities = [
            {
                "id": 99,
                "task_type": "summary",
                "conversation_type": "group",
                "conversation_id": "7",
                "requested_by_name": "alice",
                "status": "succeeded",
                "started_at": 1,
                "result_message_id": 42,
            },
            {
                "id": 100,
                "task_type": "todo",
                "conversation_type": "group",
                "conversation_id": "7",
                "requested_by_name": "alice",
                "status": "failed",
                "started_at": 2,
                "finished_at": 3,
                "error": "模型暂时不可用",
            },
        ]
        page = AICenterPage(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        page.update_state("conversations", store.state)
        card = page.task_rows.itemAt(0).widget()
        self.assertIsInstance(card, AgentTaskCard)
        labels = [label.text() for label in card.findChildren(QLabel)]
        self.assertIn("总结会话", labels)
        self.assertTrue(any("项目组" in text for text in labels))
        self.assertEqual(page.filter_summary.text(), "2 个任务")
        self.assertEqual(card.findChild(QLabel, "TaskStatus").property("taskStatus"), "succeeded")

        page.type_filter.setCurrentIndex(page.type_filter.findData("todo"))
        self.assertEqual(page.filter_summary.text(), "1 个任务")
        failed_card = page.task_rows.itemAt(0).widget()
        self.assertIsInstance(failed_card, AgentTaskCard)
        self.assertTrue(any("AI 服务暂时不可用" in label.text() for label in failed_card.findChildren(QLabel)))

        store.state.pending["request"] = "agent.activity.list"
        page.update_state("pending", store.state)
        self.assertFalse(page.notice.isHidden())
        self.assertIn("正在更新", page.notice.text())

    def test_ai_task_center_keeps_personal_structured_tasks_only(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import AICenterPage

        store = AppStore()
        key = ConversationKey("group", "7")
        store.state.current_user = {"id": 1, "username": "alice"}
        store.state.conversations = [ConversationSummary(key, "项目组")]
        store.state.activities = [
            {"id": 1, "task_type": "question", "conversation_type": "group", "conversation_id": "7",
             "requested_by": 1, "requested_by_name": "alice", "status": "succeeded", "started_at": 1},
            {"id": 2, "task_type": "summary", "conversation_type": "group", "conversation_id": "7",
             "requested_by": 1, "requested_by_name": "alice", "status": "succeeded", "started_at": 2},
            {"id": 3, "task_type": "todo", "conversation_type": "group", "conversation_id": "7",
             "requested_by": 2, "requested_by_name": "bob", "status": "failed", "started_at": 3},
        ]
        page = AICenterPage(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        page.update_state("conversations", store.state)
        self.assertEqual(page.filter_summary.text(), "1 个任务")
        self.assertLess(page.type_filter.findData("question"), 0)

    def test_ai_task_dialog_accepts_optional_time_range(self) -> None:
        from client.v2.application import NewAgentTaskDialog

        key = ConversationKey("group", "7")
        dialog = NewAgentTaskDialog([ConversationSummary(key, "测试群")])
        dialog.time_range.setText("昨天下午")
        selected, prompt = dialog.request()
        self.assertEqual(selected, key)
        self.assertEqual(prompt, "总结昨天下午的消息")
        dialog.task_type.setCurrentIndex(1)
        _selected, todo_prompt = dialog.request()
        self.assertEqual(todo_prompt, "提取昨天下午的待办")

    def test_test_notification_uses_the_application_tray(self) -> None:
        from client.v2.application import MainWindow

        fake = Mock()
        fake._tray = Mock()
        MainWindow._show_test_notification(fake)
        fake._tray.showMessage.assert_called_once()

    def test_settings_groups_real_controls_and_marks_unsupported_preferences(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import SettingsPage

        store = AppStore()
        store.state.current_user = {"id": 1, "username": "alice", "role": "user"}
        store.state.connection_text = "已连接"
        store.state.agent_online = True
        store.state.agent_model = "qwen-test"
        page = SettingsPage(ChatService(NetworkClient("127.0.0.1", 9), store), store, "127.0.0.1", 8765)
        page.resize(680, 520)
        page.show()
        page.update_state("auth", store.state)
        self.app.processEvents()

        labels = {label.text() for label in page.findChildren(QLabel)}
        self.assertIn("账号", labels)
        self.assertIn("昵称", labels)
        self.assertEqual(page.username_edit.accessibleName(), "账号")
        self.assertEqual(page.display_name_edit.accessibleName(), "昵称")
        self.assertEqual(page.save_identity.text(), "保存")

        self.assertEqual(page.settings_nav.count(), 6)
        self.assertTrue(page.compact_nav.isVisible())
        self.assertFalse(page.settings_sidebar.isVisible())
        page._select_section(4)
        self.assertEqual(page.settings_stack.currentIndex(), 4)
        self.assertEqual(page.agent_setting_status.text(), "Agent 在线")
        self.assertIn("qwen-test", page.agent_setting_model.text())
        planned = [label for label in page.findChildren(QLabel) if label.objectName() == "PlannedBadge"]
        self.assertEqual(planned, [])
        self.assertNotIn("已分离", labels)
        self.assertEqual(page.theme_mode.count(), 3)
        self.assertIsNotNone(page.send_shortcut)
        self.assertNotIn("当前固定", labels)
        self.assertEqual(page.send_shortcut.count(), 2)
        from PySide6.QtCore import QSettings
        settings = QSettings()
        old_send_shortcut = settings.value("chat/enter_to_send")
        try:
            self.assertEqual(
                page.send_shortcut.currentData(),
                settings.value("chat/enter_to_send", True, type=bool),
            )
            page.send_shortcut.setCurrentIndex(page.send_shortcut.findData(False))
            self.assertFalse(settings.value("chat/enter_to_send", type=bool))
            self.assertEqual(page.send_shortcut_hint.text(), "Enter 换行")
            page.send_shortcut.setCurrentIndex(page.send_shortcut.findData(True))
            self.assertTrue(settings.value("chat/enter_to_send", type=bool))
            self.assertEqual(page.send_shortcut_hint.text(), "Shift+Enter 换行")
        finally:
            if old_send_shortcut is None:
                settings.remove("chat/enter_to_send")
            else:
                settings.setValue("chat/enter_to_send", old_send_shortcut)
        self.assertIsNotNone(page.message_density)
        self.assertIsNotNone(page.export_diagnostics)
        self.assertIsNotNone(page.test_notification)
        self.assertIsNotNone(page.notify_enabled)
        self.assertIsNotNone(page.do_not_disturb)
        self.assertIsNotNone(page.notify_sound)

        density_values: list[str] = []
        page.density_changed.connect(density_values.append)
        page.message_density.setCurrentIndex(page.message_density.findData("compact"))
        self.assertEqual(density_values[-1], "compact")
        self.assertEqual(QSettings().value("ui/message_density"), "compact")
        page.message_density.setCurrentIndex(page.message_density.findData("default"))

    def test_mention_editor_waits_for_chinese_input_method_commit(self) -> None:
        from PySide6.QtGui import QInputMethodEvent

        from client.v2.widgets import MentionTextEdit

        editor = MentionTextEdit()
        queries: list[str] = []
        editor.mention_query.connect(queries.append)
        editor.insertPlainText("@")
        self.app.processEvents()
        queries.clear()

        QApplication.sendEvent(editor, QInputMethodEvent("小", []))
        self.app.processEvents()
        self.assertEqual(queries, [])
        self.assertFalse(editor.mention_popup_active)

        commit = QInputMethodEvent()
        commit.setCommitString("小")
        QApplication.sendEvent(editor, commit)
        self.app.processEvents()
        self.assertEqual(queries[-1], "小")

    def test_send_shortcut_modes_and_default_newline(self) -> None:
        from PySide6.QtTest import QTest

        from client.v2.widgets import MentionTextEdit

        editor = MentionTextEdit()
        sent: list[bool] = []
        editor.send_requested.connect(lambda: sent.append(True))
        with patch("client.v2.widgets.text_edit.QSettings") as settings:
            settings.return_value.value.side_effect = lambda _key, default, **_kwargs: default
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.assertEqual(sent, [True])
            self.assertEqual(editor.toPlainText(), "")
            QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(sent, [True])
            self.assertEqual(editor.toPlainText(), "\n")

            editor.clear()
            sent.clear()
            settings.return_value.value.side_effect = lambda _key, _default, **_kwargs: False
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.assertEqual(sent, [])
            self.assertEqual(editor.toPlainText(), "\n")
            QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(editor, Qt.Key.Key_Enter, Qt.KeyboardModifier.MetaModifier)
            self.assertEqual(sent, [True, True])
            editor.clear()
            sent.clear()
            settings.return_value.value.side_effect = lambda _key, default, **_kwargs: default
            editor.mention_popup_active = True
            selected: list[int] = []
            editor.mention_key.connect(selected.append)
            QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(editor.toPlainText(), "\n")
            self.assertEqual(sent, [])
            self.assertEqual(selected, [])
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.assertEqual(selected, [0])
            self.assertEqual(sent, [])
        editor.close()

    def test_mention_copy_paste_preserves_identity_and_partial_delete_is_atomic(self) -> None:
        from PySide6.QtCore import QByteArray, QMimeData
        from PySide6.QtGui import QKeyEvent, QTextCursor

        from client.v2.widgets import MentionTextEdit

        source = MentionTextEdit()
        source.insertPlainText("@bo")
        self.app.processEvents()
        source.insert_mention(
            {"id": 2, "member_id": "user:2", "username": "bob01", "kind": "user"}
        )
        cursor = source.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
        source.setTextCursor(cursor)
        mime = source.createMimeDataFromSelection()
        copied = QMimeData()
        copied.setText(mime.text())
        copied.setData(source.MENTION_MIME, QByteArray(bytes(mime.data(source.MENTION_MIME))))

        target = MentionTextEdit()
        target.insertFromMimeData(copied)
        self.app.processEvents()
        self.assertEqual(target.toPlainText(), "@bob01")
        self.assertEqual(target.mentions()[0]["member_id"], "user:2")

        cursor = target.textCursor()
        cursor.setPosition(3)
        target.setTextCursor(cursor)
        QApplication.sendEvent(
            target,
            QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier),
        )
        self.assertEqual(target.toPlainText(), "")
        self.assertEqual(target.mentions(), [])

    def test_agent_manager_and_memory_dialogs_render_existing_state(self) -> None:
        from PySide6.QtWidgets import QListWidget, QTextEdit

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import AgentManagerDialog, MemoryDialog

        store = AppStore()
        store.state.authenticated = True
        store.state.current_user = {"id": 1, "username": "alice"}
        store.state.current_conversation = ConversationKey("group", "7")
        store.state.agents = [
            {
                "id": "agent",
                "name": "AI Agent",
                "description": "默认助手",
                "enabled": True,
                "memory_enabled": True,
                "can_manage": False,
                "card": {"personality": "可靠", "background": "", "language_style": "", "behavior_rules": ""},
            },
            {
                "id": "agent_taffy",
                "name": "永雏塔菲",
                "description": "跟踪进度",
                "enabled": True,
                "memory_enabled": True,
                "builtin": True,
                "can_manage": False,
                "card": {"personality": "务实", "background": "", "language_style": "", "behavior_rules": ""},
            },
        ]
        store.state.agent_models = [{"id": "default", "name": "默认模型", "model": "demo", "is_default": True}]
        store.state.tool_definitions = [
            {"name": "read_conversation_messages", "label": "读取会话", "mode": "auto"}
        ]
        store.state.agent_tools["agent"] = [
            {"name": "read_conversation_messages", "label": "读取会话", "mode": "auto", "enabled": True}
        ]
        store.state.groups = [{"id": 7, "name": "项目组", "joined": 1}]
        store.state.memories[("agent", "group", "7")] = [
            {"id": 3, "agent_id": "agent", "scope_type": "group", "scope_id": "7", "kind": "decision", "content": "周五发布"}
        ]
        store.state.memory_settings[("agent", "group", "7")] = True
        service = ChatService(NetworkClient("127.0.0.1", 9), store)

        manager = AgentManagerDialog(service, store, group_id="7")
        manager.show()
        self.app.processEvents()
        self.assertEqual(manager.group_combo.currentData(), "7")
        self.assertEqual(manager.add_group.text(), "加入当前群")
        self.assertTrue(any("预设" in manager.agent_list.item(index).text() for index in range(manager.agent_list.count())))
        manager.agent_list.setCurrentRow(0)
        manager._selection_changed(manager.agent_list.item(0), None)
        self.app.processEvents()
        self.assertEqual(manager.name_edit.text(), "AI Agent")
        self.assertGreaterEqual(len(manager.findChildren(QTextEdit)), 4)
        manager.close()

        memories = MemoryDialog(service, store)
        memories.show()
        self.app.processEvents()
        listing = memories.findChild(QListWidget)
        self.assertIsNotNone(listing)
        self.assertIn("周五发布", listing.item(0).text())
        self.assertTrue(memories.enabled_check.isChecked())
        memories.close()

    def test_group_owner_saves_group_tools_without_agent_global_permission(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import AgentManagerDialog

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        agent = {"id": "agent", "name": "AI Agent", "owner_id": None,
                 "enabled": True, "can_manage": False}
        store.state.agents = [agent]
        store.state.groups = [{"id": 7, "name": "研究组", "owner_id": 1, "joined": 1}]
        store.state.group_agents["7"] = [agent]
        tools = [
            {"name": "web_search", "label": "联网搜索", "enabled": False, "group_enabled": True},
            {"name": "read_conversation_messages", "label": "读取会话", "enabled": True, "group_enabled": True},
        ]
        store.state.agent_tools["agent"] = [dict(tool) for tool in tools]
        store.state.group_agent_tools[("7", "agent")] = [dict(tool) for tool in tools]
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        manager = AgentManagerDialog(service, store, group_id="7")
        manager._load_agent(agent)
        self.assertFalse(manager.save_tools.isEnabled())
        self.assertTrue(manager.tool_list.isEnabled())
        self.assertTrue(manager.tool_list.isHidden())
        self.assertFalse(manager.tool_list.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(manager.save_tools.text(), "全局权限由管理员管理")
        self.assertIn("群内工具权限", manager.global_tool_help.text())
        self.assertTrue(manager.save_group_tools.isEnabled())
        self.assertTrue(manager.group_tool_list.item(0).flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertFalse(manager.group_tool_list.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertIn("Agent 未授权", manager.group_tool_list.item(0).text())
        with patch.object(service, "set_group_agent_tools") as save:
            manager._save_group_tools()
        save.assert_called_once_with("7", "agent", ["read_conversation_messages"])
        manager.close()

    def test_group_owner_saves_authorized_web_search_for_group(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import AgentManagerDialog

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        agent = {"id": "agent", "name": "AI Agent", "owner_id": None,
                 "enabled": True, "can_manage": False}
        store.state.agents = [agent]
        store.state.groups = [{"id": 7, "name": "研究组", "owner_id": 1, "joined": 1}]
        store.state.group_agents["7"] = [agent]
        tools = [{"name": "web_search", "label": "联网搜索", "enabled": True, "group_enabled": False}]
        store.state.agent_tools["agent"] = [dict(tool) for tool in tools]
        store.state.group_agent_tools[("7", "agent")] = [dict(tool) for tool in tools]
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        manager = AgentManagerDialog(service, store, group_id="7")
        manager._load_agent(agent)
        item = manager.group_tool_list.item(0)
        self.assertTrue(item.flags() & Qt.ItemFlag.ItemIsEnabled)
        item.setCheckState(Qt.CheckState.Checked)
        with patch.object(service, "set_group_agent_tools") as save:
            manager._save_group_tools()
        save.assert_called_once_with("7", "agent", ["web_search"])
        manager.close()

    def test_group_details_expose_agent_management_and_presets(self) -> None:
        from PySide6.QtWidgets import QComboBox

        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.details import ConversationDetailsPanel

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.current_conversation = key
        store.state.members_by_group["7"] = [
            {"id": 1, "member_id": "user:1", "username": "alice", "kind": "user", "group_role": "owner"}
        ]
        store.state.group_permissions["7"] = {"can_manage_members": True, "can_manage_schedule": True}
        store.state.agents = [
            {"id": "agent", "name": "AI Agent", "enabled": True, "builtin": False},
            {"id": "agent_taffy", "name": "永雏塔菲", "enabled": True, "builtin": True},
        ]
        store.state.group_agents["7"] = [
            {"id": "agent", "member_id": "agent", "name": "AI Agent", "enabled": True, "builtin": False}
        ]
        details = ConversationDetailsPanel(ChatService(NetworkClient("127.0.0.1", 9), store))
        details.set_context(key, "项目组", store.state)
        combo = details.agents.findChild(QComboBox)
        self.assertIsNotNone(combo)
        self.assertEqual(combo.currentData(), "agent_taffy")
        self.assertIn("预设", combo.currentText())
        self.assertTrue(details.agents.add_button.isEnabled())
        details.close()


if __name__ == "__main__":
    unittest.main()
