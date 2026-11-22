from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QSettings
    from PySide6.QtWidgets import QApplication, QLabel
except ImportError:  # pragma: no cover - optional until V2 dependencies are installed
    QApplication = None  # type: ignore[assignment]

from client.drafts import Draft, DraftStore
from client.models import ConversationKey, ConversationSummary


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class DraftStoreTests(unittest.TestCase):
    def test_persists_mentions_and_isolates_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "drafts.ini"), QSettings.Format.IniFormat)
            key = ConversationKey("group", "7")
            store = DraftStore(settings, account="1")
            store.put(
                key,
                Draft.create(
                    "hi @bob",
                    [{"start": 3, "length": 4, "member_id": "user:2", "kind": "user", "display": "bob"}],
                    9,
                ),
            )
            store.flush()

            restored = DraftStore(settings, account="1")
            draft = restored.get(key)
            self.assertIsNotNone(draft)
            assert draft is not None
            self.assertEqual(draft.text, "hi @bob")
            self.assertEqual(draft.reply_to_id, 9)
            self.assertEqual(draft.mentions[0]["member_id"], "user:2")

            restored.set_account("2")
            self.assertIsNone(restored.get(key))
            restored.set_account("1")
            self.assertEqual(restored.get(key).text, "hi @bob")

    def test_empty_draft_removes_persisted_record(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "drafts.ini"), QSettings.Format.IniFormat)
            key = ConversationKey("private", "2")
            store = DraftStore(settings)
            store.put(key, Draft.create("unsent"))
            store.flush()
            store.remove(key)
            store.flush()
            self.assertEqual(DraftStore(settings).all(), {})


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class ChatDraftUiTests(unittest.TestCase):
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

    @staticmethod
    def _pane(store, service, draft_store: DraftStore | None = None):
        from client.v2.application import ChatPane

        return ChatPane(service, store, draft_store=draft_store or DraftStore(None))

    @staticmethod
    def _store():
        from client.store import AppStore

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        store.state.members_by_group["7"] = [
            {"id": 2, "member_id": "user:2", "kind": "user", "username": "bob"}
        ]
        return store

    def test_drafts_are_isolated_and_restore_text_mentions_and_reply(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService

        store = self._store()
        first = ConversationKey("group", "7")
        second = ConversationKey("private", "8")
        store.state.messages[first] = [
            {
                "id": 1,
                "conversation_type": "group",
                "conversation_id": "7",
                "sender_id": 2,
                "sender_name": "bob",
                "message_type": "text",
                "content": "original",
            }
        ]
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        pane = self._pane(store, service)
        pane.set_conversation(first, "群聊 A")
        pane._reply_to(1)
        pane.compose.insertPlainText("first draft")

        pane.set_conversation(second, "私聊 B")
        self.assertEqual(pane.compose.toPlainText(), "")
        self.assertIsNone(pane._reply_to_id)
        pane.compose.undo()
        self.assertEqual(pane.compose.toPlainText(), "")
        pane.compose.setPlainText("second draft")

        pane.set_conversation(first, "群聊 A")
        self.assertEqual(pane.compose.toPlainText(), "@bob first draft")
        self.assertEqual(pane._reply_to_id, 1)
        self.assertEqual(pane.compose.mentions()[0]["member_id"], "user:2")
        self.assertEqual(pane.draft_store.get(second).text, "second draft")

    def test_switch_flushes_debounced_draft_and_new_client_restores_it(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "drafts.ini"), QSettings.Format.IniFormat)
            store = self._store()
            service = ChatService(NetworkClient("127.0.0.1", 9), store)
            first = ConversationKey("group", "7")
            second = ConversationKey("private", "8")
            pane = self._pane(store, service, DraftStore(settings))
            pane.set_conversation(first, "群聊 A")
            pane.compose.setPlainText("尚未发送的内容")
            self.assertFalse(settings.value("chat/drafts/anonymous", ""))
            pane.set_conversation(second, "私聊 B")
            self.assertTrue(settings.value("chat/drafts/anonymous", ""))

            reopened = self._pane(store, service, DraftStore(settings))
            reopened.set_conversation(first, "群聊 A")
            self.assertEqual(reopened.compose.toPlainText(), "尚未发送的内容")

    def test_send_success_clears_draft_and_failure_restores_it(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService

        store = self._store()
        key = ConversationKey("group", "7")
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        service.send_text = Mock(return_value="send-1")
        pane = self._pane(store, service)
        pane.set_conversation(key, "群聊 A")
        pane.compose.setPlainText("first message")
        pane._send()
        self.assertEqual(pane.compose.toPlainText(), "")
        self.assertIsNone(pane.draft_store.get(key))

        service.send_text = Mock(return_value="send-2")
        pane.compose.setPlainText("retry message")
        pane._send()
        self.assertIsNone(pane.draft_store.get(key))
        store.state.failed_send = (key, "retry message", [], None)
        pane.update_state("send_failed", store.state)
        self.assertEqual(pane.compose.toPlainText(), "retry message")
        self.assertEqual(pane.draft_store.get(key).text, "retry message")

    def test_immediate_send_failure_keeps_composer_and_draft(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService

        store = self._store()
        key = ConversationKey("group", "7")
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        service.send_text = Mock(return_value=None)
        pane = self._pane(store, service)
        pane.set_conversation(key, "群聊 A")
        pane.compose.setPlainText("cannot send")
        pane._send()
        self.assertEqual(pane.compose.toPlainText(), "cannot send")
        self.assertEqual(pane.draft_store.get(key).text, "cannot send")
        pane._send_feedback_timer.stop()

    def test_clearing_composer_removes_draft_and_preview(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.v2.application import ConversationPane

        store = self._store()
        key = ConversationKey("group", "7")
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        pane = self._pane(store, service)
        changes: list[tuple[ConversationKey, str]] = []
        pane.draft_preview_changed.connect(lambda item, preview: changes.append((item, preview)))
        pane.set_conversation(key, "群聊 A")
        pane.compose.setPlainText("preview me")
        self.assertEqual(changes[-1], (key, "preview me"))

        sidebar = ConversationPane()
        sidebar.set_conversations([ConversationSummary(key, "群聊 A", "最后一条消息")])
        sidebar.set_draft(key, "preview me")
        self.assertEqual(sidebar._cards[key]._preview.text(), "[草稿] preview me")
        self.assertNotIn("最后一条消息", sidebar._cards[key]._preview.text())

        pane.compose.clear()
        self.assertIsNone(pane.draft_store.get(key))
        self.assertEqual(changes[-1], (key, ""))
        sidebar.set_draft(key, "")
        self.assertEqual(sidebar._cards[key]._preview.text(), "最后一条消息")

    def test_ime_composition_defers_draft_replacement(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService

        store = self._store()
        first = ConversationKey("group", "7")
        second = ConversationKey("private", "8")
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        pane = self._pane(store, service)
        pane.set_conversation(first, "群聊 A")
        pane.compose.setPlainText("正在组合")
        pane.draft_store.put(second, Draft.create("B 草稿"))
        pane.compose._composing = True
        pane.set_conversation(second, "私聊 B")
        self.assertEqual(pane._deferred_draft_key, second)
        self.assertEqual(pane.compose.toPlainText(), "正在组合")
        pane.compose._composing = False
        pane.compose.composition_state_changed.emit(False)
        self.assertEqual(pane.compose.toPlainText(), "B 草稿")

    def test_account_switch_clears_visible_conversation_and_keeps_old_account_draft(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "drafts.ini"), QSettings.Format.IniFormat)
            store = self._store()
            key = ConversationKey("group", "7")
            service = ChatService(NetworkClient("127.0.0.1", 9), store)
            pane = self._pane(store, service, DraftStore(settings))
            pane.update_state("auth", store.state)
            pane.set_conversation(key, "群聊 A")
            pane.compose.setPlainText("account one draft")
            pane.flush_drafts()

            store.state.current_user = {"id": 2, "role": "user"}
            pane.update_state("auth", store.state)
            self.assertIsNone(pane.key)
            self.assertEqual(pane.compose.toPlainText(), "")
            self.assertEqual(DraftStore(settings, account="1").get(key).text, "account one draft")

    def test_fast_switch_keeps_latest_conversation_after_stale_history(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.v2.widgets import MessageBubble

        store = self._store()
        keys = [ConversationKey("group", str(value)) for value in (7, 8, 9)]
        for index, key in enumerate(keys, start=1):
            store.state.messages[key] = [
                {
                    "id": index,
                    "conversation_type": key.kind,
                    "conversation_id": key.identifier,
                    "sender_id": 2,
                    "sender_name": "bob",
                    "message_type": "text",
                    "content": f"conversation {key.identifier}",
                }
            ]
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        pane = self._pane(store, service)
        pane.set_conversation(keys[0], "A")
        pane.compose.setPlainText("draft A")
        pane.set_conversation(keys[1], "B")
        pane.compose.setPlainText("draft B")
        pane.set_conversation(keys[2], "C")
        pane.compose.setPlainText("draft C")
        self.app.processEvents()

        for key in keys:
            store.handle_event(
                {
                    "type": "history.result",
                    "payload": {
                        "conversation_type": key.kind,
                        "conversation_id": key.identifier,
                        "messages": [
                            {
                                "id": int(key.identifier),
                                "conversation_type": key.kind,
                                "conversation_id": key.identifier,
                                "sender_id": 2,
                                "sender_name": "bob",
                                "message_type": "text",
                                "content": f"late {key.identifier}",
                            }
                        ],
                    },
                }
            )
            pane.update_state("history", store.state)
        self.app.processEvents()

        self.assertEqual(pane.key, keys[2])
        self.assertEqual(pane.compose.toPlainText(), "draft C")
        labels = [
            label.text()
            for bubble in pane.findChildren(MessageBubble)
            for label in bubble.findChildren(QLabel)
        ]
        self.assertTrue(any("late 9" in text for text in labels))
        self.assertFalse(any("late 7" in text or "late 8" in text for text in labels))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class SwitchPerformanceBehaviorTests(unittest.TestCase):
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

    def test_history_requests_are_deduplicated_and_cached(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore

        store = AppStore()
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        calls: list[str] = []
        counter = [0]
        request_ids: list[str] = []

        def request(kind: str, payload=None):
            counter[0] += 1
            request_id = f"request-{counter[0]}"
            calls.append(kind)
            request_ids.append(request_id)
            store.set_pending(request_id, kind)
            return request_id

        service.request = request
        key = ConversationKey("group", "7")
        self.assertTrue(service.open_conversation(key))
        self.assertEqual(calls, ["history.sync", "group.members", "agent.group.list"])
        calls.clear()
        self.assertFalse(service.open_conversation(key))
        self.assertEqual(calls, [])

        store.handle_event(
            {
                "type": "history.result",
                "request_id": request_ids[0],
                "payload": {
                    "conversation_type": "group",
                    "conversation_id": "7",
                    "messages": [],
                },
            }
        )
        store.handle_event(
            {
                "type": "group.members",
                "request_id": request_ids[1],
                "payload": {"group_id": "7", "members": []},
            }
        )
        store.handle_event(
            {
                "type": "agent.group.list",
                "request_id": request_ids[2],
                "payload": {"group_id": "7", "agents": []},
            }
        )
        calls.clear()
        self.assertFalse(service.open_conversation(key))
        self.assertEqual(calls, [])

        store.state.history_loaded_at[key] = time.monotonic() - 61
        self.assertTrue(service.open_conversation(key))
        self.assertEqual(calls, ["history.sync"])

    def test_sidebar_and_message_rows_are_reused(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane, ConversationPane

        summaries = [
            ConversationSummary(
                ConversationKey("group" if index % 2 else "private", str(index)),
                f"会话 {index}",
                "latest",
                1700000000 + index,
            )
            for index in range(1, 101)
        ]
        sidebar = ConversationPane()
        sidebar.set_conversations(summaries)
        card_ids = {key: id(card) for key, card in sidebar._cards.items()}
        sidebar.set_conversations(summaries)
        self.assertEqual({key: id(card) for key, card in sidebar._cards.items()}, card_ids)

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.messages[key] = [
            {
                "id": message_id,
                "conversation_type": "group",
                "conversation_id": "7",
                "sender_id": 2,
                "sender_name": "bob",
                "message_type": "text",
                "content": f"message {message_id}",
            }
            for message_id in range(1, 51)
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(key, "群聊")
        self.app.processEvents()
        row_ids = {message_id: id(row) for message_id, row in pane._message_rows.items()}
        pane.set_conversation(key, "群聊")
        self.assertEqual(
            {message_id: id(row) for message_id, row in pane._message_rows.items()},
            row_ids,
        )

        for message_id in range(51, 56):
            store.state.messages[key].append(
                {
                    "id": message_id,
                    "conversation_type": "group",
                    "conversation_id": "7",
                    "sender_id": 2,
                    "sender_name": "bob",
                    "message_type": "text",
                    "content": f"message {message_id}",
                }
            )
        pane.update_state("messages", store.state)
        self.app.processEvents()
        self.assertEqual(
            {message_id: id(pane._message_rows[message_id]) for message_id in row_ids},
            row_ids,
        )
        self.assertIn(55, pane._message_rows)

