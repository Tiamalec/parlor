from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QColor, QImage, QPalette
    from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QWidget
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

    def test_auth_page_and_conversation_card_layout(self) -> None:
        from client.v2.application import AuthPage
        from client.v2.widgets import ConversationCard

        auth = AuthPage("127.0.0.1", 8765)
        self.assertEqual(auth.submit.text(), "登录")
        auth.register_mode.click()
        self.assertEqual(auth.submit.text(), "创建账号")
        card = ConversationCard(
            ConversationSummary(ConversationKey("group", "1"), "项目组", "最后消息", 1, 3)
        )
        self.assertEqual(card.height(), 64)
        card.set_selected(True)
        self.assertTrue(card.property("selected"))

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
        profile = resolve_profile(state, 2, state.members_by_group["7"][0])
        self.assertEqual(profile.user_id, 2)
        self.assertEqual(profile.avatar_version, 3)
        self.assertEqual(profile.image_data, "image")
        self.assertEqual(profile.group_role, "owner")
        self.assertEqual(profile.available_actions, ("mute",))

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
            }
        ]
        page = AICenterPage(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        page.update_state("conversations", store.state)
        card = page.task_rows.itemAt(0).widget()
        self.assertIsInstance(card, AgentTaskCard)
        labels = [label.text() for label in card.findChildren(type(page.status))]
        self.assertIn("总结会话", labels)
        self.assertTrue(any("项目组" in text for text in labels))

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


if __name__ == "__main__":
    unittest.main()
