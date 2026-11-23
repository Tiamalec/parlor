"""Protocol command facade used by V2 view models and widgets."""

from __future__ import annotations

import time
from typing import Any

from .models import ConversationKey
from .network import NetworkClient
from .profile import ProfileViewModel, resolve_profile
from .store import AppStore


HISTORY_CACHE_SECONDS = 60.0


class ChatService:
    def __init__(self, network: NetworkClient, store: AppStore) -> None:
        self.network = network
        self.store = store
        self._avatar_requests: dict[tuple[int, int], float] = {}
        self._agent_avatar_requests: dict[tuple[str, int], float] = {}
        self._history_requests: dict[ConversationKey, str] = {}
        self._member_requests: dict[str, str] = {}
        self._group_agent_requests: dict[str, str] = {}

    def request(self, event_type: str, payload: dict[str, Any] | None = None, *, request_id: str | None = None) -> str | None:
        try:
            request_id = self.network.send(event_type, payload or {}, request_id=request_id)
        except (ConnectionError, ValueError) as exc:
            self.store.state.last_error = str(exc)
            self.store.notify("error")
            return None
        self.store.set_pending(request_id, event_type)
        return request_id

    @staticmethod
    def scope(key: ConversationKey) -> dict[str, Any]:
        return {
            "conversation_type": key.kind,
            "conversation_id": key.identifier,
            "scope": {"type": key.kind, "id": key.identifier},
        }

    def login(self, username: str, password: str) -> str | None:
        return self.request("auth.login", {"username": username, "password": password,
                                           "after_event_id": self.store.state.event_cursor})

    def register(self, username: str, password: str) -> str | None:
        return self.request("auth.register", {"username": username, "password": password})

    def logout(self) -> str | None:
        return self.request("auth.logout")

    def bootstrap_workspace(self) -> None:
        for event_type, payload in (
            ("conversation.list", {}),
            ("presence.list", {}),
            ("group.list", {}),
            ("agent.status", {}),
            ("agent.list", {}),
            ("agent.activity.list", {"limit": 100}),
            ("tool.calls.list", {"limit": 100}),
            ("tool.list", {}),
        ):
            self.request(event_type, payload)
        if self.store.state.current_user.get("role") == "admin":
            self.request("system.status")

    def _pending(self, request_id: str) -> bool:
        return request_id in self.store.state.pending

    def _live_request(self, requests: dict[Any, str], key: Any) -> str | None:
        request_id = requests.get(key)
        if request_id and self._pending(request_id):
            return request_id
        if request_id:
            requests.pop(key, None)
        return None

    def history_loading(self, key: ConversationKey) -> bool:
        return self._live_request(self._history_requests, key) is not None

    def _history_is_fresh(self, key: ConversationKey) -> bool:
        loaded_at = self.store.state.history_loaded_at.get(key)
        if loaded_at is None:
            return False
        return time.monotonic() - loaded_at < HISTORY_CACHE_SECONDS

    def open_conversation(self, key: ConversationKey) -> bool:
        """Select immediately and request only metadata that is missing or stale.

        Returns True when a history request was queued, allowing the chat pane
        to show a lightweight loading row without waiting for the reply.
        """
        if self.store.state.current_conversation != key:
            self.store.select_conversation(key)
        if key.kind == "private" and self.store.state.capabilities.get("private_chat_v2"):
            self.request("private.open", {"target_user_id": self._private_peer_id(key)})
        history_requested = False
        if (
            self._live_request(self._history_requests, key) is None
            and not self._history_is_fresh(key)
        ):
            payload = self.scope(key)
            payload.update({"limit": 50, "media_mode": "metadata"})
            request_id = self.request("history.sync", payload)
            if request_id:
                self._history_requests[key] = request_id
                history_requested = True
        if key.kind == "group":
            group_id = key.identifier
            if (
                group_id not in self.store.state.members_by_group
                and self._live_request(self._member_requests, group_id) is None
            ):
                request_id = self.request("group.members", {"group_id": group_id})
                if request_id:
                    self._member_requests[group_id] = request_id
            if (
                group_id not in self.store.state.group_agents
                and self._live_request(self._group_agent_requests, group_id) is None
            ):
                request_id = self.request("agent.group.list", {"group_id": group_id})
                if request_id:
                    self._group_agent_requests[group_id] = request_id
        return history_requested

    def load_older(self, key: ConversationKey, before_id: int) -> None:
        payload = self.scope(key)
        payload.update({"before_id": before_id, "limit": 50, "media_mode": "metadata"})
        self.request("history.sync", payload)

    def send_text(
        self, key: ConversationKey, content: str, mentions: list[dict[str, Any]] | None = None,
        reply_to_id: int | None = None,
    ) -> str | None:
        payload = self.scope(key)
        payload.update(
            {
                "message_type": "text",
                "content": content,
                "mentions": mentions or [],
                "mention_index_unit": "unicode_codepoints",
                "reply_to_id": reply_to_id,
            }
        )
        request_id = self.request("message.send", payload)
        if request_id:
            self.store.state.pending_sends[request_id] = (key, content, mentions or [], reply_to_id)
            if key.kind == "private" and self.store.state.capabilities.get("private_chat_v2"):
                self.store.state.private_outbox[request_id] = {"key": key, "payload": payload, "status": "sending"}
                self.store.notify("private_outbox")
        return request_id

    def set_avatar(self, mime: str | None = None, encoded: str = "") -> str | None:
        return self.request("profile.avatar.set", {"image_mime": mime, "image_data": encoded})

    def set_identity(self, username: str, display_name: str) -> str | None:
        return self.request("profile.identity.set", {"username": username, "display_name": display_name})

    def set_group_nickname(self, group_id: str, nickname: str) -> str | None:
        return self.request("group.nickname.set", {"group_id": group_id, "nickname": nickname})

    def fetch_avatar(self, user_id: int, version: int) -> str | None:
        cached = self.store.state.avatar_cache.get(user_id)
        if cached and int(cached.get("avatar_version", -1)) == version and not cached.get("load_failed"):
            self._avatar_requests.pop((user_id, version), None)
            return None
        key = (user_id, version)
        now = time.monotonic()
        if now - self._avatar_requests.get(key, 0) < 5:
            return None
        request_id = self.request("profile.avatar.get", {"user_id": user_id, "avatar_version": version})
        if request_id:
            self._avatar_requests[key] = now
        return request_id

    def profile(
        self,
        user_id: int | None,
        fallback: dict[str, Any] | None = None,
        group_id: str | None = None,
    ) -> ProfileViewModel:
        return resolve_profile(self.store.state, user_id, fallback, group_id)

    def avatar_data(self, profile: ProfileViewModel) -> str | None:
        if profile.kind == "agent":
            if profile.image_data:
                return profile.image_data
            if profile.agent_id and profile.avatar_version > 0:
                self.fetch_agent_avatar(profile.agent_id, profile.avatar_version)
            return None
        if profile.user_id is None:
            return None
        if profile.image_data:
            return profile.image_data
        if profile.avatar_version > 0:
            self.fetch_avatar(profile.user_id, profile.avatar_version)
        return None

    def load_result(self, key: ConversationKey, message_id: int) -> str | None:
        payload = self.scope(key)
        payload.update({"before_id": message_id + 1, "limit": 20, "media_mode": "metadata"})
        return self.request("history.sync", payload)

    def send_image(self, key: ConversationKey, name: str, mime: str, encoded: str, size: int) -> str | None:
        payload = self.scope(key)
        payload.update(
            {
                "message_type": "image",
                "image_name": name,
                "image_mime": mime,
                "image_data": encoded,
                "size": size,
            }
        )
        return self._send_message_payload(key, payload)

    def send_sticker(
        self,
        key: ConversationKey,
        sticker_id: str,
        sticker_name: str,
        image_name: str,
        mime: str,
        encoded: str,
        size: int,
        reply_to_id: int | None = None,
    ) -> str | None:
        payload = self.scope(key)
        payload.update(
            {
                "message_type": "sticker",
                "sticker_id": sticker_id,
                "sticker_name": sticker_name,
                "image_name": image_name,
                "image_mime": mime,
                "image_data": encoded,
                "size": size,
                "reply_to_id": reply_to_id,
            }
        )
        return self._send_message_payload(key, payload)

    def _send_message_payload(self, key: ConversationKey, payload: dict[str, Any]) -> str | None:
        request_id = self.request("message.send", payload)
        if request_id and key.kind == "private" and self.store.state.capabilities.get("private_chat_v2"):
            self.store.state.private_outbox[request_id] = {"key": key, "payload": payload, "status": "sending"}
            self.store.notify("private_outbox")
        return request_id

    def set_private_block(self, key: ConversationKey, blocked: bool) -> str | None:
        return self.request("private.block.set", {"target_user_id": self._private_peer_id(key), "blocked": blocked})

    def _private_peer_id(self, key: ConversationKey) -> int:
        parts = key.identifier.split(":")
        if len(parts) == 3 and parts[0] == "dm":
            own_id = int(self.store.state.current_user["id"])
            return int(parts[2]) if int(parts[1]) == own_id else int(parts[1])
        return int(key.identifier)

    def set_private_typing(self, key: ConversationKey, active: bool) -> str | None:
        if not self.store.state.capabilities.get("private_chat_v2"):
            return None
        return self.request("private.typing", {"conversation_id": key.identifier, "active": active})

    def reconcile_private_sends(self) -> None:
        for request_id, record in tuple(self.store.state.private_outbox.items()):
            if record.get("status") == "uncertain":
                self.request("message.status", {"client_request_id": request_id})

    def retry_private_send(self, request_id: str) -> str | None:
        record = self.store.state.private_outbox.get(request_id)
        if not record or record.get("status") != "retry_ready":
            return None
        sent = self.request("message.send", record["payload"], request_id=request_id)
        if sent:
            record["status"] = "sending"
            self.store.notify("private_outbox")
        return sent

    def search(self, key: ConversationKey, keyword: str, before_id: int | None = None) -> str | None:
        payload = self.scope(key)
        payload.update({"keyword": keyword, "limit": 50, "before_id": before_id})
        return self.request("history.search", payload)

    def sync_events(self) -> str | None:
        return self.request("history.events", {"after_event_id": self.store.state.event_cursor})

    def edit(self, message_id: int, content: str, mentions: list[dict[str, Any]] | None = None) -> str | None:
        return self.request("message.edit", {"message_id": message_id, "content": content,
                                              "mentions": mentions or [], "mention_index_unit": "unicode_codepoints"})

    def invoke_agent(self, key: ConversationKey, prompt: str, agent_id: str | None = None) -> str | None:
        payload = self.scope(key)
        payload.update({"prompt": prompt, "task_type": "query", "limit": 200})
        if agent_id:
            payload["agent_id"] = agent_id
        return self.request("agent.invoke", payload)

    def create_agent(self, **values: Any) -> str | None:
        return self.request("agent.create", values)

    def update_agent(self, agent_id: str, **values: Any) -> str | None:
        return self.request("agent.update", {"agent_id": agent_id, **values})

    def delete_agent(self, agent_id: str) -> str | None:
        return self.request("agent.delete", {"agent_id": agent_id})

    def set_agent_avatar(self, agent_id: str, mime: str | None = None, encoded: str = "") -> str | None:
        return self.request("agent.avatar.set", {"agent_id": agent_id, "image_mime": mime, "image_data": encoded})

    def fetch_agent_avatar(self, agent_id: str, version: int) -> str | None:
        cached = self.store.state.agent_avatar_cache.get(agent_id)
        if cached and int(cached.get("avatar_version", -1)) == version and not cached.get("load_failed"):
            self._agent_avatar_requests.pop((agent_id, version), None)
            return None
        key = (agent_id, version)
        now = time.monotonic()
        if now - self._agent_avatar_requests.get(key, 0) < 5:
            return None
        request_id = self.request("agent.avatar.get", {"agent_id": agent_id, "avatar_version": version})
        if request_id:
            self._agent_avatar_requests[key] = now
        return request_id

    def set_agent_tools(self, agent_id: str, tools: list[str]) -> str | None:
        return self.request("agent.tool.set", {"agent_id": agent_id, "tools": tools})

    def set_group_agent(self, group_id: str, agent_id: str, enabled: bool) -> str | None:
        return self.request("agent.group.set", {"group_id": group_id, "agent_id": agent_id, "enabled": enabled})

    def set_group_auto_sticker(self, group_id: str, agent_id: str, enabled: bool) -> str | None:
        return self.request("agent.group.sticker.set", {"group_id": group_id, "agent_id": agent_id, "enabled": enabled})

    def set_group_agent_tools(self, group_id: str, agent_id: str, tools: list[str]) -> str | None:
        return self.request("agent.group.tools.set", {"group_id": group_id, "agent_id": agent_id, "tools": tools})

    def load_memories(self, key: ConversationKey, agent_id: str) -> str | None:
        payload = self.scope(key)
        payload["agent_id"] = agent_id
        return self.request("memory.list", payload)

    def add_memory(self, key: ConversationKey, agent_id: str, content: str, kind: str = "note") -> str | None:
        payload = self.scope(key)
        payload.update({"agent_id": agent_id, "content": content, "kind": kind})
        return self.request("memory.add", payload)

    def update_memory(self, memory_id: int, content: str, kind: str = "note") -> str | None:
        return self.request("memory.update", {"memory_id": memory_id, "content": content, "kind": kind})

    def delete_memory(self, memory_id: int) -> str | None:
        return self.request("memory.delete", {"memory_id": memory_id})

    def set_memory_enabled(self, key: ConversationKey, agent_id: str, enabled: bool) -> str | None:
        payload = self.scope(key)
        payload.update({"agent_id": agent_id, "enabled": enabled})
        return self.request("memory.settings.set", payload)

    def recall(self, message_id: int) -> str | None:
        return self.request("message.recall", {"message_id": message_id})

    def fetch_media(self, message_id: int) -> str | None:
        return self.request("media.get", {"message_id": message_id})

    def moderate(self, group_id: str, user_id: int, muted: bool) -> str | None:
        return self.request(
            "moderation.mute" if muted else "moderation.unmute",
            {"group_id": group_id, "user_id": user_id},
        )

    def set_schedule(self, group_id: str, minutes: int, enabled: bool) -> str | None:
        return self.request(
            "agent.schedule.set",
            {"group_id": group_id, "interval_minutes": minutes, "enabled": enabled},
        )

    def create_group(self, name: str) -> str | None:
        return self.request("group.create", {"name": name})

    def join_group(self, group_id: int) -> str | None:
        return self.request("group.join", {"group_id": group_id})

    def leave_group(self, group_id: int) -> str | None:
        return self.request("group.leave", {"group_id": group_id})
