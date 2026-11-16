"""Framework-independent application store for the V2 desktop client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import AppState, ConversationKey, ConversationSummary


StateListener = Callable[[str, AppState], None]


class AppStore:
    def __init__(self) -> None:
        self.state = AppState()
        self._listeners: list[StateListener] = []

    def subscribe(self, listener: StateListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def notify(self, topic: str) -> None:
        for listener in tuple(self._listeners):
            listener(topic, self.state)

    def set_pending(self, request_id: str | None, action: str) -> None:
        if request_id:
            self.state.pending[request_id] = action
            self.notify("pending")

    def select_conversation(self, key: ConversationKey) -> None:
        self.state.current_conversation = key
        self.notify("selection")

    def clear_session(self) -> None:
        connected = self.state.connected
        connection_text = self.state.connection_text
        self.state = AppState(connected=connected, connection_text=connection_text)
        self.notify("session")

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        request_id = str(event.get("request_id", ""))
        if event_type in {
            "ack",
            "error",
            "auth.result",
            "history.result",
            "profile.avatar.result",
            "group.members",
            "agent.schedule.updated",
        }:
            self.state.pending.pop(request_id, None)

        if event_type == "network.connected":
            self.state.connected = True
            self.state.connection_text = "已连接"
            self.notify("connection")
            return
        if event_type in {"network.disconnected", "network.reconnecting"}:
            self.state.connected = False
            if event_type == "network.disconnected":
                self.state.authenticated = False
            delay = payload.get("delay")
            self.state.connection_text = f"{delay} 秒后重连" if delay else "连接已断开"
            self.notify("connection")
            return
        if event_type == "network.error":
            self.state.last_error = str(payload.get("message", "连接失败"))
            self.notify("error")
            return
        if event_type == "auth.result":
            if not bool(payload.get("ok", payload.get("success", False))):
                self.state.last_error = str(payload.get("message", "认证失败"))
                self.notify("auth")
                return
            self.state.authenticated = True
            self.state.current_user = dict(payload.get("user") or {})
            self.state.capabilities = dict(payload.get("capabilities") or {})
            self.state.users = [dict(item) for item in payload.get("users", [])]
            self.state.groups = [dict(item) for item in payload.get("groups", [])]
            self.state.agent_online = bool(payload.get("agent_online", False))
            for message in payload.get("offline_messages", []):
                if isinstance(message, dict):
                    self._merge_message(message)
            self.notify("auth")
            return
        if event_type == "conversation.list":
            records = payload.get("conversations", [])
            self.state.conversations = [self._conversation(item) for item in records if isinstance(item, dict)]
            self.notify("conversations")
            return
        if event_type in {"message.event", "agent.result"}:
            message = payload.get("message")
            if isinstance(message, dict):
                self._merge_message(message)
            elif payload.get("content") or payload.get("result"):
                synthetic = dict(payload)
                synthetic["content"] = str(payload.get("content", payload.get("result", "")))
                synthetic["message_type"] = "agent"
                synthetic.setdefault("content_format", "plain_text")
                synthetic["sender_name"] = "Agent"
                self._merge_message(synthetic)
            if payload.get("status") == "failed" or payload.get("success") is False:
                self.state.last_error = str(payload.get("error", "Agent 调用失败"))
            self.notify("agent_result" if event_type == "agent.result" else "messages")
            return
        if event_type == "history.result":
            key = ConversationKey.from_payload(payload, self._current_user_id())
            if key:
                results = [dict(item) for item in payload.get("messages", []) if isinstance(item, dict)]
                if payload.get("search"):
                    self.state.search_results[key] = results
                    self.notify("search")
                    return
                for message in results:
                    self._merge_message(message, fallback=key)
            self.notify("history")
            return
        if event_type in {"message.recall", "message.recalled"}:
            recalled = payload.get("message") if isinstance(payload.get("message"), dict) else payload
            message_id = str(recalled.get("id", recalled.get("message_id", "")))
            for records in self.state.messages.values():
                for message in records:
                    if str(message.get("id", message.get("message_id", ""))) == message_id:
                        message.update(recalled)
                        message["recalled"] = True
                        message["content"] = ""
            self.notify("messages")
            return
        if event_type == "media.result":
            message = payload.get("message")
            if isinstance(message, dict):
                self._merge_message(message)
                self.notify("media")
            return
        if event_type == "presence.list":
            self.state.users = [dict(item) for item in payload.get("users", [])]
            self.notify("contacts")
            return
        if event_type == "presence.changed":
            user = payload.get("user")
            if isinstance(user, dict):
                updated = dict(user)
                updated["online"] = bool(payload.get("online", False))
                updated = self._upsert_user(self.state.users, updated)
            self.notify("contacts")
            return
        if event_type == "group.list":
            self.state.groups = [dict(item) for item in payload.get("groups", [])]
            self.notify("groups")
            return
        if event_type == "group.members":
            members = [dict(item) for item in payload.get("members", [])]
            group_id = str(payload.get("group_id", ""))
            self.state.members_by_group[group_id] = members
            self.state.group_permissions[group_id] = dict(payload.get("permissions") or {})
            schedule = payload.get("schedule")
            if isinstance(schedule, dict):
                self.state.schedules_by_group[group_id] = dict(schedule)
            if self.state.current_conversation == ConversationKey("group", group_id):
                self.state.members = members
            self.notify("details")
            return
        if event_type == "moderation.changed":
            group_id = str(payload.get("group_id", ""))
            user_id = str(payload.get("user_id", ""))
            muted = bool(payload.get("muted", False))
            for member in self.state.members_by_group.get(group_id, []):
                if str(member.get("id")) != user_id:
                    continue
                member["muted"] = muted
                if member.get("available_actions"):
                    member["available_actions"] = ["unmute" if muted else "mute"]
                break
            if self.state.current_conversation == ConversationKey("group", group_id):
                self.state.members = self.state.members_by_group.get(group_id, [])
            self.notify("details")
            return
        if event_type == "agent.schedule.updated":
            schedule = payload.get("schedule")
            if isinstance(schedule, dict):
                group_id = str(schedule.get("group_id", ""))
                self.state.schedules_by_group[group_id] = dict(schedule)
            self.notify("details")
            return
        if event_type == "profile.updated":
            user = payload.get("user")
            if isinstance(user, dict):
                updated = dict(user)
                user_id = int(updated.get("id", 0) or 0)
                cached_version = int(self.state.avatar_cache.get(user_id, {}).get("avatar_version", -1) or -1)
                updated = self._upsert_user(self.state.users, updated)
                if int(self.state.current_user.get("id", 0) or 0) == user_id:
                    if int(self.state.current_user.get("avatar_version", 0) or 0) > int(updated.get("avatar_version", 0) or 0):
                        updated["avatar_version"] = self.state.current_user.get("avatar_version", 0)
                        updated["avatar_mime"] = self.state.current_user.get("avatar_mime")
                    self.state.current_user.update(updated)
                for members in self.state.members_by_group.values():
                    for member in members:
                        if str(member.get("id")) == str(user_id):
                            member.update(updated)
                authoritative_version = int(updated.get("avatar_version", 0) or 0)
                if cached_version != authoritative_version:
                    self.state.avatar_cache.pop(user_id, None)
            self.notify("profile")
            return
        if event_type == "profile.avatar.result":
            user_id = int(payload.get("user_id", 0) or 0)
            if user_id:
                self.state.avatar_cache[user_id] = dict(payload)
            self.notify("avatar")
            return
        if event_type == "agent.status":
            self.state.agent_online = bool(payload.get("online", False))
            self.state.agent_model = str(payload.get("model", ""))
            self.notify("agent")
            return
        if event_type == "agent.activity.list":
            self.state.activities = [dict(item) for item in payload.get("activities", [])]
            self.notify("activities")
            return
        if event_type == "tool.calls.list":
            self.state.tool_calls = [dict(item) for item in payload.get("tool_calls", [])]
            self.notify("tools")
            return
        if event_type == "tool.call.event":
            record = payload.get("tool_call") if isinstance(payload.get("tool_call"), dict) else payload
            self._upsert(self.state.tool_calls, dict(record))
            self.notify("tools")
            return
        if event_type == "system.status":
            self.state.system_status = dict(payload)
            self.notify("system")
            return
        if event_type == "error":
            self.state.last_error = str(payload.get("message", payload.get("error", "请求失败")))
            self.notify("error")
            return
        if event_type == "ack":
            if payload.get("action") == "auth.logout":
                self.clear_session()
                return
            self.notify("ack")

    def _current_user_id(self) -> int | str | None:
        return self.state.current_user.get("id", self.state.current_user.get("user_id"))

    def _conversation(self, item: dict[str, Any]) -> ConversationSummary:
        return ConversationSummary(
            key=ConversationKey(str(item.get("conversation_type", "")), str(item.get("conversation_id", ""))),
            title=str(item.get("title", "未命名会话")),
            preview=str(item.get("last_message_preview", "")),
            timestamp=float(item.get("last_message_at", 0) or 0),
            unread_count=int(item.get("unread_count", 0) or 0),
            member_role=str(item.get("member_role", "")),
        )

    def _merge_message(self, message: dict[str, Any], fallback: ConversationKey | None = None) -> None:
        key = ConversationKey.from_payload(message, self._current_user_id()) or fallback
        if not key:
            return
        records = self.state.messages.setdefault(key, [])
        message_id = str(message.get("id", message.get("message_id", "")))
        for current in records:
            if message_id and str(current.get("id", current.get("message_id", ""))) == message_id:
                current.update(message)
                break
        else:
            records.append(dict(message))
        records.sort(key=lambda item: (float(item.get("created_at", item.get("timestamp", 0)) or 0), str(item.get("id", ""))))

    @staticmethod
    def _upsert(records: list[dict[str, Any]], item: dict[str, Any]) -> None:
        item_id = str(item.get("id", ""))
        for current in records:
            if item_id and str(current.get("id", "")) == item_id:
                current.update(item)
                return
        records.insert(0, item)

    @staticmethod
    def _upsert_user(records: list[dict[str, Any]], item: dict[str, Any]) -> dict[str, Any]:
        user_id = str(item.get("id", ""))
        for current in records:
            if user_id and str(current.get("id", "")) == user_id:
                if int(current.get("avatar_version", 0) or 0) > int(item.get("avatar_version", 0) or 0):
                    item["avatar_version"] = current.get("avatar_version", 0)
                    item["avatar_mime"] = current.get("avatar_mime")
                current.update(item)
                return current
        records.append(item)
        return item

