"""Protocol command facade used by V2 view models and widgets."""

from __future__ import annotations

import time
from typing import Any

from .models import ConversationKey
from .network import NetworkClient
from .profile import ProfileViewModel, resolve_profile
from .store import AppStore


class ChatService:
    def __init__(self, network: NetworkClient, store: AppStore) -> None:
        self.network = network
        self.store = store
        self._avatar_requests: dict[tuple[int, int], float] = {}

    def request(self, event_type: str, payload: dict[str, Any] | None = None) -> str | None:
        try:
            request_id = self.network.send(event_type, payload or {})
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
        return self.request("auth.login", {"username": username, "password": password})

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
            ("agent.activity.list", {"limit": 100}),
            ("tool.calls.list", {"limit": 100}),
        ):
            self.request(event_type, payload)
        if self.store.state.current_user.get("role") == "admin":
            self.request("system.status")

    def open_conversation(self, key: ConversationKey) -> None:
        self.store.select_conversation(key)
        payload = self.scope(key)
        payload.update({"limit": 50, "media_mode": "metadata"})
        self.request("history.sync", payload)
        if key.kind == "group":
            self.request("group.members", {"group_id": key.identifier})

    def load_older(self, key: ConversationKey, before_id: int) -> None:
        payload = self.scope(key)
        payload.update({"before_id": before_id, "limit": 50, "media_mode": "metadata"})
        self.request("history.sync", payload)

    def send_text(
        self, key: ConversationKey, content: str, mentions: list[dict[str, Any]] | None = None
    ) -> str | None:
        payload = self.scope(key)
        payload.update(
            {
                "message_type": "text",
                "content": content,
                "mentions": mentions or [],
                "mention_index_unit": "unicode_codepoints",
            }
        )
        return self.request("message.send", payload)

    def set_avatar(self, mime: str | None = None, encoded: str = "") -> str | None:
        return self.request("profile.avatar.set", {"image_mime": mime, "image_data": encoded})

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

    def profile(self, user_id: int | None, fallback: dict[str, Any] | None = None) -> ProfileViewModel:
        return resolve_profile(self.store.state, user_id, fallback)

    def avatar_data(self, profile: ProfileViewModel) -> str | None:
        if profile.kind == "agent" or profile.user_id is None:
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
        return self.request("message.send", payload)

    def search(self, key: ConversationKey, keyword: str) -> str | None:
        payload = self.scope(key)
        payload.update({"keyword": keyword, "limit": 50})
        return self.request("history.search", payload)

    def invoke_agent(self, key: ConversationKey, prompt: str) -> str | None:
        payload = self.scope(key)
        payload.update({"prompt": prompt, "task_type": "query", "limit": 200})
        return self.request("agent.invoke", payload)

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
