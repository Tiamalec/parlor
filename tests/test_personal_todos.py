from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from client.models import AppState
from client.store import AppStore
from server.app import ChatServer
from server.config import Config
from server.database import Database, DatabaseError
from server.todos import TodoService
from tests.test_server_integration import WireClient


class TodoServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = Database(root / "chat.sqlite3", root / "uploads")
        self.db.initialize()
        self.alice = self.db.register_user("alice", "password-a")["id"]
        self.bob = self.db.register_user("bob01", "password-b")["id"]
        self.todos = TodoService(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_crud_filters_sort_and_persistence_are_account_scoped(self) -> None:
        due = datetime.now().timestamp() + 3600
        first = self.todos.create(self.alice, title="  实验报告  ", due_at=due, priority="high")
        second = self.todos.create(self.alice, title="整理资料", priority="low")
        self.assertEqual(first["todo"]["title"], "实验报告")
        self.assertEqual([item["id"] for item in self.todos.list(self.alice)["todos"]],
                         [first["todo"]["id"], second["todo"]["id"]])
        self.assertEqual(self.todos.list(self.bob)["todos"], [])
        with self.assertRaises(DatabaseError):
            self.todos.update(self.bob, first["todo"]["id"], title="窃改")
        with self.assertRaises(DatabaseError):
            self.todos.delete(self.bob, first["todo"]["id"])
        updated = self.todos.update(self.alice, first["todo"]["id"], description="交给老师", priority="normal")
        self.assertEqual(updated["todo"]["description"], "交给老师")
        completed = self.todos.complete(self.alice, first["todo"]["id"])
        self.assertTrue(completed["todo"]["completed"])
        self.assertEqual(len(self.todos.list(self.alice, completed=True)["todos"]), 1)
        self.assertFalse(self.todos.complete(self.alice, first["todo"]["id"], False)["todo"]["completed"])
        self.assertEqual(len(self.todos.list(self.alice, due_before=due + 1)["todos"]), 1)
        self.db.initialize()
        self.assertEqual(len(TodoService(self.db).list(self.alice)["todos"]), 2)
        deleted = self.todos.delete(self.alice, second["todo"]["id"])
        self.assertEqual(deleted["revision"], 6)
        self.assertEqual(len(self.todos.list(self.alice)["todos"]), 1)

    def test_source_message_requires_access_at_create_and_jump(self) -> None:
        group = self.db.create_group("待办测试群", self.bob)
        self.db.join_group(group["id"], self.alice)
        message = self.db.save_message("group", str(group["id"]), self.bob, "bob01", "text", "开会")
        linked = self.todos.create(
            self.alice, title="开会", source_type="chat_message",
            source_conversation_type="group", source_conversation_id=str(group["id"]),
            source_message_id=message["id"],
        )["todo"]
        self.assertEqual(self.todos.resolve_message(self.alice, linked["id"])["message_id"], message["id"])
        with self.assertRaises(DatabaseError):
            self.todos.resolve_message(self.bob, linked["id"])
        self.db.leave_group(group["id"], self.alice)
        with self.assertRaises(DatabaseError):
            self.todos.resolve_message(self.alice, linked["id"])
        with self.assertRaises(DatabaseError):
            self.todos.create(
                self.alice, title="无权", source_type="chat_message",
                source_conversation_type="group", source_conversation_id=str(group["id"]),
                source_message_id=message["id"],
            )

    def test_priority_sort_can_override_due_order(self) -> None:
        now = datetime.now().timestamp()
        early = self.todos.create(self.alice, title="先到期", priority="low", due_at=now + 3600)["todo"]
        urgent = self.todos.create(self.alice, title="高优先级", priority="high", due_at=now + 7200)["todo"]
        self.assertEqual([item["id"] for item in self.todos.list(self.alice, sort="due")["todos"]],
                         [early["id"], urgent["id"]])
        self.assertEqual([item["id"] for item in self.todos.list(self.alice, sort="priority")["todos"]],
                         [urgent["id"], early["id"]])

    def test_invalid_input_is_rejected(self) -> None:
        for values in ({"title": ""}, {"title": "x", "priority": "urgent"},
                       {"title": "x", "due_at": float("nan")},
                       {"title": "x", "source_type": "agent_extracted"}):
            with self.subTest(values=values), self.assertRaises(DatabaseError):
                self.todos.create(self.alice, **values)

    def test_extracted_todos_are_idempotent_and_version_checked(self) -> None:
        group = self.db.create_group("幂等测试群", self.alice)
        message = self.db.save_message(
            "group", str(group["id"]), self.alice, "alice", "text", "我明天提交报告"
        )
        payload = dict(
            title="提交报告",
            source_type="agent_extracted",
            source_conversation_type="group",
            source_conversation_id=str(group["id"]),
            source_message_id=message["id"],
            actor="agent",
            agent_id="agent",
        )
        first = self.todos.create(self.alice, **payload)
        second = self.todos.create(self.alice, **payload)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["todo"]["id"], second["todo"]["id"])
        self.assertEqual(len(self.todos.list(self.alice)["todos"]), 1)
        version = first["todo"]["version"]
        updated = self.todos.update(
            self.alice,
            first["todo"]["id"],
            actor="manual",
            expected_version=version,
            priority="high",
        )
        self.assertGreater(updated["todo"]["version"], version)
        with self.assertRaises(DatabaseError):
            self.todos.update(
                self.alice,
                first["todo"]["id"],
                actor="manual",
                expected_version=version,
                description="stale",
            )

    def test_agent_cannot_overwrite_manual_edit_without_explicit_authorization(self) -> None:
        todo = self.todos.create(self.alice, title="实验报告")["todo"]
        edited = self.todos.update(
            self.alice, todo["id"], actor="manual", description="我手动改过"
        )["todo"]
        with self.assertRaises(DatabaseError):
            self.todos.update(
                self.alice,
                todo["id"],
                actor="agent",
                expected_version=edited["version"],
                priority="high",
            )
        changed = self.todos.update(
            self.alice,
            todo["id"],
            actor="agent",
            allow_manual_override=True,
            expected_version=edited["version"],
            priority="high",
        )["todo"]
        self.assertEqual(changed["priority"], "high")

    def test_same_titled_extracted_events_on_different_dates_are_distinct(self) -> None:
        group = self.db.create_group("同日标题测试群", self.alice)
        message = self.db.save_message(
            "group", str(group["id"]), self.alice, "alice", "text",
            "我明天参加七神会议，后天还有一场七神会议",
        )
        first_due = datetime(2026, 11, 25, 8, 0).timestamp()
        second_due = datetime(2026, 11, 26, 8, 0).timestamp()
        common = dict(
            title="参加七神会议",
            source_type="agent_extracted",
            source_conversation_type="group",
            source_conversation_id=str(group["id"]),
            source_message_id=message["id"],
            actor="agent",
            agent_id="agent",
        )
        first = self.todos.create(self.alice, due_at=first_due, **common)
        second = self.todos.create(self.alice, due_at=second_due, **common)
        self.assertTrue(first["created"])
        self.assertTrue(second["created"])
        self.assertNotEqual(first["todo"]["id"], second["todo"]["id"])
        self.assertFalse(
            self.todos.create(self.alice, due_at=first_due, **common)["created"]
        )
        self.assertFalse(
            self.todos.create(self.alice, due_at=second_due, **common)["created"]
        )

    def test_action_prefix_variants_match_for_extracted_deduplication(self) -> None:
        group = self.db.create_group("前缀查重群", self.alice)
        message = self.db.save_message(
            "group", str(group["id"]), self.alice, "alice", "text",
            "我明天参加七神会议",
        )
        due = datetime(2026, 11, 25, 8, 0).timestamp()
        common = dict(
            due_at=due,
            source_type="agent_extracted",
            source_conversation_type="group",
            source_conversation_id=str(group["id"]),
            source_message_id=message["id"],
            actor="agent",
            agent_id="agent",
        )
        first = self.todos.create(self.alice, title="参加七神会议", **common)
        second = self.todos.create(self.alice, title="七神会议", **common)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["todo"]["id"], second["todo"]["id"])


class TodoWireTests(unittest.TestCase):
    def test_same_account_realtime_sync_and_reconnect_snapshot(self) -> None:
        asyncio.run(self._scenario())

    async def _scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(Config.from_env(), host="127.0.0.1", port=0, data_dir=root,
                             database_path=root / "chat.sqlite3", uploads_dir=root / "uploads",
                             admin_username=None, admin_password=None,
                             agent_base_url="http://127.0.0.1:9/v1")
            server = ChatServer(config)
            await server.start()
            port = int(server._server.sockets[0].getsockname()[1])
            alice = await WireClient.connect(port)
            second = await WireClient.connect(port)
            bob = await WireClient.connect(port)
            try:
                request = await alice.send("auth.register", {"username": "alice", "password": "password-a"})
                await alice.until("auth.result", request_id=request)
                request = await second.send("auth.login", {"username": "alice", "password": "password-a"})
                await second.until("auth.result", request_id=request)
                request = await bob.send("auth.register", {"username": "bob01", "password": "password-b"})
                await bob.until("auth.result", request_id=request)
                request = await alice.send("todo.create", {"title": "伪造来源", "source_type": "agent_extracted"})
                await alice.until("error", request_id=request)
                request = await alice.send("todo.create", {"title": "同步测试", "priority": "high"})
                created = (await alice.until("todo.created", request_id=request))["payload"]
                todo_id = created["todo"]["id"]
                pushed = (await second.until("todo.changed"))["payload"]
                self.assertEqual(pushed["todo"]["id"], todo_id)
                request = await bob.send("todo.list")
                self.assertEqual((await bob.until("todo.list", request_id=request))["payload"]["todos"], [])
                request = await bob.send("todo.complete", {"todo_id": todo_id})
                await bob.until("error", request_id=request)
                request = await second.send("todo.complete", {"todo_id": todo_id, "completed": True})
                await second.until("todo.updated", request_id=request)
                changed = (await alice.until("todo.changed"))["payload"]
                if not changed["todo"]["completed"]:
                    changed = (await alice.until("todo.changed"))["payload"]
                self.assertTrue(changed["todo"]["completed"])
                await second.close()
                reconnected = await WireClient.connect(port)
                try:
                    request = await reconnected.send("auth.login", {"username": "alice", "password": "password-a"})
                    await reconnected.until("auth.result", request_id=request)
                    request = await reconnected.send("todo.list")
                    snapshot = (await reconnected.until("todo.list", request_id=request))["payload"]
                    self.assertEqual(snapshot["todos"][0]["id"], todo_id)
                    self.assertTrue(snapshot["todos"][0]["completed"])
                    request = await reconnected.send("todo.delete", {"todo_id": todo_id})
                    await reconnected.until("todo.deleted", request_id=request)
                    request = await alice.send("todo.list")
                    self.assertEqual((await alice.until("todo.list", request_id=request))["payload"]["todos"], [])
                finally:
                    await reconnected.close()
                alice_user = server.database.authenticate("alice", "password-a")
                bob_user = server.database.authenticate("bob01", "password-b")
                group = server.database.create_group("导入测试群", alice_user["id"])
                server.database.join_group(group["id"], bob_user["id"])
                source = server.database.save_message(
                    "group", str(group["id"]), alice_user["id"], "alice", "text",
                    "我明天提交报告，bob01 调整部署",
                )
                card_message = server.database.save_message(
                    "group", str(group["id"]), None, "AI Agent", "agent",
                    "## 待办提取", sender_kind="agent", content_format="markdown",
                    card_payload={
                        "kind": "todo", "title": "待办事项", "requester_id": alice_user["id"],
                        "tasks": [
                            {"title": "提交报告", "owner": "alice", "owner_kind": "current",
                             "assignee_user_id": alice_user["id"], "sources": [source["id"]],
                             "priority": "high", "status": "open", "due_at": None},
                            {"title": "调整部署", "owner": "bob01", "owner_kind": "other",
                             "assignee_user_id": bob_user["id"], "sources": [source["id"]],
                             "priority": "normal", "status": "open", "due_at": None},
                        ],
                    },
                )
                request = await bob.send(
                    "todo.import",
                    {"message_id": card_message["id"], "task_indices": [0]},
                )
                bob_import = (await bob.until("todo.imported", request_id=request))["payload"]
                self.assertEqual(bob_import["created"], 0)
                self.assertEqual(bob_import["skipped"], 1)
                request = await alice.send(
                    "todo.import",
                    {"message_id": card_message["id"], "task_indices": [0, 1]},
                )
                imported = (await alice.until("todo.imported", request_id=request))["payload"]
                self.assertEqual(imported["created"], 1)
                self.assertEqual(imported["skipped"], 1)
                self.assertEqual(imported["failed"], 0)
                alice_todos = server.todos.list(alice_user["id"])["todos"]
                self.assertEqual([item["title"] for item in alice_todos], ["提交报告"])
                request = await alice.send(
                    "todo.import",
                    {"message_id": card_message["id"], "task_indices": [0]},
                )
                repeated = (await alice.until("todo.imported", request_id=request))["payload"]
                self.assertEqual(repeated["created"], 0)
                self.assertEqual(repeated["matched"], 1)
                self.assertEqual(len(server.todos.list(alice_user["id"])["todos"]), 1)
                request = await bob.send("todo.list")
                self.assertEqual(
                    (await bob.until("todo.list", request_id=request))["payload"]["todos"],
                    [],
                )
            finally:
                await alice.close()
                await bob.close()
                await server.close()


class TodoStoreTests(unittest.TestCase):
    def test_snapshot_revision_and_account_change(self) -> None:
        store = AppStore()
        store.handle_event({"type": "auth.result", "payload": {"ok": True, "user": {"id": 1}}})
        store.handle_event({"type": "todo.list", "payload": {"revision": 1, "todos": [{"id": 1, "title": "A"}]}})
        store.handle_event({"type": "todo.changed", "payload": {"revision": 2, "todo": {"id": 1, "title": "B"}}})
        store.handle_event({"type": "todo.list", "payload": {"revision": 1, "todos": []}})
        self.assertEqual(store.state.todos[0]["title"], "B")
        store.handle_event({"type": "todo.removed", "payload": {"revision": 4, "todo_id": 1}})
        self.assertTrue(store.state.todo_needs_refresh)
        store.handle_event(
            {
                "type": "todo.changed",
                "payload": {
                    "revision": 5,
                    "todo": {"id": 9, "owner_id": 99, "title": "not mine"},
                },
            }
        )
        self.assertNotIn(9, [item["id"] for item in store.state.todos])
        store.handle_event(
            {
                "type": "todo.imported",
                "payload": {"message_id": 12, "status": "success", "outcomes": []},
            }
        )
        self.assertEqual(store.state.todo_import_results[12]["status"], "success")
        store.handle_event({"type": "auth.result", "payload": {"ok": True, "user": {"id": 2}}})
        self.assertEqual(store.state.todos, [])
        self.assertEqual(store.state.todo_revision, 0)
        self.assertEqual(store.state.todo_import_results, {})


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TodoUiTests(unittest.TestCase):
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

    def test_page_filters_quick_add_and_details(self) -> None:
        from client.v2.personal_todo import PersonalTodoPage

        service = Mock()
        service.create_todo.return_value = "request"
        page = PersonalTodoPage(service)
        state = AppState()
        state.connected = state.authenticated = state.todos_loaded = True
        today = datetime.now().replace(hour=18, minute=0, second=0, microsecond=0)
        state.todos = [
            {"id": 1, "title": "今天", "description": "测试", "due_at": today.timestamp(),
             "priority": "high", "completed": False, "source_type": "manual", "source_message_id": None},
            {"id": 2, "title": "明天", "description": "", "due_at": (today + timedelta(days=1)).timestamp(),
             "priority": "low", "completed": False, "source_type": "manual", "source_message_id": None},
        ]
        page.update_state("todos", state)
        page.quick_title.setText("新增")
        page.quick_title.returnPressed.emit()
        service.create_todo.assert_called_once_with("新增")
        page.category.setCurrentIndex(1)
        self.assertEqual([item["id"] for item in page._filtered()], [1])
        page.category.setCurrentIndex(2)
        self.assertEqual([item["id"] for item in page._filtered()], [2])
        page.category.setCurrentIndex(0)
        page.priority_filter.setCurrentIndex(1)
        self.assertEqual([item["id"] for item in page._filtered()], [1])
        page.priority_filter.setCurrentIndex(0)
        self.assertEqual([key for key, _label, _items in page._sections(page._filtered())],
                         ["today", "upcoming"])
        page.resize(640, 520)
        page.show()
        page._select(1)
        self.app.processEvents()
        self.assertTrue(page.detail_scroll.isVisible())
        self.assertEqual(page.height(), 520)
        self.assertFalse(page.quick_bar.isVisible())
        self.assertFalse(page.filter_bar.isVisible())
        page.edit_description.setPlainText("更新")
        page.save_button.click()
        self.assertEqual(service.update_todo.call_args.kwargs["description"], "更新")
        page.edit_status.setCurrentIndex(1)
        service.complete_todo.assert_called_once_with(1, True, None)
        page._close_detail()
        self.app.processEvents()
        self.assertTrue(page.quick_bar.isVisible())
        self.assertTrue(page.filter_bar.isVisible())
        self.assertTrue(page.scroll.isVisible())
        page._toggle_section("today")
        self.assertIn("today", page._collapsed)
        page.close()

    def test_full_create_and_theme_choices_have_no_native_combo_arrow(self) -> None:
        from PySide6.QtWidgets import QComboBox, QLineEdit
        from client.v2.personal_todo import PersonalTodoPage, TodoChoiceButton

        service = Mock()
        service.create_todo.return_value = "request"
        page = PersonalTodoPage(service)
        page.update_state("auth", AppState(connected=True, authenticated=True, todos_loaded=True))
        page.resize(640, 520)
        page.show()
        page.quick_title.setText("完整创建")
        page.add_button.click()
        self.app.processEvents()
        self.assertTrue(page.detail_scroll.isVisible())
        self.assertTrue(page._editing_new)
        self.assertEqual(page.edit_title.text(), "完整创建")
        self.assertFalse(page.scroll.isVisible())
        self.assertFalse(page.findChildren(QComboBox))
        self.assertTrue(all(isinstance(choice, TodoChoiceButton) for choice in
                            (page.category, page.priority_filter, page.sort,
                             page.edit_priority, page.edit_status)))
        self.assertIsInstance(page.edit_due, QLineEdit)
        page.edit_description.setPlainText("说明")
        page.edit_priority.setCurrentIndex(1)
        page.edit_due.setText("2026-12-01 09:30")
        due = datetime.strptime(page.edit_due.text(), "%Y-%m-%d %H:%M").timestamp()
        page.save_button.click()
        service.create_todo.assert_called_once_with("完整创建", "说明", due, "high")
        page._new()
        page.edit_title.setText("无截止时间")
        page.save_button.click()
        service.create_todo.assert_called_with("无截止时间", "", None, "normal")
        page.close()

    def test_invalid_due_does_not_submit(self) -> None:
        from client.v2.personal_todo import PersonalTodoPage

        service = Mock()
        page = PersonalTodoPage(service)
        page.update_state("auth", AppState(connected=True, authenticated=True, todos_loaded=True))
        page._new()
        page.edit_title.setText("安排会议")
        page.edit_due.setText("不是日期")
        page._save()
        service.create_todo.assert_not_called()
        self.assertIn("截止时间格式", page.feedback.text())
        page.close()

    def test_completed_group_is_collapsed_by_default_and_category_restores_rows(self) -> None:
        from PySide6.QtWidgets import QFrame, QPushButton

        from client.v2.personal_todo import PersonalTodoPage

        service = Mock()
        page = PersonalTodoPage(service)
        state = AppState(connected=True, authenticated=True, todos_loaded=True)
        state.todos = [
            {"id": 1, "title": "未完成", "due_at": None, "priority": "normal",
             "completed": False, "source_type": "manual"},
            {"id": 2, "title": "已完成", "due_at": None, "priority": "low",
             "completed": True, "source_type": "manual"},
        ]
        page.update_state("todos", state)
        page.resize(1100, 700)
        page.show()
        self.app.processEvents()
        self.assertNotIn(
            "已完成",
            [button.text() for button in page.findChildren(QPushButton, "TodoTitle")],
        )
        self.assertEqual(
            [row.property("completed") for row in page.findChildren(QFrame, "TodoRow")],
            ["false"],
        )
        page.category.setCurrentIndex(3)
        self.app.processEvents()
        rows = page.findChildren(QFrame, "TodoRow")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].property("completed"), "true")
        page.close()

    def test_resolved_message_uses_existing_chat_navigation(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.v2.application import Workspace

        store = AppStore()
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        service.open_conversation = Mock(return_value=False)
        service.load_result = Mock(return_value="history-request")
        workspace = Workspace(service, store, "127.0.0.1", 9)
        store.state.current_user = {"id": 1, "role": "user"}
        store.state.todo_target = {"todo_id": 3, "conversation_type": "group",
                                   "conversation_id": "7", "message_id": 19}
        workspace.update_state("todo_target", store.state)
        self.assertEqual(workspace.pages.currentIndex(), 0)
        self.assertEqual(workspace.chat._focus_message_id, 19)
        service.load_result.assert_called_once()
        self.assertIsNone(store.state.todo_target)
        workspace.close()

    def test_synced_card_opens_and_selects_personal_todo(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.v2.application import Workspace

        store = AppStore()
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        store.state.connected = store.state.authenticated = store.state.todos_loaded = True
        store.state.current_user = {"id": 1, "role": "user"}
        store.state.todos = [
            {
                "id": 8,
                "owner_id": 1,
                "title": "来自聊天",
                "due_at": None,
                "priority": "high",
                "completed": False,
                "source_type": "agent_extracted",
                "version": 1,
            }
        ]
        workspace = Workspace(service, store, "127.0.0.1", 9)
        workspace.show()
        workspace._open_todo(8)
        self.app.processEvents()
        self.assertEqual(workspace.pages.currentIndex(), 4)
        self.assertEqual(workspace.todos._selected_id, 8)
        self.assertTrue(workspace.todos.detail_scroll.isVisible())
        workspace.close()
