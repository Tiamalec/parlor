from __future__ import annotations

import unittest

from client.models import ConversationKey
from client.store import AppStore


class ClientStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = AppStore()
        self.store.handle_event(
            {
                "type": "auth.result",
                "request_id": "auth-1",
                "payload": {
                    "ok": True,
                    "user": {"id": 1, "username": "alice", "role": "user"},
                    "capabilities": {"conversation_list": True},
                },
            }
        )

    def test_conversation_projection_and_message_merge(self) -> None:
        self.store.handle_event(
            {
                "type": "conversation.list",
                "payload": {
                    "conversations": [
                        {
                            "conversation_type": "group",
                            "conversation_id": "7",
                            "title": "项目组",
                            "last_message_preview": "开会",
                            "unread_count": 2,
                        }
                    ]
                },
            }
        )
        self.assertEqual(self.store.state.conversations[0].unread_count, 2)
        message = {
            "id": 9,
            "conversation_type": "group",
            "conversation_id": "7",
            "sender_id": 2,
            "content": "第一版",
            "created_at": 1,
        }
        self.store.handle_event({"type": "message.event", "payload": {"message": message}})
        message["content"] = "更新版"
        self.store.handle_event({"type": "message.event", "payload": {"message": message}})
        records = self.store.state.messages[ConversationKey("group", "7")]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["content"], "更新版")

    def test_search_results_do_not_pollute_timeline(self) -> None:
        key = ConversationKey("group", "7")
        self.store.handle_event(
            {
                "type": "history.result",
                "payload": {
                    "conversation_type": "group",
                    "conversation_id": "7",
                    "search": True,
                    "messages": [{"id": 2, "content": "命中"}],
                },
            }
        )
        self.assertEqual(self.store.state.search_results[key][0]["content"], "命中")
        self.assertNotIn(key, self.store.state.messages)

    def test_recall_updates_cached_message(self) -> None:
        key = ConversationKey("group", "7")
        self.store.handle_event(
            {
                "type": "message.event",
                "payload": {
                    "message": {
                        "id": 3,
                        "conversation_type": "group",
                        "conversation_id": "7",
                        "content": "撤回前",
                    }
                },
            }
        )
        self.store.handle_event(
            {"type": "message.recalled", "payload": {"message": {"id": 3, "recalled": True}}}
        )
        self.assertTrue(self.store.state.messages[key][0]["recalled"])
        self.assertEqual(self.store.state.messages[key][0]["content"], "")

    def test_delayed_profile_event_cannot_roll_back_avatar_version(self) -> None:
        self.store.state.users = [
            {"id": 2, "username": "bob01", "avatar_version": 3, "avatar_mime": "image/webp"}
        ]
        self.store.handle_event(
            {
                "type": "profile.updated",
                "payload": {
                    "user": {"id": 2, "username": "bob01", "avatar_version": 2, "avatar_mime": "image/png"}
                },
            }
        )
        self.assertEqual(self.store.state.users[0]["avatar_version"], 3)
        self.assertEqual(self.store.state.users[0]["avatar_mime"], "image/webp")

    def test_profile_update_invalidates_only_an_obsolete_avatar_cache_entry(self) -> None:
        self.store.state.users = [
            {"id": 2, "username": "bob01", "avatar_version": 3, "avatar_mime": "image/webp"}
        ]
        self.store.state.avatar_cache[2] = {
            "user_id": 2,
            "avatar_version": 3,
            "image_data": "cached",
        }
        self.store.handle_event(
            {
                "type": "profile.updated",
                "payload": {
                    "user": {"id": 2, "username": "bob01", "avatar_version": 2, "avatar_mime": "image/png"}
                },
            }
        )
        self.assertEqual(self.store.state.avatar_cache[2]["image_data"], "cached")

        self.store.handle_event(
            {
                "type": "profile.updated",
                "payload": {
                    "user": {"id": 2, "username": "bob01", "avatar_version": 4, "avatar_mime": "image/png"}
                },
            }
        )
        self.assertNotIn(2, self.store.state.avatar_cache)

    def test_group_details_and_server_events_update_authoritative_state(self) -> None:
        self.store.handle_event(
            {
                "type": "group.members",
                "request_id": "members-1",
                "payload": {
                    "group_id": 7,
                    "members": [
                        {
                            "id": 2,
                            "username": "bob01",
                            "kind": "user",
                            "muted": False,
                            "available_actions": ["mute"],
                        }
                    ],
                    "permissions": {"can_manage_members": True, "can_manage_schedule": True},
                    "schedule": {"group_id": 7, "interval_minutes": 30, "enabled": True},
                },
            }
        )
        self.assertTrue(self.store.state.group_permissions["7"]["can_manage_members"])
        self.assertEqual(self.store.state.schedules_by_group["7"]["interval_minutes"], 30)

        self.store.handle_event(
            {
                "type": "moderation.changed",
                "payload": {"group_id": 7, "user_id": 2, "muted": True},
            }
        )
        member = self.store.state.members_by_group["7"][0]
        self.assertTrue(member["muted"])
        self.assertEqual(member["available_actions"], ["unmute"])

        self.store.set_pending("schedule-1", "agent.schedule.set")
        self.store.handle_event(
            {
                "type": "agent.schedule.updated",
                "request_id": "schedule-1",
                "payload": {
                    "schedule": {"group_id": 7, "interval_minutes": 120, "enabled": False}
                },
            }
        )
        self.assertNotIn("schedule-1", self.store.state.pending)
        self.assertEqual(self.store.state.schedules_by_group["7"]["interval_minutes"], 120)
        self.assertFalse(self.store.state.schedules_by_group["7"]["enabled"])


if __name__ == "__main__":
    unittest.main()
