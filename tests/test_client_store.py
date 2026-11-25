from __future__ import annotations

import unittest
from unittest.mock import Mock

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

    def test_group_nickname_updates_only_its_group(self) -> None:
        from client.profile import resolve_profile

        for group_id in ("7", "8"):
            self.store.handle_event({"type": "group.members", "payload": {
                "group_id": group_id, "members": [{"id": 2, "username": "bob01", "display_name": "小博", "nickname": "旧名"}],
            }})
        self.store.handle_event({"type": "group.nickname.updated", "payload": {
            "group_id": 7, "user_id": 2, "nickname": "研发小博",
        }})
        first = self.store.state.members_by_group["7"][0]
        second = self.store.state.members_by_group["8"][0]
        self.assertEqual(resolve_profile(self.store.state, 2, first).display_name, "研发小博")
        self.assertEqual(resolve_profile(self.store.state, 2, second).display_name, "旧名")
        self.assertEqual(resolve_profile(self.store.state, 2, {"id": 2}).display_name, "小博")

    def test_old_server_name_request_has_actionable_error(self) -> None:
        self.store.handle_event({"type": "error", "payload": {
            "message": "未知消息类型：profile.identity.set",
        }})
        self.assertEqual(self.store.state.last_error, "服务端版本过旧，请重启服务端后重试")

    def test_list_responses_clear_pending_requests(self) -> None:
        cases = (
            ("presence-1", "presence.list", {"users": []}),
            ("groups-1", "group.list", {"groups": []}),
            ("activity-1", "agent.activity.list", {"activities": []}),
            ("status-1", "agent.status", {"online": False}),
        )
        for request_id, event_type, payload in cases:
            self.store.set_pending(request_id, event_type)
            self.store.handle_event(
                {"type": event_type, "request_id": request_id, "payload": payload}
            )
            self.assertNotIn(request_id, self.store.state.pending)

    def test_agent_and_group_tool_permissions_stay_separate(self) -> None:
        self.store.handle_event({"type": "agent.list", "payload": {"agents": [
            {"id": "agent", "owner_id": 1, "can_manage": False, "name": "我的 Agent"},
        ]}})
        self.assertTrue(self.store.state.agents[0]["can_manage"])
        self.store.handle_event({"type": "agent.changed", "payload": {"agent": {
            "id": "agent", "owner_id": 1, "can_manage": False, "name": "已更新 Agent",
        }}})
        self.assertTrue(self.store.state.agents[0]["can_manage"])
        self.store.handle_event({"type": "agent.tools.list", "payload": {
            "agent_id": "agent", "tools": [{"name": "web_search", "enabled": True}],
        }})
        self.store.handle_event({"type": "agent.group.tools.list", "payload": {
            "group_id": 7, "agent_id": "agent",
            "tools": [{"name": "web_search", "enabled": True, "group_enabled": False}],
        }})
        self.assertTrue(self.store.state.agent_tools["agent"][0]["enabled"])
        self.assertFalse(self.store.state.group_agent_tools[("7", "agent")][0]["group_enabled"])
        self.store.handle_event({"type": "agent.tools.updated", "payload": {
            "agent_id": "agent", "tools": [{"name": "web_search", "enabled": False}],
        }})
        self.assertNotIn(("7", "agent"), self.store.state.group_agent_tools)

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

    def test_private_open_state_receipt_and_typing(self) -> None:
        key = ConversationKey("private", "dm:1:2")
        self.store.handle_event({"type": "private.opened", "payload": {"conversation": {
            "conversation_type": "private", "conversation_id": key.identifier,
            "title": "bob", "can_send": True,
        }}})
        self.assertEqual(self.store.state.conversations[0].key, key)
        self.store.handle_event({"type": "conversation.list", "payload": {"conversations": []}})
        self.assertEqual(self.store.state.conversations[0].key, key)
        self.store.handle_event({"type": "private.state", "payload": {
            "conversation_id": key.identifier, "can_send": False, "blocked_by_me": True,
        }})
        self.assertFalse(self.store.state.conversations[0].can_send)
        self.assertTrue(self.store.state.conversations[0].blocked_by_me)
        self.store.handle_event({"type": "private.typing", "payload": {
            "conversation_id": key.identifier, "user_id": 2, "active": True,
        }})
        self.assertTrue(self.store.state.private_typing[key])
        self.store.handle_event({"type": "private.read", "payload": {
            "conversation_id": key.identifier, "reader_id": 2, "message_id": 18,
        }})
        self.assertEqual(self.store.state.conversations[0].peer_last_read_message_id, 18)
        self.store.handle_event({"type": "conversation.list", "payload": {"conversations": [{
            "conversation_type": "private", "conversation_id": key.identifier,
            "title": "bob", "peer_last_read_message_id": 18,
        }]}})
        self.assertFalse(self.store.state.conversations[0].can_send)

    def test_private_outbox_reconciliation_requires_manual_retry(self) -> None:
        key = ConversationKey("private", "dm:1:2")
        self.store.state.private_outbox["send-1"] = {"key": key, "payload": {"content": "hello"}, "status": "sending"}
        self.store.handle_event({"type": "network.disconnected", "payload": {}})
        self.assertEqual(self.store.state.private_outbox["send-1"]["status"], "uncertain")
        self.store.handle_event({"type": "message.status", "payload": {
            "client_request_id": "send-1", "found": False,
        }})
        self.assertEqual(self.store.state.private_outbox["send-1"]["status"], "retry_ready")
        self.store.handle_event({"type": "ack", "request_id": "send-1", "payload": {"action": "message.send"}})
        self.assertNotIn("send-1", self.store.state.private_outbox)

    def test_private_retry_reuses_original_request_id(self) -> None:
        from client.service import ChatService

        network = Mock()
        network.send.side_effect = lambda _event, _payload, request_id=None: request_id or "send-1"
        service = ChatService(network, self.store)
        self.store.state.capabilities["private_chat_v2"] = True
        key = ConversationKey("private", "dm:1:2")
        self.assertEqual(service.send_text(key, "hello"), "send-1")
        record = self.store.state.private_outbox["send-1"]
        record["status"] = "retry_ready"
        self.assertEqual(service.retry_private_send("send-1"), "send-1")
        self.assertEqual(record["status"], "sending")
        self.assertEqual(network.send.call_args.kwargs["request_id"], "send-1")

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

    def test_replay_keeps_newest_message_version_and_stable_order(self) -> None:
        key = ConversationKey("group", "7")
        base = {"conversation_type": "group", "conversation_id": "7", "sender_id": 2}
        self.store.handle_event({"type": "message.edited", "payload": {"message": {**base, "id": 10, "content": "edited", "event_id": 3, "edited": True}}})
        self.store.handle_event({"type": "history.result", "payload": {"conversation_type": "group", "conversation_id": "7", "messages": [{**base, "id": 10, "content": "old", "event_id": 1}, {**base, "id": 9, "content": "earlier"}]}})
        self.assertEqual([item["id"] for item in self.store.state.messages[key]], [9, 10])
        self.assertEqual(self.store.state.messages[key][1]["content"], "edited")
        self.store.handle_event({"type": "history.events", "payload": {"events": [{"event_type": "message.recalled", "message": {**base, "id": 10, "content": "", "recalled": True, "event_id": 4}}], "cursor": 4, "has_more": False}})
        self.assertTrue(self.store.state.messages[key][1]["recalled"])
        self.assertEqual(self.store.state.event_cursor, 4)

    def test_send_failure_keeps_draft_for_recovery(self) -> None:
        key = ConversationKey("group", "7")
        self.store.set_pending("send-1", "message.send")
        self.store.state.pending_sends["send-1"] = (key, "unsent text", [], None)
        self.store.handle_event({"type": "network.send_failed", "payload": {"request_id": "send-1"}})
        self.assertEqual(self.store.state.failed_send, (key, "unsent text", [], None))
        self.assertNotIn("send-1", self.store.state.pending)

    def test_media_result_enriches_versioned_image_without_reviving_recall(self) -> None:
        key = ConversationKey("group", "7")
        base = {"id": 8, "conversation_type": "group", "conversation_id": "7", "message_type": "image"}
        self.store.handle_event({"type": "message.event", "payload": {"message": {**base, "event_id": 10}}})
        self.store.handle_event({"type": "media.result", "payload": {"message": {**base, "image_data": "encoded"}}})
        self.assertEqual(self.store.state.messages[key][0]["image_data"], "encoded")
        self.store.handle_event({"type": "message.recalled", "payload": {"message": {**base, "event_id": 11, "recalled": True}}})
        self.store.handle_event({"type": "media.result", "payload": {"message": {**base, "image_data": "late"}}})
        self.assertNotEqual(self.store.state.messages[key][0].get("image_data"), "late")

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
