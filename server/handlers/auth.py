"""Authentication, presence and profile identity handlers."""

from __future__ import annotations

from typing import Any
from ..session import ClientSession
from ..database import DatabaseError
import asyncio
import contextlib


class AuthHandlers:
    """Authentication, presence and profile identity handlers."""

    async def _auth_register(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        user = await asyncio.to_thread(
            self.database.register_user, str(payload.get("username", "")), str(payload.get("password", ""))
        )
        await self._attach_user(session, user)
        await session.send("auth.result", await self._bootstrap(user, registered=True), request_id=request_id)

    async def _auth_login(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        user = await asyncio.to_thread(
            self.database.authenticate, str(payload.get("username", "")), str(payload.get("password", ""))
        )
        if not user:
            raise DatabaseError("账号或密码错误")
        await self._attach_user(session, user)
        await session.send("auth.result", await self._bootstrap(
            user,
            after_event_id=max(0, int(payload.get("after_event_id", 0))),
            include_legacy_offline="after_event_id" not in payload,
        ), request_id=request_id)

    async def _attach_user(self, session: ClientSession, user: dict[str, Any]) -> None:
        if session.user and session.user["id"] != user["id"]:
            raise DatabaseError("当前连接已经登录")
        first_online = int(user["id"]) not in self.sessions_by_user
        session.user = user
        self.sessions_by_user.setdefault(int(user["id"]), set()).add(session)
        if first_online:
            await self._broadcast_presence(int(user["id"]), True)

    async def _auth_logout(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        await self._detach_user(session)
        await session.send("ack", {"action": "auth.logout"}, request_id=request_id)

    async def _bootstrap(self, user: dict[str, Any], *, registered: bool = False, after_event_id: int = 0,
                         include_legacy_offline: bool = True) -> dict[str, Any]:
        users, groups = await asyncio.gather(
            asyncio.to_thread(self.database.list_users),
            asyncio.to_thread(self.database.list_groups, int(user["id"])),
        )
        for item in users:
            item["online"] = int(item["id"]) in self.sessions_by_user
        offline = await asyncio.to_thread(self.database.sync_for_user, int(user["id"])) if include_legacy_offline else []
        if include_legacy_offline:
            offline = [await self._with_image_data(message) for message in offline]
        changes = await asyncio.to_thread(self.database.message_events_for_user, int(user["id"]), after_event_id)
        return {
            "ok": True,
            "registered": registered,
            "user": user,
            "users": users,
            "groups": groups,
            "offline_messages": offline,
            "offline_events": changes["events"],
            "event_cursor": changes["cursor"],
            "events_has_more": changes["has_more"],
            "agent_online": self.agent.enabled and self.agent.online,
            "capabilities": {
                "conversation_list": True,
                "personal_todos": True,
                "private_chat_v2": True,
                "history_pagination": True,
                "media_get": True,
                "avatar_profiles": True,
                "structured_mentions": True,
                "mention_offsets_codepoints": True,
                "agent_activity": True,
                "agent_management": True,
                "structured_agent_cards": True,
                "agent_memory": True,
                "agent_tool_authorization": True,
                "system_status": user.get("role") == "admin",
            },
        }

    async def _presence_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        users = await asyncio.to_thread(self.database.list_users)
        for user in users:
            user["online"] = int(user["id"]) in self.sessions_by_user
        await session.send("presence.list", {"users": users}, request_id=request_id)

    async def _profile_identity_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        user = await asyncio.to_thread(
            self.database.update_identity, int(session.user["id"]),
            str(payload.get("username", "")), str(payload.get("display_name", "")),
        )
        for active in self.sessions_by_user.get(int(user["id"]), set()):
            active.user.update(user)
        await session.send("ack", {"action": "profile.identity.set"}, request_id=request_id)
        await self._broadcast_all("profile.updated", {"user": user})

    async def _broadcast_presence(self, user_id: int, online: bool) -> None:
        try:
            user = await asyncio.to_thread(self.database.get_user, user_id)
        except DatabaseError:
            return
        await self._broadcast_all("presence.changed", {"user": user, "online": online})

    async def _refresh_groups_for_all(self) -> None:
        for session in list(self.sessions):
            if not session.user:
                continue
            groups = await asyncio.to_thread(self.database.list_groups, int(session.user["id"]))
            with contextlib.suppress(Exception):
                await session.send("group.list", {"groups": groups})
