from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.database import Database, DatabaseError, dm_conversation_id


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = Database(root / "chat.sqlite3", root / "uploads")
        self.db.initialize()
        self.alice = self.db.register_user("alice", "password-a")
        self.bob = self.db.register_user("bob01", "password-b")
        self.carol = self.db.register_user("carol", "password-c")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_registration_authentication_and_default_lobby(self) -> None:
        self.assertIsNotNone(self.db.authenticate("alice", "password-a"))
        self.assertIsNone(self.db.authenticate("alice", "wrong-pass"))
        lobby = next(group for group in self.db.list_groups(self.alice["id"]) if group["name"] == "大厅")
        self.assertEqual(lobby["joined"], 1)
        with self.assertRaises(DatabaseError):
            self.db.register_user("alice", "another-password")
        if os.name == "posix":
            self.assertEqual(self.db.path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.db.path.stat().st_mode & 0o777, 0o600)

    def test_bootstrap_admin_never_promotes_same_name_with_wrong_password(self) -> None:
        self.db.register_user("admin01", "attacker-password")
        with self.assertRaises(DatabaseError):
            self.db.initialize("admin01", "configured-admin-password")
        existing = self.db.authenticate("admin01", "attacker-password")
        self.assertIsNotNone(existing)
        self.assertEqual(existing["role"], "user")

    def test_group_permissions_mute_and_recall(self) -> None:
        group = self.db.create_group("项目组", self.alice["id"])
        self.db.join_group(group["id"], self.bob["id"])
        self.db.set_mute(group["id"], self.bob["id"], self.alice["id"], True)
        self.assertTrue(self.db.is_muted(group["id"], self.bob["id"]))
        with self.assertRaises(DatabaseError):
            self.db.set_mute(group["id"], self.alice["id"], self.bob["id"], True)
        message = self.db.save_message("group", str(group["id"]), self.bob["id"], "bob01", "text", "hello")
        recalled = self.db.recall_message(message["id"], self.alice["id"])
        self.assertTrue(recalled["recalled"])
        self.assertEqual(recalled["content"], "")

    def test_schedule_readback_has_default_and_persists_server_value(self) -> None:
        group = self.db.create_group("总结设置组", self.alice["id"])
        default = self.db.get_schedule(group["id"])
        self.assertEqual(default["group_id"], group["id"])
        self.assertFalse(default["enabled"])
        self.assertEqual(default["interval_minutes"], 60)

        saved = self.db.set_schedule(group["id"], self.alice["id"], 90, True)
        current = self.db.get_schedule(group["id"])
        self.assertEqual(current["interval_minutes"], saved["interval_minutes"])
        self.assertEqual(current["enabled"], saved["enabled"])

    def test_private_history_search_and_access_control(self) -> None:
        scope = dm_conversation_id(self.alice["id"], self.bob["id"])
        first = self.db.save_message("private", scope, self.alice["id"], "alice", "text", "网络实验待办")
        self.db.save_message("private", scope, self.bob["id"], "bob01", "text", "收到")
        history = self.db.history(self.alice["id"], "private", scope)
        self.assertEqual(len(history), 2)
        self.assertEqual(self.db.search_messages(self.bob["id"], "private", scope, "待办")[0]["id"], first["id"])
        with self.assertRaises(DatabaseError):
            self.db.history(self.carol["id"], "private", scope)

    def test_content_format_defaults_to_plain_text_and_round_trips_markdown(self) -> None:
        scope = dm_conversation_id(self.alice["id"], self.bob["id"])
        plain = self.db.save_message(
            "private", scope, self.alice["id"], "alice", "text", "*原样* #标题 _下划线_"
        )
        markdown = self.db.save_message(
            "private",
            scope,
            None,
            "Agent",
            "agent",
            "# 总结\n\n- 完成联调",
            sender_kind="agent",
            content_format="markdown",
        )

        history = self.db.history(self.alice["id"], "private", scope)
        self.assertEqual(plain["content_format"], "plain_text")
        self.assertEqual(markdown["content_format"], "markdown")
        self.assertEqual(history[0]["content"], "*原样* #标题 _下划线_")
        self.assertEqual(history[1]["content"], "# 总结\n\n- 完成联调")

    def test_avatar_metadata_and_structured_mentions_round_trip(self) -> None:
        updated = self.db.set_avatar(self.alice["id"], "/tmp/alice.png", "image/png")
        self.assertEqual(updated["avatar_version"], 1)
        self.assertEqual(self.db.get_user(self.alice["id"])["avatar_mime"], "image/png")
        self.assertEqual(self.db.avatar_file(self.alice["id"])["path"], "/tmp/alice.png")

        group = self.db.create_group("提及测试群", self.alice["id"])
        self.db.join_group(group["id"], self.bob["id"])
        mentions = [
            {"member_id": f"user:{self.bob['id']}", "kind": "user", "display": "bob01", "start": 0, "length": 6}
        ]
        message = self.db.save_message(
            "group", str(group["id"]), self.alice["id"], "alice", "text", "@bob01 收到", mentions=mentions
        )
        self.assertEqual(message["mentions"], mentions)
        self.assertEqual(self.db.get_message(message["id"])["mentions"], mentions)

    def test_avatar_paths_are_portable_and_legacy_absolute_paths_still_work(self) -> None:
        avatar = self.db.uploads_dir / "avatars" / "alice.png"
        avatar.parent.mkdir(parents=True, exist_ok=True)
        avatar.write_bytes(b"avatar")
        self.db.set_avatar(self.alice["id"], str(avatar), "image/png")
        stored = self.db.avatar_file(self.alice["id"])
        self.assertEqual(stored["path"], "avatars/alice.png")
        self.assertEqual(stored["resolved_path"], avatar)

        legacy = self.db.uploads_dir / "avatars" / "legacy.png"
        legacy.write_bytes(b"legacy")
        with self.db._connect() as connection:
            connection.execute(
                "UPDATE users SET avatar_path=? WHERE id=?", (str(legacy), self.bob["id"])
            )
        self.assertEqual(self.db.avatar_file(self.bob["id"])["resolved_path"], legacy)

    def test_initialize_migrates_historical_messages_as_plain_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "legacy.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_type TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        sender_id INTEGER,
                        sender_name TEXT NOT NULL,
                        sender_kind TEXT NOT NULL DEFAULT 'user',
                        message_type TEXT NOT NULL,
                        content TEXT NOT NULL DEFAULT '',
                        image_path TEXT,
                        image_name TEXT,
                        image_mime TEXT,
                        created_at REAL NOT NULL,
                        recalled_at REAL,
                        recalled_by INTEGER
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO messages(
                        conversation_type, conversation_id, sender_name, message_type,
                        content, created_at
                    ) VALUES('group', '1', 'legacy', 'text', '*历史符号*', 1)
                    """
                )

            legacy = Database(path, root / "uploads")
            legacy.initialize()
            message = legacy.get_message(1)
            self.assertIsNotNone(message)
            self.assertEqual(message["content_format"], "plain_text")
            self.assertEqual(message["content"], "*历史符号*")
            self.assertEqual(message["mentions"], [])

    def test_offline_sync_uses_read_cursor(self) -> None:
        scope = dm_conversation_id(self.alice["id"], self.bob["id"])
        first = self.db.save_message("private", scope, self.alice["id"], "alice", "text", "one")
        second = self.db.save_message("private", scope, self.alice["id"], "alice", "text", "two")
        self.db.set_read_cursor(self.bob["id"], "private", scope, first["id"])
        synced = self.db.sync_for_user(self.bob["id"])
        self.assertIn(second["id"], [message["id"] for message in synced])
        self.assertNotIn(first["id"], [message["id"] for message in synced])

    def test_conversation_projection_and_history_pagination(self) -> None:
        scope = dm_conversation_id(self.alice["id"], self.bob["id"])
        messages = [
            self.db.save_message("private", scope, self.alice["id"], "alice", "text", f"message-{index}")
            for index in range(6)
        ]
        self.db.set_read_cursor(self.bob["id"], "private", scope, messages[2]["id"])
        conversations = self.db.list_conversations(self.bob["id"])
        private = next(item for item in conversations if item["conversation_type"] == "private")
        self.assertEqual(private["title"], "alice")
        self.assertEqual(private["last_message_preview"], "message-5")
        self.assertEqual(private["unread_count"], 3)

        latest = self.db.history_page(self.bob["id"], "private", scope, limit=3)
        self.assertEqual([item["content"] for item in latest["messages"]], ["message-3", "message-4", "message-5"])
        self.assertTrue(latest["has_more"])
        older = self.db.history_page(
            self.bob["id"], "private", scope, before_id=latest["first_id"], limit=3
        )
        self.assertEqual([item["content"] for item in older["messages"]], ["message-0", "message-1", "message-2"])

    def test_agent_activity_and_system_counts_respect_access(self) -> None:
        group = self.db.create_group("活动群", self.alice["id"])
        run_id = self.db.create_agent_run("summary", "group", str(group["id"]), self.alice["id"])
        self.db.finish_agent_run(run_id, "succeeded")
        self.assertEqual(len(self.db.list_agent_activity(self.alice["id"])), 1)
        self.assertEqual(self.db.list_agent_activity(self.bob["id"]), [])
        self.assertEqual(self.db.system_counts()["users"], 3)



if __name__ == "__main__":
    unittest.main()

