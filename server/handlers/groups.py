"""Group, private conversation and moderation handlers."""

from __future__ import annotations

from typing import Any
from ..session import ClientSession
from ..database import DatabaseError, dm_conversation_id, dm_participants
import asyncio


class GroupHandlers:
    """Group, private conversation and moderation handlers."""

    async def _group_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        groups = await asyncio.to_thread(self.database.list_groups, int(session.user["id"]))
        await session.send("group.list", {"groups": groups}, request_id=request_id)

    async def _group_create(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group = await asyncio.to_thread(self.database.create_group, str(payload.get("name", "")), int(session.user["id"]))
        await session.send("group.created", {"group": group}, request_id=request_id)
        await self._refresh_groups_for_all()

    async def _group_join(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        await asyncio.to_thread(self.database.join_group, group_id, int(session.user["id"]))
        await session.send("ack", {"action": "group.join", "group_id": group_id}, request_id=request_id)
        await self._refresh_groups_for_all()

    async def _group_leave(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        await asyncio.to_thread(self.database.leave_group, group_id, int(session.user["id"]))
        await session.send("ack", {"action": "group.leave", "group_id": group_id}, request_id=request_id)
        await self._refresh_groups_for_all()

    async def _group_nickname_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        user_id = int(session.user["id"])
        nickname = await asyncio.to_thread(
            self.database.set_group_nickname, group_id, user_id, str(payload.get("nickname", ""))
        )
        await session.send("ack", {"action": "group.nickname.set", "group_id": group_id}, request_id=request_id)
        await self.emit_to_scope("group", str(group_id), "group.nickname.updated", {
            "group_id": group_id, "user_id": user_id, "nickname": nickname,
        })

    async def _group_members(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        actor_id = int(session.user["id"])
        if not await asyncio.to_thread(self.database.is_group_member, group_id, actor_id):
            raise DatabaseError("无权查看该群成员")
        members, can_manage, schedule = await asyncio.gather(
            asyncio.to_thread(self.database.group_members, group_id),
            asyncio.to_thread(self.database.can_manage_group, group_id, actor_id),
            asyncio.to_thread(self.database.get_schedule, group_id),
        )
        for member in members:
            member["member_id"] = f"user:{member['id']}"
            member["kind"] = "user"
            member["online"] = int(member["id"]) in self.sessions_by_user
            member["available_actions"] = (
                ["unmute" if member.get("muted") else "mute"]
                if can_manage and int(member["id"]) != actor_id
                else []
            )
        agents = await asyncio.to_thread(self.database.group_agents, group_id, enabled_only=True)
        for agent in agents:
            members.append(
                {
                    "id": agent["id"],
                    "member_id": agent["member_id"],
                    "username": agent["name"],
                    "display_name": agent["name"],
                    "description": agent.get("description", ""),
                    "kind": "agent",
                    "group_role": "agent",
                    "online": self.agent.enabled and self.agent.online and bool(agent.get("enabled")),
                    "avatar_version": int(agent.get("avatar_version", 0) or 0),
                    "avatar_mime": agent.get("avatar_mime"),
                    "available_actions": [],
                }
            )
        await session.send(
            "group.members",
            {
                "group_id": group_id,
                "members": members,
                "permissions": {
                    "can_manage_members": can_manage,
                    "can_manage_schedule": can_manage,
                },
                "schedule": schedule,
            },
            request_id=request_id,
        )

    async def _conversation_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        conversations = await asyncio.to_thread(
            self.database.list_conversations,
            int(session.user["id"]),
            int(payload.get("limit", 100)),
        )
        await session.send(
            "conversation.list", {"conversations": conversations}, request_id=request_id
        )

    async def _private_open(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        conversation = await asyncio.to_thread(
            self.database.open_private, int(session.user["id"]), int(payload["target_user_id"])
        )
        await session.send("private.opened", {"conversation": conversation}, request_id=request_id)

    async def _private_block_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        if type(payload.get("blocked")) is not bool:
            raise DatabaseError("屏蔽状态无效")
        user_id = int(session.user["id"])
        peer_id = int(payload["target_user_id"])
        own_state = await asyncio.to_thread(self.database.set_private_block, user_id, peer_id, payload["blocked"])
        peer_state = await asyncio.to_thread(self.database.private_state, peer_id, user_id)
        await session.send("ack", {"action": "private.block.set"}, request_id=request_id)
        await self._send_to_users({user_id}, "private.state", own_state)
        await self._send_to_users({peer_id}, "private.state", peer_state)

    async def _private_typing(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        if type(payload.get("active")) is not bool:
            raise DatabaseError("输入状态无效")
        scope_type, scope_id = await self._resolve_scope(session, {"conversation_type": "private", "conversation_id": payload["conversation_id"]})
        await asyncio.to_thread(self.database.assert_private_can_send, scope_id)
        user_id = int(session.user["id"])
        peer_id = next(participant for participant in dm_participants(scope_id) if participant != user_id)
        await self._send_to_users({peer_id}, "private.typing", {"conversation_id": scope_id, "user_id": user_id, "active": payload["active"]})
        await session.send("ack", {"action": "private.typing"}, request_id=request_id)

    async def _resolve_scope(self, session: ClientSession, payload: dict[str, Any]) -> tuple[str, str]:
        scope_type = str(payload.get("conversation_type") or payload.get("scope_type") or "")
        user_id = int(session.user["id"])
        if scope_type == "group":
            scope_id = str(int(payload.get("conversation_id") or payload.get("scope_id")))
            if not await asyncio.to_thread(self.database.is_group_member, int(scope_id), user_id):
                raise DatabaseError("你尚未加入该群")
            return scope_type, scope_id
        if scope_type == "private":
            raw_scope = payload.get("conversation_id") or payload.get("scope_id")
            if raw_scope and str(raw_scope).startswith("dm:"):
                scope_id = str(raw_scope)
                participants = dm_participants(scope_id)
                if not participants or user_id not in participants:
                    raise DatabaseError("私聊会话无效")
                peer_id = participants[1] if participants[0] == user_id else participants[0]
                await asyncio.to_thread(self.database.get_user, peer_id)
                return scope_type, scope_id
            target_id = int(payload.get("target_user_id") or raw_scope)
            if target_id == user_id:
                raise DatabaseError("不能与自己私聊")
            await asyncio.to_thread(self.database.get_user, target_id)
            return scope_type, dm_conversation_id(user_id, target_id)
        raise DatabaseError("会话类型无效")

    async def _moderation_mute(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        target_id = int(payload["user_id"])
        duration = payload.get("duration_minutes")
        await asyncio.to_thread(
            self.database.set_mute, group_id, target_id, int(session.user["id"]), True, int(duration) if duration else None
        )
        await session.send("ack", {"action": "moderation.mute"}, request_id=request_id)
        await self.emit_to_scope("group", str(group_id), "moderation.changed", {"group_id": group_id, "user_id": target_id, "muted": True})

    async def _moderation_unmute(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        target_id = int(payload["user_id"])
        await asyncio.to_thread(self.database.set_mute, group_id, target_id, int(session.user["id"]), False)
        await session.send("ack", {"action": "moderation.unmute"}, request_id=request_id)
        await self.emit_to_scope("group", str(group_id), "moderation.changed", {"group_id": group_id, "user_id": target_id, "muted": False})
