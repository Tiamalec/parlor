from __future__ import annotations

import asyncio
import base64
from contextlib import closing
import json
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from server.admin.auth import AdminSessionStore
from server.admin.auth import AdminAuthError
from server.admin.config_store import ProviderConfigStore
from server.admin.service import AdminRequestError, AdminService
from server.admin.http import AdminHttpServer, Request
from server.agent import AgentService
from server.app import ChatServer
from server.config import Config
from server.database import Database, DatabaseError
from server.stickers import extract_sticker_directive, select_sticker, validate_sticker_image
from server.tools import DEFAULT_AGENT_TOOL_NAMES
from client.models import ConversationKey
from client.store import AppStore


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/SAAAAABJRU5ErkJggg=="
)


class FixedProvider:
    async def generate(self, _system: str, _context: str) -> str:
        return "好的，给你一个。"


class FakeChat:
    def online_user_ids(self) -> set[int]:
        return set()


class StickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "chat.sqlite3", self.root / "uploads")
        self.db.initialize()
        self.admin = self.db.register_user("admin01", "password-admin", role="admin")
        self.user = self.db.register_user("member1", "password-user")
        self.group = self.db.create_group("M4 群聊", self.admin["id"])
        self.db.join_group(self.group["id"], self.user["id"])
        self.config = replace(
            Config.from_env(), data_dir=self.root, database_path=self.root / "chat.sqlite3",
            uploads_dir=self.root / "uploads", sticker_auto_interval_seconds=90,
        )
        self.events: list[tuple[str, dict]] = []

        async def emit(_scope: str, _scope_id: str, event_type: str, payload: dict) -> None:
            self.events.append((event_type, payload))

        self.agent = AgentService(self.config, self.db, emit)
        self.agent.provider = FixedProvider()  # type: ignore[assignment]
        self.admin_service = AdminService(
            self.db, self.agent, FakeChat(), ProviderConfigStore(self.root), AdminSessionStore(600)
        )
        self.agent.provider = FixedProvider()  # type: ignore[assignment]

    def tearDown(self) -> None:
        self.admin_service.close()
        self.temp.cleanup()

    def _import(
        self,
        name: str = "开心",
        *,
        shared: bool = True,
        agent_ids: list[str] | None = None,
    ) -> dict:
        async def run() -> dict:
            _, session = await self.admin_service.login("admin01", "password-admin", "127.0.0.1")
            return await self.admin_service.save_sticker(session, {
                "name": name, "category": "开心", "tags": ["开心", "庆祝"],
                "mime": "image/png", "image_data": base64.b64encode(PNG).decode(),
                "shared": shared, "agent_ids": agent_ids or [],
            })
        return asyncio.run(run())

    def test_sticker_scope_separates_shared_and_role_card_libraries(self) -> None:
        shared = self._import("通用笑脸")
        taffy = self._import("塔菲专属", shared=False, agent_ids=["agent_taffy"])
        anon = self._import("爱音专属", shared=False, agent_ids=["agent_anon"])

        self.assertTrue(shared["shared"])
        self.assertEqual(shared["agent_ids"], [])
        self.assertFalse(taffy["shared"])
        self.assertEqual(taffy["agent_ids"], ["agent_taffy"])

        taffy_ids = {item["id"] for item in self.db.list_stickers_for_agent("agent_taffy")}
        anon_ids = {item["id"] for item in self.db.list_stickers_for_agent("agent_anon")}
        default_ids = {item["id"] for item in self.db.list_stickers_for_agent("agent")}
        self.assertEqual(taffy_ids, {shared["id"], taffy["id"]})
        self.assertEqual(anon_ids, {shared["id"], anon["id"]})
        self.assertEqual(default_ids, {shared["id"]})

        _, session = asyncio.run(self.admin_service.login("admin01", "password-admin", "127.0.0.1"))
        with self.assertRaises(AdminRequestError):
            asyncio.run(self.admin_service.save_sticker(
                session, {"shared": False, "agent_ids": []}, taffy["id"]
            ))
        with self.assertRaises(AdminRequestError):
            asyncio.run(self.admin_service.save_sticker(
                session,
                {"name": "坏绑定", "shared": False, "agent_ids": ["missing_agent"],
                 "mime": "image/png", "image_data": base64.b64encode(PNG).decode()},
            ))

        updated = asyncio.run(self.admin_service.save_sticker(
            session, {"shared": True, "agent_ids": []}, taffy["id"]
        ))
        self.assertTrue(updated["shared"])
        self.assertIn(
            taffy["id"],
            {item["id"] for item in self.db.list_stickers_for_agent("agent_anon")},
        )

    def test_legacy_sticker_table_migrates_to_shared(self) -> None:
        sticker = self._import("旧版表情包")
        with closing(sqlite3.connect(self.db.path)) as conn, conn:
            conn.execute("ALTER TABLE stickers DROP COLUMN shared")
        self.db.initialize()
        migrated = self.db.get_sticker(sticker["id"])
        self.assertTrue(migrated["shared"])
        self.assertIn(
            sticker["id"],
            {item["id"] for item in self.db.list_stickers_for_agent("agent_anon")},
        )

    def test_admin_preset_role_card_syncs_into_sticker_scope(self) -> None:
        async def scenario() -> None:
            _, session = await self.admin_service.login(
                "admin01", "password-admin", "127.0.0.1"
            )
            created = await self.admin_service.save_role_card(
                session,
                {
                    "name": "新预设角色",
                    "description": "由后台创建的全局角色卡",
                    "card": {
                        "personality": "冷静、直接",
                        "background": "后台预设角色",
                        "language_style": "简洁中文",
                        "behavior_rules": "遵守权限边界",
                    },
                    "enabled": True,
                    "memory_enabled": False,
                    "auto_sticker": True,
                },
            )
            self.assertTrue(created["preset"])
            self.assertTrue(created["deletable"])
            self.assertFalse(created["memory_enabled"])
            self.assertIn(
                created["id"],
                {item["id"] for item in await self.admin_service.agent_cards(session)},
            )

            sticker = await self.admin_service.save_sticker(
                session,
                {
                    "name": "新角色专属",
                    "category": "自定义",
                    "tags": ["新角色"],
                    "shared": False,
                    "agent_ids": [created["id"]],
                    "mime": "image/png",
                    "image_data": base64.b64encode(PNG).decode(),
                },
            )
            self.assertIn(
                sticker["id"],
                {
                    item["id"]
                    for item in self.db.list_stickers_for_agent(created["id"])
                },
            )

            updated = await self.admin_service.save_role_card(
                session, {"name": "新预设角色改"}, created["id"]
            )
            self.assertEqual(updated["name"], "新预设角色改")

            with self.assertRaises(AdminRequestError):
                await self.admin_service.delete_role_card(session, "agent_taffy")

            await self.admin_service.delete_role_card(session, created["id"])
            self.assertNotIn(
                created["id"],
                {item["id"] for item in await self.admin_service.agent_cards(session)},
            )
            self.assertEqual(
                [item["id"] for item in self.db.list_stickers_for_agent(created["id"])],
                [],
            )

        asyncio.run(scenario())

    def test_admin_import_metadata_validation_and_history_survives_delete(self) -> None:
        sticker = self._import()
        self.assertEqual(sticker["tags"], ["开心", "庆祝"])
        self.assertTrue((self.root / "uploads" / sticker["path"]).exists())
        with self.assertRaises(ValueError):
            validate_sticker_image(b"not an image", "image/png")
        with self.assertRaises(AdminRequestError):
            asyncio.run(self.admin_service.save_sticker(
                self.admin_service.sessions.create(self.admin, "127.0.0.1")[1],
                {"name": "坏图", "mime": "image/gif", "image_data": "!!!"},
            ))
        asyncio.run(self.agent.invoke(self.admin["id"], "group", str(self.group["id"]), "发个表情包"))
        media = [payload["message"] for event, payload in self.events if event == "message.event"]
        self.assertEqual([item["message_type"] for item in media], ["agent", "sticker"])
        self.assertEqual(media[1]["sticker"]["name"], "开心")
        self.assertEqual(base64.b64decode(media[1]["image_data"]), PNG)
        sent = self.db.get_message(media[1]["id"])
        self.assertEqual(sent["message_type"], "sticker")
        copy_path = self.root / "uploads" / sent["image_path"]
        self.assertTrue(copy_path.exists())
        asyncio.run(self.admin_service.delete_sticker(
            self.admin_service.sessions.create(self.admin, "127.0.0.1")[1], sticker["id"]
        ))
        self.assertFalse((self.root / "uploads" / sticker["path"]).exists())
        self.assertTrue(copy_path.exists())
        self.assertEqual(self.db.history(self.user["id"], "group", str(self.group["id"]))[-1]["id"], sent["id"])

    def test_sticker_directive_never_leaks_into_visible_reply(self) -> None:
        self.assertIn("已加载完毕", AgentService.TASK_SYSTEM_PROMPTS["question"])
        self.assertIn("系统或调试提示", AgentService.TASK_SYSTEM_PROMPTS["question"])
        reply = (
            "哎呀，被你猜中啦！无聊得想睡觉喵！[sticker: 魂龙]\n"
            "不过嘛……偶尔也能遇到开心的人，瞬间就满血复活啦！"
        )
        visible, requested = extract_sticker_directive(reply)
        self.assertEqual(requested, "魂龙")
        expected_visible = (
            "哎呀，被你猜中啦！无聊得想睡觉喵！\n"
            "不过嘛……偶尔也能遇到开心的人，瞬间就满血复活啦！"
        )
        self.assertEqual(
            visible,
            expected_visible,
        )
        self.assertNotIn("sticker", visible.casefold())

        visible, requested = extract_sticker_directive(
            "先说一句 [表情包：开心] 再继续聊 [sticker: 备用]"
        )
        self.assertEqual(visible, "先说一句 再继续聊")
        self.assertEqual(requested, "开心")

        historical = self.db.save_message(
            "group",
            str(self.group["id"]),
            None,
            "Agent",
            "agent",
            reply,
            sender_kind="agent",
            sender_agent_id="agent",
            content_format="markdown",
        )
        self.assertEqual(historical["content"], expected_visible)
        self.assertNotIn("[sticker:", self.db.get_message(historical["id"])["content"])

    def test_rate_limit_auto_switch_and_failure_keep_text(self) -> None:
        sticker = self._import()
        scope = str(self.group["id"])
        asyncio.run(self.agent.invoke(self.admin["id"], "group", scope, "发个表情包"))
        self.events.clear()
        asyncio.run(self.agent.invoke(self.admin["id"], "group", scope, "再发个表情包"))
        self.assertEqual([payload["message"]["message_type"] for event, payload in self.events if event == "message.event"], ["agent"])
        self.assertTrue(any(event == "agent.sticker.result" for event, _ in self.events))
        self.db.mark_sticker_sent("agent", "group", scope, sticker["id"], time.time() - 200)
        self.db.set_group_auto_sticker(self.group["id"], "agent", self.admin["id"], False)
        self.events.clear()
        asyncio.run(self.agent.invoke(self.admin["id"], "group", scope, "真开心"))
        self.assertEqual([payload["message"]["message_type"] for event, payload in self.events if event == "message.event"], ["agent"])
        self.db.set_group_auto_sticker(self.group["id"], "agent", self.admin["id"], True)
        self.db.update_agent("agent", self.admin["id"], {"auto_sticker": False})
        self.events.clear()
        asyncio.run(self.agent.invoke(self.admin["id"], "group", scope, "真开心"))
        self.assertEqual([payload["message"]["message_type"] for event, payload in self.events if event == "message.event"], ["agent"])
        (self.root / "uploads" / sticker["path"]).unlink()
        self.events.clear()
        asyncio.run(self.agent.invoke(self.admin["id"], "group", scope, "发个表情包"))
        self.assertEqual([payload["message"]["message_type"] for event, payload in self.events if event == "message.event"], ["agent"])
        self.assertEqual([payload["status"] for event, payload in self.events if event == "agent.result"][-1], "succeeded")
        self.assertTrue(any(event == "agent.sticker.result" and payload["status"] == "failed" for event, payload in self.events))

    def test_admin_route_requires_session_and_csrf(self) -> None:
        http = AdminHttpServer(self.config, self.admin_service)
        sticker = self._import()
        token, session = self.admin_service.sessions.create(self.admin, "127.0.0.1")
        async def scenario() -> None:
            with self.assertRaises(AdminAuthError):
                await http._route(Request("GET", "/api/v1/admin/stickers", {}, {}, b"", "127.0.0.1"))
            with self.assertRaises(AdminAuthError):
                await http._route(Request("DELETE", f"/api/v1/admin/stickers/{sticker['id']}", {}, {"cookie": f"chatroom_admin_session={token}"}, b"", "127.0.0.1"))
            response = await http._route(Request("GET", f"/api/v1/admin/stickers/{sticker['id']}/media", {}, {"cookie": f"chatroom_admin_session={token}"}, b"", "127.0.0.1"))
            self.assertEqual(response.body, PNG)
            self.assertEqual(response.content_type, "image/png")
            agents = await http._route(Request("GET", "/api/v1/admin/agents", {}, {"cookie": f"chatroom_admin_session={token}"}, b"", "127.0.0.1"))
            self.assertTrue(any(item["id"] == "agent_taffy" for item in json.loads(agents.body)["agents"]))
            role_response = await http._route(Request(
                "POST", "/api/v1/admin/role-cards", {},
                {"cookie": f"chatroom_admin_session={token}", "x-csrf-token": session.csrf_token},
                json.dumps({"name": "HTTP 角色", "description": "测试"}).encode(),
                "127.0.0.1",
            ))
            created_role = json.loads(role_response.body)
            self.assertTrue(created_role["preset"])
            roles = await http._route(Request("GET", "/api/v1/admin/role-cards", {}, {"cookie": f"chatroom_admin_session={token}"}, b"", "127.0.0.1"))
            self.assertIn(created_role["id"], {item["id"] for item in json.loads(roles.body)["role_cards"]})
            deleted_role = await http._route(Request(
                "DELETE", f"/api/v1/admin/role-cards/{created_role['id']}", {},
                {"cookie": f"chatroom_admin_session={token}", "x-csrf-token": session.csrf_token},
                b"", "127.0.0.1",
            ))
            self.assertTrue(json.loads(deleted_role.body)["ok"])
            deleted = await http._route(Request("DELETE", f"/api/v1/admin/stickers/{sticker['id']}", {}, {"cookie": f"chatroom_admin_session={token}", "x-csrf-token": session.csrf_token}, b"", "127.0.0.1"))
            self.assertTrue(json.loads(deleted.body)["ok"])
        asyncio.run(scenario())

    def test_group_broadcast_delivers_structured_sticker_to_all_members(self) -> None:
        self._import()
        server = ChatServer(self.config)
        server.agent.provider = FixedProvider()  # type: ignore[assignment]
        class Receiver:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict]] = []
            async def send(self, event_type: str, payload: dict) -> None:
                self.events.append((event_type, payload))
        owner, member = Receiver(), Receiver()
        server.sessions_by_user = {self.admin["id"]: {owner}, self.user["id"]: {member}}  # type: ignore[assignment]
        asyncio.run(server.agent.invoke(self.admin["id"], "group", str(self.group["id"]), "发个表情包"))
        for receiver in (owner, member):
            media = [payload["message"] for event, payload in receiver.events if event == "message.event"]
            self.assertEqual([item["message_type"] for item in media], ["agent", "sticker"])
            self.assertEqual(media[1]["sticker"]["mime"], "image/png")
            self.assertEqual(base64.b64decode(media[1]["image_data"]), PNG)
            self.assertEqual(media[1]["id"], [payload["message"]["id"] for event, payload in owner.events if event == "message.event"][-1])

    def test_sticker_tool_denial_preserves_answer_and_reports_reason(self) -> None:
        self._import()
        self.db.set_agent_tools("agent", self.admin["id"], [name for name in DEFAULT_AGENT_TOOL_NAMES if name != "send_agent_sticker"])
        asyncio.run(self.agent.invoke(self.admin["id"], "group", str(self.group["id"]), "发个表情包"))
        self.assertEqual([payload["message"]["message_type"] for event, payload in self.events if event == "message.event"], ["agent"])
        self.assertEqual([payload["status"] for event, payload in self.events if event == "agent.result"][-1], "succeeded")
        self.assertTrue(any(event == "agent.sticker.result" and "未获授权" in payload["reason"] for event, payload in self.events))
        denied = [item for item in self.db.recent_tool_calls() if item["tool_name"] == "send_agent_sticker"]
        self.assertEqual(denied[0]["status"], "denied")

    def test_selection_and_legacy_message_constraint_migrate_without_fk_loss(self) -> None:
        sticker = self._import()
        self.assertEqual(select_sticker([sticker], context="大家真开心", agent={"name": "朋友"})["id"], sticker["id"])
        before = self.db.save_message("group", str(self.group["id"]), self.user["id"], "member1", "text", "原消息")
        reply = self.db.save_message("group", str(self.group["id"]), self.admin["id"], "admin01", "text", "回复", reply_to_id=before["id"])
        with closing(sqlite3.connect(self.db.path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='messages'").fetchone()[0]
            old_sql = sql.replace("CREATE TABLE messages", "CREATE TABLE messages_legacy").replace("'agent','sticker','system'", "'agent','system'")
            conn.execute(old_sql)
            conn.execute("INSERT INTO messages_legacy SELECT * FROM messages")
            conn.execute("DROP TABLE messages")
            conn.execute("ALTER TABLE messages_legacy RENAME TO messages")
        self.db.initialize()
        self.assertEqual(self.db.get_message(reply["id"])["reply_to_id"], before["id"])
        with closing(sqlite3.connect(self.db.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        sent = self.db.save_message("group", str(self.group["id"]), None, "Agent", "sticker", "", sender_kind="agent", sender_agent_id="agent", sticker_id=sticker["id"])
        self.assertEqual(sent["message_type"], "sticker")


class StickerClientTests(unittest.TestCase):
    def test_local_sticker_library_imports_favorites_and_recent_without_touching_source(self) -> None:
        from client.v2.stickers import LocalStickerLibrary

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            valid_png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
            source.write_bytes(valid_png)
            library = LocalStickerLibrary(root / "library")
            sticker = library.import_file(source)

            self.assertEqual(sticker["mime"], "image/png")
            self.assertTrue(Path(sticker["path"]).is_file())
            self.assertEqual(source.read_bytes(), valid_png)
            self.assertEqual([item["id"] for item in library.list("mine")], [sticker["id"]])
            library.set_favorite(sticker["id"], True)
            self.assertEqual([item["id"] for item in library.list("favorites")], [sticker["id"]])
            library.mark_used(sticker["id"])
            self.assertEqual([item["id"] for item in library.list("recent")], [sticker["id"]])

            invalid = root / "invalid.gif"
            invalid.write_bytes(b"not-a-gif")
            with self.assertRaises(ValueError):
                library.import_file(invalid)

    def test_chat_service_sends_sticker_as_structured_message(self) -> None:
        from unittest.mock import Mock

        from client.network import NetworkClient
        from client.service import ChatService

        store = AppStore()
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        service.request = Mock(return_value="sticker-request")
        key = ConversationKey("group", "7")

        result = service.send_sticker(
            key, "local-1", "开心", "happy.png", "image/png", "encoded", 123
        )

        self.assertEqual(result, "sticker-request")
        service.request.assert_called_once_with(
            "message.send",
            {
                **service.scope(key),
                "message_type": "sticker",
                "sticker_id": "local-1",
                "sticker_name": "开心",
                "image_name": "happy.png",
                "image_mime": "image/png",
                "image_data": "encoded",
                "size": 123,
                "reply_to_id": None,
            },
        )

    def test_agent_status_tool_failure_and_sticker_feedback_are_scoped(self) -> None:
        store = AppStore()
        scope = {"conversation_type": "group", "conversation_id": "7"}
        key = ConversationKey("group", "7")
        store.handle_event({"type": "agent.result", "payload": {**scope, "run_id": 5, "agent_name": "助手", "status": "running"}})
        self.assertEqual(store.state.agent_runs[5]["key"], key)
        store.handle_event({"type": "tool.call.event", "payload": {**scope, "tool_call": {"id": 4, "run_id": 5, "tool_name": "send_agent_sticker", "status": "running"}}})
        self.assertEqual(store.state.agent_runs[5]["tool"], "send_agent_sticker")
        store.handle_event({"type": "tool.call.event", "payload": {**scope, "tool_call": {"id": 4, "run_id": 5, "tool_name": "send_agent_sticker", "status": "denied", "error": "无权限"}}})
        self.assertIn("无权限", store.state.agent_notices[key])
        store.handle_event({"type": "agent.result", "payload": {**scope, "run_id": 5, "status": "succeeded"}})
        self.assertNotIn(5, store.state.agent_runs)
        store.handle_event({"type": "agent.sticker.result", "payload": {**scope, "status": "failed", "reason": "文件丢失"}})
        self.assertEqual(store.state.agent_notices[key], "文件丢失")

    def test_gif_bubble_animates_and_plain_symbols_remain_plain(self) -> None:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication, QLabel
            from PySide6.QtCore import Qt
            from client.v2.widgets import MessageBubble
        except ImportError:
            self.skipTest("PySide6 not installed")
        app = QApplication.instance() or QApplication([])
        gif = base64.b64decode("R0lGODlhAQABAAD/ACwAAAAAAQABAAACAUwAOw==")
        bubble = MessageBubble({"id": 1, "sender_name": "Agent", "sender_kind": "agent", "message_type": "sticker", "sticker_name": "动态表情", "image_mime": "image/gif", "image_data": base64.b64encode(gif).decode()}, mine=False)
        self.assertTrue(bubble._movie.isValid())
        self.assertEqual(bubble.findChild(QLabel, "MessageSticker").movie(), bubble._movie)
        plain = MessageBubble({"id": 2, "sender_name": "用户", "message_type": "text", "content_format": "plain_text", "content": "*自然符号* #话题"}, mine=False)
        self.assertEqual(plain.content_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(plain.content_label.text(), "*自然符号* #话题")
        bubble.close()
        plain.close()
        app.processEvents()

    def test_enter_preference_sends_without_hijacking_shift_enter(self) -> None:
        import os
        from unittest.mock import patch
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            from PySide6.QtTest import QTest
            from client.v2.widgets import MentionTextEdit
        except ImportError:
            self.skipTest("PySide6 not installed")
        app = QApplication.instance() or QApplication([])
        editor = MentionTextEdit()
        sent: list[bool] = []
        editor.send_requested.connect(lambda: sent.append(True))
        with patch("client.v2.widgets.text_edit.QSettings") as settings:
            settings.return_value.value.return_value = True
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.assertEqual(sent, [True])
            self.assertEqual(editor.toPlainText(), "")
            QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(sent, [True])
            self.assertEqual(editor.toPlainText(), "\n")
        editor.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
