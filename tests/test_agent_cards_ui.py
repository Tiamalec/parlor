from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QSettings
    from PySide6.QtWidgets import QApplication, QCheckBox, QLabel
except ImportError:  # pragma: no cover - optional until V2 dependencies are installed
    QApplication = None  # type: ignore[assignment]

from client.models import ConversationKey


def _summary(**overrides):
    card = {
        "kind": "summary",
        "title": "群聊总结",
        "overview": "团队完成 TCP 联调，灰度窗口仍待确认。",
        "conclusions": [
            {"text": "TCP 联调已完成", "sources": [12]},
            {"text": "服务端使用 asyncio", "sources": [14]},
            {"text": "灰度先覆盖 10% 用户", "sources": [18]},
            {"text": "周四前补齐监控面板", "sources": [20]},
        ],
        "pending": [
            {"text": "灰度窗口定在周四还是周五尚未决定", "sources": [22]},
            {"text": "回滚责任人尚未确认", "sources": [23]},
        ],
        "sources": [12, 23],
        "range": {"start_id": 12, "end_id": 23},
        "generated_at": 0,
        "parse_error": False,
    }
    card.update(overrides)
    return card


def _todo(**overrides):
    card = {
        "kind": "todo",
        "title": "待办事项",
        "tasks": [
            {"title": "补充演示录屏", "owner": "Alice", "due": "周四", "group": "confirmed"},
            {"title": "更新部署文档", "owner": "Bob", "due": "", "group": "confirmed"},
            {"title": "整理监控告警阈值", "owner": "", "due": "", "group": "suggested"},
            {"title": "确认灰度发布窗口", "owner": "Alice", "due": "周五前", "group": "confirmed"},
        ],
        "unresolved": ["回滚负责人尚未确认"],
        "sources": [12, 22],
        "range": {"start_id": 12, "end_id": 23},
        "generated_at": 0,
        "parse_error": False,
    }
    card.update(overrides)
    return card


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class AgentCardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        from client.v2.theme import ThemeMode, install_theme_manager

        cls.settings_dir = tempfile.TemporaryDirectory()
        cls.manager = install_theme_manager(
            cls.app,
            QSettings(
                str(Path(cls.settings_dir.name) / "chatroom-card-ui-tests.ini"),
                QSettings.Format.IniFormat,
            ),
        )
        cls.manager.set_mode(ThemeMode.LIGHT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.settings_dir.cleanup()

    def tearDown(self) -> None:
        self.app.processEvents()
        for widget in self.app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_summary_card_collapses_to_three_points_and_expands(self) -> None:
        from client.v2.agent_cards import SummaryCard

        card = SummaryCard(_summary())
        card.show()
        self.app.processEvents()
        self.assertEqual(card.objectName(), "AgentCard")
        self.assertLessEqual(card.maximumWidth(), 640)
        self.assertLessEqual(card.minimumWidth(), 400)
        self.assertEqual(len(card.findChildren(QLabel, "SummaryPoint")), 3)
        self.assertEqual(len(card.findChildren(QLabel, "CardPendingPoint")), 0)
        self.assertTrue(card._expand_button.isVisible())
        collapsed_height = card.height()
        card.set_expanded(True)
        self.app.processEvents()
        self.assertEqual(len(card.findChildren(QLabel, "SummaryPoint")), 4)
        self.assertEqual(len(card.findChildren(QLabel, "CardPendingPoint")), 2)
        self.assertGreater(card.height(), collapsed_height)
        self.assertEqual(card._expand_button.text(), "收起")
        card.set_expanded(False)
        self.app.processEvents()
        self.assertEqual(len(card.findChildren(QLabel, "SummaryPoint")), 3)

    def test_summary_and_todo_source_actions_emit_valid_ids(self) -> None:
        from client.v2.agent_cards import SummaryCard, TodoCard

        summary = SummaryCard(_summary())
        todo = TodoCard(_todo())
        summary_ids: list[list[int]] = []
        todo_ids: list[list[int]] = []
        summary.sources_requested.connect(summary_ids.append)
        todo.sources_requested.connect(todo_ids.append)
        summary._source_button.click()
        todo._source_button.click()
        self.assertEqual(summary_ids, [[12, 23]])
        self.assertEqual(todo_ids, [[12, 22]])

    def test_todo_card_is_read_only_and_hides_missing_owner_or_due(self) -> None:
        from client.v2.agent_cards import TodoCard

        card = TodoCard(_todo())
        card.show()
        self.app.processEvents()
        self.assertEqual(len(card.findChildren(QCheckBox)), 0)
        titles = [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")]
        self.assertEqual(titles, ["补充演示录屏", "更新部署文档", "整理监控告警阈值"])
        meta = " ".join(label.text() for label in card.findChildren(QLabel, "CardTaskMeta"))
        self.assertIn("负责人 Alice", meta)
        self.assertIn("截止 周四", meta)
        self.assertNotIn("未明确", meta)
        card.set_expanded(True)
        self.app.processEvents()
        titles = [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")]
        self.assertEqual(len(titles), 4)
        self.assertIn("确认灰度发布窗口", titles)
        self.assertEqual(len(card.findChildren(QLabel, "CardPendingPoint")), 1)

        compact = TodoCard(
            _todo(
                tasks=[
                    {"title": "补充演示录屏", "owner": "Alice", "due": "", "group": "confirmed"},
                    {"title": "更新部署文档", "group": "confirmed"},
                ],
                unresolved=[],
            )
        )
        compact.show()
        self.app.processEvents()
        self.assertTrue(compact.footer.isVisible())
        self.assertTrue(compact._copy_button.isVisible())
        self.assertFalse(compact._expand_button.isVisible())

    def test_message_bubble_dispatches_by_card_metadata_not_text_keywords(self) -> None:
        from client.v2.agent_cards import SummaryCard, TodoCard
        from client.v2.widgets import MessageBubble

        summary_bubble = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "markdown",
                "content": "## 群聊总结\n\n### 主要话题\n- 不应直接显示",
                "card": _summary(),
            },
            mine=False,
        )
        self.assertIsInstance(summary_bubble.agent_card, SummaryCard)
        self.assertIsNone(summary_bubble.findChild(QLabel, "AgentMarkdownContent"))
        self.assertEqual(summary_bubble.property("messageType"), "summary")
        self.assertEqual(summary_bubble.layout().contentsMargins().left(), 0)

        todo_bubble = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "markdown",
                "content": "## 待办提取\n\n- 不应直接显示",
                "card": _todo(),
            },
            mine=False,
        )
        self.assertIsInstance(todo_bubble.agent_card, TodoCard)
        self.assertIsNone(todo_bubble.findChild(QLabel, "AgentMarkdownContent"))

        legacy = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "markdown",
                "content": "## 群聊总结\n\n### 主要话题\n- 旧消息",
            },
            mine=False,
        )
        self.assertIsNone(getattr(legacy, "agent_card", None))
        self.assertIsNotNone(legacy.findChild(QLabel, "AgentMarkdownContent"))

        keyword_only = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "plain",
                "content": "群聊总结和待办提取只是普通文本。",
            },
            mine=False,
        )
        self.assertIsNone(getattr(keyword_only, "agent_card", None))

    def test_chat_pane_renders_cards_and_keeps_narrow_layout_inside_bounds(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.agent_cards import SummaryCard, TodoCard
        from client.v2.application import ChatPane

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.current_conversation = key
        store.state.messages[key] = [
            {"id": 1, "sender_id": 2, "sender_name": "Alice", "message_type": "text", "content": "开始联调", "created_at": 1000},
            {"id": 2, "sender_id": None, "sender_name": "AI Agent", "sender_kind": "agent", "sender_agent_id": "agent", "message_type": "agent", "content_format": "markdown", "content": "## 群聊总结", "card": _summary(), "created_at": 1100},
            {"id": 3, "sender_id": None, "sender_name": "AI Agent", "sender_kind": "agent", "sender_agent_id": "agent", "message_type": "agent", "content_format": "markdown", "content": "## 待办提取", "card": _todo(), "created_at": 1200},
            {"id": 4, "sender_id": None, "sender_name": "AI Agent", "sender_kind": "agent", "sender_agent_id": "agent", "message_type": "agent", "content_format": "plain", "content": "群聊总结只是普通回复", "created_at": 1300},
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(key, "项目组")
        pane.resize(470, 760)
        pane.show()
        self.app.processEvents()
        self.app.processEvents()
        summaries = pane.findChildren(SummaryCard)
        todos = pane.findChildren(TodoCard)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(todos), 1)
        self.assertLessEqual(summaries[0].width(), 470)
        self.assertLessEqual(todos[0].width(), 470)
        sender_labels = [label.text() for label in pane.findChildren(QLabel, "AgentMessageSender")]
        self.assertIn("AI Agent · 总结", sender_labels)
        self.assertIn("AI Agent · 待办", sender_labels)

    def test_invalid_or_empty_card_payload_does_not_crash(self) -> None:
        from client.v2.agent_cards import SummaryCard, TodoCard
        from client.v2.widgets import MessageBubble
        from PySide6.QtCore import Qt

        empty = MessageBubble({"message_type": "agent", "card": {"kind": "summary"}}, mine=False)
        self.assertIsInstance(empty.agent_card, SummaryCard)
        self.assertTrue(empty.agent_card.findChildren(QLabel, "CardEmpty"))

        malformed = SummaryCard(
            {"kind": "summary", "overview": 42, "conclusions": "bad", "pending": [None, {"text": 3}]}
        )
        self.assertTrue(malformed.findChildren(QLabel, "CardEmpty"))

        todo = TodoCard({"kind": "todo", "tasks": ["bad", {"title": ""}], "unresolved": "bad"})
        self.assertTrue(todo.findChildren(QLabel, "CardEmpty"))

        markup = SummaryCard(
            _summary(
                title="<b>标题</b>",
                overview="<b>不应加粗</b>",
                conclusions=[{"text": "<script>alert(1)</script>", "sources": []}],
                pending=[],
            )
        )
        for label in markup.findChildren(QLabel):
            if label.objectName() in {"CardTitle", "CardBody", "SummaryPoint"}:
                self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_long_text_wraps_within_card_height_and_expands_to_full_copy(self) -> None:
        from client.v2.agent_cards import SummaryCard

        long_overview = "团队围绕发布计划进行了讨论。" * 20
        long_point = "需要确认灰度窗口、回滚责任人和监控面板的完成时间。" * 8
        card = SummaryCard(
            _summary(
                overview=long_overview,
                conclusions=[{"text": long_point, "sources": [12]}],
                pending=[],
            )
        )
        card.resize(420, 700)
        card.show()
        self.app.processEvents()
        self.app.processEvents()
        body = card.findChildren(QLabel, "CardBody")[0]
        point = card.findChildren(QLabel, "SummaryPoint")[0]
        self.assertGreaterEqual(body.height(), body.heightForWidth(body.width()))
        self.assertGreaterEqual(point.height(), point.heightForWidth(point.width()))
        self.assertLessEqual(card.width(), 420)
        self.assertNotEqual(body.text(), long_overview)
        card.set_expanded(True)
        self.app.processEvents()
        self.app.processEvents()
        body = card.findChildren(QLabel, "CardBody")[0]
        point = card.findChildren(QLabel, "SummaryPoint")[0]
        self.assertEqual(body.text(), long_overview)
        self.assertEqual(point.text(), long_point)
        self.assertGreaterEqual(body.height(), body.heightForWidth(body.width()))


if __name__ == "__main__":
    unittest.main()
