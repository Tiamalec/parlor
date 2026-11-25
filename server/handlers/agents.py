"""Agent, memory and tool handlers."""

from __future__ import annotations

from ..constants import AGENT_MEMBER_ID
from ..media import ALLOWED_IMAGE_MIME, MAX_AVATAR_SIZE, valid_avatar
from typing import Any
from ..agent_config import BUILTIN_AGENT_PRESETS
from ..session import ClientSession
from ..database import DatabaseError
from pathlib import Path
import asyncio
import base64
import contextlib
import mimetypes
import re
import time
import uuid


class AgentHandlers:
    """Agent, memory and tool handlers."""

    async def _agent_invoke(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        if not self.agent.enabled:
            raise DatabaseError("Agent 功能当前已禁用")
        scope_type, scope_id = await self._resolve_scope(session, payload)
        if scope_type == "private":
            await asyncio.to_thread(self.database.assert_private_can_send, scope_id)
        if scope_type == "group" and await asyncio.to_thread(
            self.database.is_muted, int(scope_id), int(session.user["id"])
        ):
            raise DatabaseError("你已被该群禁言")
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise DatabaseError("Agent 指令不能为空")
        agent_id = str(payload.get("agent_id") or AGENT_MEMBER_ID)
        await asyncio.to_thread(
            self.database._assert_agent_scope,
            agent_id,
            int(session.user["id"]),
            scope_type,
            scope_id,
        )
        self._track(
            asyncio.create_task(
                self.agent.invoke(
                    int(session.user["id"]), scope_type, scope_id, prompt, agent_id
                )
            )
        )
        await session.send("ack", {"action": "agent.invoke", "queued": True}, request_id=request_id)

    async def _agent_schedule(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        schedule = await asyncio.to_thread(
            self.database.set_schedule,
            group_id,
            int(session.user["id"]),
            int(payload.get("interval_minutes", 60)),
            bool(payload.get("enabled", True)),
        )
        await session.send("agent.schedule.updated", {"schedule": schedule}, request_id=request_id)

    async def _agent_status(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        await session.send(
            "agent.status",
            self.agent.status(),
            request_id=request_id,
        )

    async def _agent_activity_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        activities = await asyncio.to_thread(
            self.database.list_agent_activity,
            int(session.user["id"]),
            int(payload.get("limit", 100)),
        )
        await session.send(
            "agent.activity.list", {"activities": activities}, request_id=request_id
        )

    @staticmethod
    def _public_agent(agent: dict[str, Any]) -> dict[str, Any]:
        result = dict(agent)
        result.pop("avatar_path", None)
        result.pop("owner_role", None)
        return result

    def _validate_agent_model(self, profile_id: Any) -> str | None:
        value = str(profile_id or "").strip()
        if not value:
            return None
        allowed = {item["id"] for item in self.agent.model_options()}
        if value not in allowed:
            raise DatabaseError("指定的模型配置不存在")
        return value

    async def _agent_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agents = await asyncio.to_thread(self.database.list_agents, int(session.user["id"]))
        await session.send(
            "agent.list",
            {
                "agents": [self._public_agent(agent) for agent in agents],
                "models": self.agent.model_options(),
                "presets": [dict(item) for item in BUILTIN_AGENT_PRESETS],
            },
            request_id=request_id,
        )

    async def _agent_create(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        profile_id = self._validate_agent_model(payload.get("model_profile_id"))
        group_id = int(payload["group_id"]) if payload.get("group_id") is not None else None
        if group_id is not None and not await asyncio.to_thread(
            self.database.can_manage_group, group_id, int(session.user["id"])
        ):
            raise DatabaseError("没有管理该群组 Agent 的权限")
        agent = await asyncio.to_thread(
            self.database.create_agent,
            int(session.user["id"]),
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            card=payload.get("card"),
            model_profile_id=profile_id,
            enabled=bool(payload.get("enabled", True)),
            memory_enabled=bool(payload.get("memory_enabled", True)),
            auto_sticker=bool(payload.get("auto_sticker", True)),
        )
        group_agents: list[dict[str, Any]] | None = None
        if group_id is not None:
            bound = await asyncio.to_thread(
                self.database.set_group_agent,
                group_id,
                agent["id"],
                int(session.user["id"]),
                True,
            )
            group_agents = [self._public_agent(item) for item in bound]
        public = self._public_agent(agent)
        await session.send(
            "agent.created",
            {
                "agent": public,
                "group_id": group_id,
                "group_agents": group_agents,
            },
            request_id=request_id,
        )
        await self._broadcast_all("agent.changed", {"action": "created", "agent": public})
        if group_id is not None and group_agents is not None:
            await self.emit_to_scope(
                "group",
                str(group_id),
                "agent.group.updated",
                {"group_id": group_id, "agents": group_agents},
            )

    async def _agent_update(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id = str(payload.get("agent_id", ""))
        values = {
            key: payload[key]
            for key in ("name", "description", "card", "enabled", "memory_enabled", "auto_sticker")
            if key in payload
        }
        if "model_profile_id" in payload:
            values["model_profile_id"] = self._validate_agent_model(payload.get("model_profile_id"))
        agent = await asyncio.to_thread(
            self.database.update_agent, agent_id, int(session.user["id"]), values
        )
        public = self._public_agent(agent)
        await session.send("agent.updated", {"agent": public}, request_id=request_id)
        await self._broadcast_all("agent.changed", {"action": "updated", "agent": public})

    async def _agent_delete(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id = str(payload.get("agent_id", ""))
        agent = await asyncio.to_thread(self.database.get_agent, agent_id)
        await asyncio.to_thread(self.database.delete_agent, agent_id, int(session.user["id"]))
        stored = agent.get("avatar_path")
        if stored:
            path = Path(str(stored))
            if not path.is_absolute():
                path = self.config.uploads_dir / path
            root = (self.config.uploads_dir / "agent-avatars").resolve()
            with contextlib.suppress(OSError, ValueError):
                if path.resolve().is_relative_to(root):
                    path.unlink(missing_ok=True)
        await session.send("ack", {"action": "agent.delete", "agent_id": agent_id}, request_id=request_id)
        await self._broadcast_all("agent.changed", {"action": "deleted", "agent_id": agent_id})

    async def _agent_avatar_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id = str(payload.get("agent_id", ""))
        await asyncio.to_thread(self.database._assert_agent_manager, agent_id, int(session.user["id"]))
        encoded = str(payload.get("image_data", ""))
        path: str | None = None
        mime: str | None = None
        if encoded:
            mime = str(payload.get("image_mime", ""))
            if mime not in ALLOWED_IMAGE_MIME:
                raise DatabaseError("Agent 头像仅支持 PNG、JPEG、GIF 或 WebP")
            raw = base64.b64decode(encoded, validate=True)
            if not raw or len(raw) > MAX_AVATAR_SIZE:
                raise DatabaseError("Agent 头像大小需在 1 字节至 2 MiB 之间")
            if not valid_avatar(raw, mime):
                raise DatabaseError("Agent 头像文件内容与格式不匹配")
            avatar_dir = self.config.uploads_dir / "agent-avatars"
            avatar_dir.mkdir(parents=True, exist_ok=True)
            self.database._secure_path(avatar_dir, 0o700)
            safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", agent_id)[:60]
            target = avatar_dir / f"{safe_id}-{uuid.uuid4().hex}{mimetypes.guess_extension(mime) or '.img'}"
            target.write_bytes(raw)
            self.database._secure_path(target, 0o600)
            path = str(target)
        agent = await asyncio.to_thread(self.database.set_agent_avatar, agent_id, path, mime)
        previous = agent.pop("previous_avatar_path", None)
        if previous and previous != path:
            previous_path = Path(str(previous))
            if not previous_path.is_absolute():
                previous_path = self.config.uploads_dir / previous_path
            root = (self.config.uploads_dir / "agent-avatars").resolve()
            with contextlib.suppress(OSError, ValueError):
                if previous_path.resolve().is_relative_to(root):
                    previous_path.unlink(missing_ok=True)
        await session.send("ack", {"action": "agent.avatar.set", "agent_id": agent_id}, request_id=request_id)
        await self._broadcast_all("agent.changed", {"action": "updated", "agent": self._public_agent(agent)})

    async def _agent_avatar_get(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id = str(payload.get("agent_id", ""))
        avatar = await asyncio.to_thread(self.database.agent_avatar_file, agent_id)
        response: dict[str, Any] = {
            "agent_id": agent_id,
            "avatar_version": avatar["version"],
            "avatar_mime": avatar["mime"],
        }
        path = avatar.get("resolved_path")
        if path:
            candidate = Path(str(path))
            root = (self.config.uploads_dir / "agent-avatars").resolve()
            try:
                if not candidate.resolve().is_relative_to(root):
                    raise DatabaseError("Agent 头像路径无效")
                raw = candidate.read_bytes()
                if len(raw) > MAX_AVATAR_SIZE or not valid_avatar(raw, str(avatar["mime"])):
                    raise DatabaseError("Agent 头像文件无效")
                response["image_data"] = base64.b64encode(raw).decode("ascii")
            except OSError:
                response["load_failed"] = True
        await session.send("agent.avatar.result", response, request_id=request_id)

    async def _agent_tool_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id = str(payload.get("agent_id", ""))
        tools = await asyncio.to_thread(self.database.agent_tools, agent_id)
        await session.send(
            "agent.tools.list", {"agent_id": agent_id, "tools": tools}, request_id=request_id
        )

    async def _agent_tool_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id = str(payload.get("agent_id", ""))
        tools = await asyncio.to_thread(
            self.database.set_agent_tools,
            agent_id,
            int(session.user["id"]),
            payload.get("tools", []),
        )
        await session.send(
            "agent.tools.updated", {"agent_id": agent_id, "tools": tools}, request_id=request_id
        )

    async def _agent_group_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        if not await asyncio.to_thread(
            self.database.is_group_member, group_id, int(session.user["id"])
        ):
            raise DatabaseError("无权查看该群 Agent")
        agents = await asyncio.to_thread(self.database.group_agents, group_id)
        await session.send(
            "agent.group.list",
            {"group_id": group_id, "agents": [self._public_agent(agent) for agent in agents]},
            request_id=request_id,
        )

    async def _agent_group_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        agent_id = str(payload.get("agent_id", ""))
        enabled = bool(payload.get("enabled", True))
        agents = await asyncio.to_thread(
            self.database.set_group_agent,
            group_id,
            agent_id,
            int(session.user["id"]),
            enabled,
        )
        public = [self._public_agent(agent) for agent in agents]
        await session.send(
            "agent.group.updated",
            {"group_id": group_id, "agents": public},
            request_id=request_id,
        )
        await self.emit_to_scope(
            "group",
            str(group_id),
            "agent.group.updated",
            {"group_id": group_id, "agents": public},
        )

    async def _agent_group_tools_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        if not await asyncio.to_thread(
            self.database.can_manage_group, group_id, int(session.user["id"])
        ):
            raise DatabaseError("没有管理群组工具权限的权限")
        agent_id = str(payload.get("agent_id", ""))
        tools = await asyncio.to_thread(self.database.group_tool_states, group_id, agent_id)
        await session.send(
            "agent.group.tools.list",
            {"group_id": group_id, "agent_id": agent_id, "tools": tools},
            request_id=request_id,
        )

    async def _agent_group_tools_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        group_id = int(payload["group_id"])
        agent_id = str(payload.get("agent_id", ""))
        tools = await asyncio.to_thread(
            self.database.set_group_tools,
            group_id,
            agent_id,
            int(session.user["id"]),
            payload.get("tools", []),
        )
        await session.send(
            "agent.group.tools.updated",
            {"group_id": group_id, "agent_id": agent_id, "tools": tools},
            request_id=request_id,
        )

    async def _agent_group_sticker_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        if type(payload.get("enabled")) is not bool:
            raise DatabaseError("enabled 必须是 boolean")
        group_id = int(payload["group_id"])
        agent_id = str(payload.get("agent_id", ""))
        agents = await asyncio.to_thread(
            self.database.set_group_auto_sticker,
            group_id,
            agent_id,
            int(session.user["id"]),
            bool(payload.get("enabled", True)),
        )
        public = [self._public_agent(agent) for agent in agents]
        await session.send(
            "agent.group.updated",
            {"group_id": group_id, "agents": public},
            request_id=request_id,
        )
        await self.emit_to_scope(
            "group",
            str(group_id),
            "agent.group.updated",
            {"group_id": group_id, "agents": public},
        )

    async def _agent_models_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        await session.send(
            "agent.models.list", {"models": self.agent.model_options()}, request_id=request_id
        )

    async def _memory_scope(
        self, session: ClientSession, payload: dict[str, Any]
    ) -> tuple[str, str, str]:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        agent_id = str(payload.get("agent_id") or AGENT_MEMBER_ID)
        return agent_id, scope_type, scope_id

    async def _memory_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id, scope_type, scope_id = await self._memory_scope(session, payload)
        memories = await asyncio.to_thread(
            self.database.list_memories,
            agent_id,
            int(session.user["id"]),
            scope_type,
            scope_id,
        )
        enabled = await asyncio.to_thread(
            self.database.memory_effective,
            agent_id,
            scope_type,
            scope_id,
            int(session.user["id"]),
        )
        await session.send(
            "memory.list",
            {
                "agent_id": agent_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "enabled": enabled,
                "memories": memories,
            },
            request_id=request_id,
        )

    async def _memory_add(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id, scope_type, scope_id = await self._memory_scope(session, payload)
        memory = await asyncio.to_thread(
            self.database.save_memory,
            agent_id,
            int(session.user["id"]),
            scope_type,
            scope_id,
            str(payload.get("content", "")),
            kind=str(payload.get("kind", "note")),
        )
        await session.send("memory.saved", {"memory": memory}, request_id=request_id)

    async def _memory_update(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        memory = await asyncio.to_thread(
            self.database.update_memory,
            int(payload["memory_id"]),
            int(session.user["id"]),
            content=payload.get("content"),
            kind=payload.get("kind"),
        )
        await session.send("memory.updated", {"memory": memory}, request_id=request_id)

    async def _memory_delete(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        memory_id = int(payload["memory_id"])
        await asyncio.to_thread(
            self.database.delete_memory, memory_id, int(session.user["id"])
        )
        await session.send("memory.deleted", {"memory_id": memory_id}, request_id=request_id)

    async def _memory_settings_get(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id, scope_type, scope_id = await self._memory_scope(session, payload)
        enabled = await asyncio.to_thread(
            self.database.memory_setting_enabled,
            agent_id,
            scope_type,
            scope_id,
            int(session.user["id"]),
        )
        await session.send(
            "memory.settings.result",
            {
                "agent_id": agent_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "enabled": enabled,
            },
            request_id=request_id,
        )

    async def _memory_settings_set(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        agent_id, scope_type, scope_id = await self._memory_scope(session, payload)
        setting = await asyncio.to_thread(
            self.database.set_memory_enabled,
            agent_id,
            int(session.user["id"]),
            scope_type,
            scope_id,
            bool(payload.get("enabled", False)),
        )
        await session.send("memory.settings.updated", setting, request_id=request_id)

    async def _tool_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        from ..tools import tool_catalog

        await session.send("tool.list", {"tools": tool_catalog()}, request_id=request_id)

    async def _tool_calls_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        records = await asyncio.to_thread(self.database.recent_tool_calls, int(payload.get("limit", 100)))
        allowed: list[dict[str, Any]] = []
        for record in records:
            try:
                await asyncio.to_thread(
                    self.database._assert_access,
                    int(session.user["id"]),
                    record["conversation_type"],
                    record["conversation_id"],
                )
                allowed.append(record)
            except DatabaseError:
                continue
        await session.send("tool.calls.list", {"tool_calls": allowed}, request_id=request_id)

    async def _system_status(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        if session.user.get("role") != "admin":
            raise DatabaseError("仅管理员可以查看系统状态")
        counts = await asyncio.to_thread(self.database.system_counts)
        await session.send(
            "system.status",
            {
                "server_version": "2.0",
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
                "online_users": len(self.sessions_by_user),
                "total_users": counts["users"],
                "groups": counts["groups"],
                "agent_enabled": self.agent.enabled,
                "agent_online": self.agent.enabled and self.agent.online,
                "agent_model": self.agent.model,
            },
            request_id=request_id,
        )
