from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import mimetypes
import re
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.protocol import ProtocolError, encode_frame, envelope, read_frame

from .agent import AgentService
from .agent_config import BUILTIN_AGENT_PRESETS
from .config import Config
from .database import Database, DatabaseError, dm_conversation_id, dm_participants
from .todos import TodoService


LOGGER = logging.getLogger("chatroom.server")
MAX_IMAGE_SIZE = 2 * 1024 * 1024
MAX_AVATAR_SIZE = 2 * 1024 * 1024
MAX_AVATAR_DIMENSION = 4096
MAX_AVATAR_PIXELS = 16_000_000
MAX_TEXT_LENGTH = 4_000
ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
AGENT_MEMBER_ID = "agent"


def _valid_image_signature(raw: bytes, mime: str) -> bool:
    if mime == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    return False


def _image_dimensions(raw: bytes, mime: str) -> tuple[int, int] | None:
    """Read dimensions from supported image headers without decoding pixel data."""
    if mime == "image/png" and len(raw) >= 24:
        return struct.unpack(">II", raw[16:24])
    if mime == "image/gif" and len(raw) >= 10:
        return struct.unpack("<HH", raw[6:10])
    if mime == "image/webp" and len(raw) >= 30:
        chunk = raw[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(raw[24:27], "little"), 1 + int.from_bytes(raw[27:30], "little")
        if chunk == b"VP8 " and raw[23:26] == b"\x9d\x01\x2a":
            width, height = struct.unpack("<HH", raw[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if chunk == b"VP8L" and len(raw) >= 25 and raw[20] == 0x2F:
            bits = int.from_bytes(raw[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        return None
    if mime == "image/jpeg":
        offset = 2
        while offset + 4 <= len(raw):
            if raw[offset] != 0xFF:
                offset += 1
                continue
            marker = raw[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(raw):
                break
            segment_length = int.from_bytes(raw[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(raw):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if segment_length >= 7:
                    height = int.from_bytes(raw[offset + 3:offset + 5], "big")
                    width = int.from_bytes(raw[offset + 5:offset + 7], "big")
                    return width, height
                break
            offset += segment_length
    return None


def _valid_avatar(raw: bytes, mime: str) -> bool:
    if not _valid_image_signature(raw, mime):
        return False
    dimensions = _image_dimensions(raw, mime)
    if not dimensions:
        return False
    width, height = dimensions
    return (
        0 < width <= MAX_AVATAR_DIMENSION
        and 0 < height <= MAX_AVATAR_DIMENSION
        and width * height <= MAX_AVATAR_PIXELS
    )


@dataclass(eq=False)
class ClientSession:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    user: dict[str, Any] | None = None
    seen_requests: set[str] = field(default_factory=set)

    async def send(self, message_type: str, payload: dict[str, Any], *, request_id: str | None = None) -> None:
        message = envelope(message_type, payload, request_id=request_id)
        async with self.send_lock:
            self.writer.write(encode_frame(message))
            await self.writer.drain()

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


class ChatServer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.database = Database(config.database_path, config.uploads_dir, config.password_pbkdf2_iterations)
        self.database.initialize(config.admin_username, config.admin_password)
        self.todos = TodoService(self.database)
        self.sessions: set[ClientSession] = set()
        self.sessions_by_user: dict[int, set[ClientSession]] = {}
        self.agent = AgentService(
            config,
            self.database,
            self.emit_to_scope,
            todos=self.todos,
            todo_event_callback=self._notify_todo_change,
        )
        self._server: asyncio.AbstractServer | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._scheduled_groups: set[int] = set()
        self._closing = False
        self.started_at = time.time()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_connection, self.config.host, self.config.port)
        self._track(asyncio.create_task(self._scheduler_loop(), name="agent-scheduler"))
        self._track(asyncio.create_task(self._agent_health_loop(), name="agent-health"))
        addresses = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        LOGGER.info("聊天室服务已启动：%s", addresses)

    async def serve_forever(self) -> None:
        if not self._server:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        self._closing = True
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for task in list(self._background_tasks):
            task.cancel()
        for session in list(self.sessions):
            await session.close()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = ClientSession(reader, writer)
        self.sessions.add(session)
        peer = writer.get_extra_info("peername")
        LOGGER.info("客户端连接：%s", peer)
        try:
            while True:
                try:
                    message = await read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                except ProtocolError as exc:
                    await session.send("error", {"code": "invalid_frame", "message": str(exc)})
                    break
                await self._dispatch(session, message)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            LOGGER.exception("处理客户端连接时发生未捕获异常")
        finally:
            await self._disconnect(session)
            LOGGER.info("客户端断开：%s", peer)

    async def _disconnect(self, session: ClientSession) -> None:
        self.sessions.discard(session)
        await self._detach_user(session)
        await session.close()

    async def _detach_user(self, session: ClientSession) -> None:
        if session.user:
            user_id = int(session.user["id"])
            user_sessions = self.sessions_by_user.get(user_id)
            if user_sessions:
                user_sessions.discard(session)
                if not user_sessions:
                    self.sessions_by_user.pop(user_id, None)
                    await self._broadcast_presence(user_id, False)
            session.user = None

    async def _dispatch(self, session: ClientSession, message: dict[str, Any]) -> None:
        request_id = message["request_id"]
        message_type = message["type"]
        payload = message["payload"]
        if request_id in session.seen_requests:
            await session.send("ack", {"duplicate": True}, request_id=request_id)
            return
        session.seen_requests.add(request_id)
        if len(session.seen_requests) > 2_000:
            session.seen_requests = set(list(session.seen_requests)[-1_000:])
        try:
            if message_type == "auth.register":
                await self._auth_register(session, payload, request_id)
                return
            if message_type == "auth.login":
                await self._auth_login(session, payload, request_id)
                return
            if message_type == "heartbeat.ping":
                await session.send("heartbeat.pong", {"server_time": time.time()}, request_id=request_id)
                return
            if not session.user:
                raise DatabaseError("请先登录")
            handlers = {
                "presence.list": self._presence_list,
                "profile.avatar.set": self._profile_avatar_set,
                "profile.identity.set": self._profile_identity_set,
                "profile.avatar.get": self._profile_avatar_get,
                "auth.logout": self._auth_logout,
                "group.list": self._group_list,
                "group.create": self._group_create,
                "group.join": self._group_join,
                "group.leave": self._group_leave,
                "group.members": self._group_members,
                "group.nickname.set": self._group_nickname_set,
                "conversation.list": self._conversation_list,
                "private.open": self._private_open,
                "private.block.set": self._private_block_set,
                "private.typing": self._private_typing,
                "message.send": self._message_send,
                "message.status": self._message_status,
                "message.edit": self._message_edit,
                "message.recall": self._message_recall,
                "media.get": self._media_get,
                "history.sync": self._history_sync,
                "history.search": self._history_search,
                "history.events": self._history_events,
                "history.read": self._history_read,
                "todo.list": self._todo_list,
                "todo.create": self._todo_create,
                "todo.update": self._todo_update,
                "todo.complete": self._todo_complete,
                "todo.delete": self._todo_delete,
                "todo.import": self._todo_import,
                "todo.resolve": self._todo_resolve,
                "moderation.mute": self._moderation_mute,
                "moderation.unmute": self._moderation_unmute,
                "agent.invoke": self._agent_invoke,
                "agent.status": self._agent_status,
                "agent.activity.list": self._agent_activity_list,
                "agent.schedule.set": self._agent_schedule,
                "agent.list": self._agent_list,
                "agent.create": self._agent_create,
                "agent.update": self._agent_update,
                "agent.delete": self._agent_delete,
                "agent.avatar.set": self._agent_avatar_set,
                "agent.avatar.get": self._agent_avatar_get,
                "agent.tool.list": self._agent_tool_list,
                "agent.tool.set": self._agent_tool_set,
                "agent.group.list": self._agent_group_list,
                "agent.group.set": self._agent_group_set,
                "agent.group.tools.list": self._agent_group_tools_list,
                "agent.group.tools.set": self._agent_group_tools_set,
                "agent.group.sticker.set": self._agent_group_sticker_set,
                "agent.models.list": self._agent_models_list,
                "memory.list": self._memory_list,
                "memory.add": self._memory_add,
                "memory.update": self._memory_update,
                "memory.delete": self._memory_delete,
                "memory.settings.get": self._memory_settings_get,
                "memory.settings.set": self._memory_settings_set,
                "tool.calls.list": self._tool_calls_list,
                "tool.list": self._tool_list,
                "system.status": self._system_status,
            }
            handler = handlers.get(message_type)
            if not handler:
                raise DatabaseError(f"未知消息类型：{message_type}")
            await handler(session, payload, request_id)
        except (DatabaseError, ValueError, KeyError, binascii.Error) as exc:
            await session.send("error", {"code": "request_failed", "message": str(exc)}, request_id=request_id)
        except Exception as exc:
            LOGGER.exception("请求处理失败：%s", message_type)
            await session.send("error", {"code": "internal_error", "message": "服务器内部错误"}, request_id=request_id)

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

    async def _todo_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        result = await asyncio.to_thread(self.todos.list, int(session.user["id"]))
        await session.send("todo.list", result, request_id=request_id)

    async def _notify_todo_change(
        self, owner_id: int, event_type: str, payload: dict[str, Any]
    ) -> None:
        await self._send_to_users({owner_id}, event_type, payload)

    async def _todo_create(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        if payload.get("source_type", "manual") != "manual" or any(
            payload.get(key) is not None for key in
            ("source_conversation_type", "source_conversation_id", "source_message_id")
        ):
            raise DatabaseError("客户端只能手动创建待办")
        result = await asyncio.to_thread(
            self.todos.create, int(session.user["id"]), title=payload.get("title", ""),
            description=payload.get("description", ""), due_at=payload.get("due_at"),
            priority=payload.get("priority", "normal"),
        )
        await session.send("todo.created", result, request_id=request_id)
        await self._send_to_users({int(session.user["id"])}, "todo.changed", result)

    async def _todo_update(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        changes = {key: payload[key] for key in ("title", "description", "due_at", "priority") if key in payload}
        result = await asyncio.to_thread(
            self.todos.update,
            int(session.user["id"]),
            int(payload["todo_id"]),
            actor="manual",
            expected_version=payload.get("expected_version"),
            **changes,
        )
        await session.send("todo.updated", result, request_id=request_id)
        await self._send_to_users({int(session.user["id"])}, "todo.changed", result)

    async def _todo_complete(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        result = await asyncio.to_thread(
            self.todos.complete,
            int(session.user["id"]),
            int(payload["todo_id"]),
            payload.get("completed", True),
            actor="manual",
            expected_version=payload.get("expected_version"),
        )
        await session.send("todo.updated", result, request_id=request_id)
        await self._send_to_users({int(session.user["id"])}, "todo.changed", result)

    async def _todo_delete(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        result = await asyncio.to_thread(
            self.todos.delete,
            int(session.user["id"]),
            int(payload["todo_id"]),
            actor="manual",
            expected_version=payload.get("expected_version"),
        )
        await session.send("todo.deleted", result, request_id=request_id)
        await self._send_to_users({int(session.user["id"])}, "todo.removed", result)

    async def _todo_import(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        user_id = int(session.user["id"])
        message = await asyncio.to_thread(
            self.database.authorized_message, user_id, int(payload.get("message_id", 0))
        )
        card = message.get("card")
        if not isinstance(card, dict) or card.get("kind") != "todo":
            raise DatabaseError("该消息不包含待办提取结果")
        raw_indices = payload.get("task_indices")
        if not isinstance(raw_indices, list):
            raise DatabaseError("请选择要加入的待办")
        indices: list[int] = []
        for raw in raw_indices:
            if isinstance(raw, bool):
                continue
            try:
                index = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(card.get("tasks", [])) and index not in indices:
                indices.append(index)
        if not indices:
            raise DatabaseError("请选择要加入的待办")
        if len(indices) > 40:
            raise DatabaseError("单次最多加入 40 项待办")
        current_name = await asyncio.to_thread(
            self.database.name_in_scope,
            user_id,
            str(message["conversation_type"]),
            str(message["conversation_id"]),
        )
        outcomes: list[dict[str, Any]] = []
        for index in indices:
            task = card["tasks"][index]
            if not isinstance(task, dict):
                outcomes.append({"index": index, "status": "failed", "error": "待办数据无效"})
                continue
            owner = str(task.get("owner") or "").strip()
            assignee_id = task.get("assignee_user_id")
            is_current = (
                assignee_id is not None and int(assignee_id) == user_id
            ) or bool(owner and owner == current_name)
            is_unassigned = not owner
            if not is_current and not is_unassigned:
                outcomes.append({"index": index, "status": "skipped", "error": "该任务由其他成员负责"})
                continue
            if owner and not is_current and owner != current_name:
                outcomes.append({"index": index, "status": "skipped", "error": "该任务负责人不是当前用户"})
                continue
            try:
                sources = [
                    int(value)
                    for value in task.get("sources", [])
                    if not isinstance(value, bool) and str(value).isdigit()
                ]
                if not sources:
                    outcomes.append(
                        {"index": index, "status": "skipped", "error": "缺少可验证的来源消息"}
                    )
                    continue
                result = await asyncio.to_thread(
                    self.todos.create,
                    user_id,
                    title=str(task.get("title") or ""),
                    description=str(task.get("description") or ""),
                    due_at=task.get("due_at"),
                    priority=str(task.get("priority") or "normal"),
                    source_type="agent_extracted",
                    source_conversation_type=str(message["conversation_type"]),
                    source_conversation_id=str(message["conversation_id"]),
                    source_message_id=sources[0],
                    actor="manual",
                )
                outcomes.append(
                    {
                        "index": index,
                        "status": "created" if result.get("created") else "matched",
                        "todo_id": result["todo"]["id"],
                        "title": result["todo"]["title"],
                    }
                )
                if result.get("created"):
                    await self._send_to_users({user_id}, "todo.changed", result)
                if task.get("status") == "completed" and not result["todo"].get("completed"):
                    try:
                        completed = await asyncio.to_thread(
                            self.todos.complete,
                            user_id,
                            int(result["todo"]["id"]),
                            True,
                            actor="manual",
                            expected_version=int(result["todo"].get("version") or 1),
                        )
                        outcomes[-1]["completed"] = True
                        await self._send_to_users({user_id}, "todo.changed", completed)
                    except Exception as exc:
                        outcomes[-1]["completion_error"] = str(exc)
            except Exception as exc:
                outcomes.append({"index": index, "status": "failed", "error": str(exc)})
        result = {
            "message_id": message["id"],
            "outcomes": outcomes,
            "created": sum(item["status"] == "created" for item in outcomes),
            "matched": sum(item["status"] == "matched" for item in outcomes),
            "skipped": sum(item["status"] == "skipped" for item in outcomes),
            "failed": sum(item["status"] == "failed" for item in outcomes),
        }
        result["status"] = (
            "success"
            if result["failed"] == 0
            else "failed"
            if result["created"] + result["matched"] == 0
            else "partial"
        )
        if result["failed"]:
            result["error"] = f"{result['failed']} 项待办加入失败"
        await session.send("todo.imported", result, request_id=request_id)

    async def _todo_resolve(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        result = await asyncio.to_thread(self.todos.resolve_message, int(session.user["id"]), int(payload["todo_id"]))
        await session.send("todo.resolved", result, request_id=request_id)

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

    async def _profile_avatar_set(
        self, session: ClientSession, payload: dict[str, Any], request_id: str
    ) -> None:
        user_id = int(session.user["id"])
        encoded = str(payload.get("image_data", ""))
        path: str | None = None
        mime: str | None = None
        if encoded:
            mime = str(payload.get("image_mime", ""))
            if mime not in ALLOWED_IMAGE_MIME:
                raise DatabaseError("头像仅支持 PNG、JPEG、GIF 或 WebP")
            raw = base64.b64decode(encoded, validate=True)
            if not raw or len(raw) > MAX_AVATAR_SIZE:
                raise DatabaseError("头像大小需在 1 字节至 2 MiB 之间")
            if not _valid_avatar(raw, mime):
                raise DatabaseError("头像文件内容与格式不匹配")
            avatar_dir = self.config.uploads_dir / "avatars"
            avatar_dir.mkdir(parents=True, exist_ok=True)
            self.database._secure_path(avatar_dir, 0o700)
            suffix = mimetypes.guess_extension(mime) or ".img"
            target = avatar_dir / f"{user_id}-{uuid.uuid4().hex}{suffix}"
            target.write_bytes(raw)
            self.database._secure_path(target, 0o600)
            path = str(target)
        user = await asyncio.to_thread(self.database.set_avatar, user_id, path, mime)
        previous = user.pop("previous_avatar_path", None)
        if previous and previous != path:
            previous_path = Path(str(previous))
            if not previous_path.is_absolute():
                previous_path = self.config.uploads_dir / previous_path
            avatar_root = (self.config.uploads_dir / "avatars").resolve()
            with contextlib.suppress(OSError, ValueError):
                if previous_path.resolve().is_relative_to(avatar_root):
                    previous_path.unlink(missing_ok=True)
        session.user.update(user)
        await session.send("ack", {"action": "profile.avatar.set"}, request_id=request_id)
        await self._broadcast_all("profile.updated", {"user": user})

    async def _profile_avatar_get(
        self, session: ClientSession, payload: dict[str, Any], request_id: str
    ) -> None:
        user_id = int(payload.get("user_id", session.user["id"]))
        avatar = await asyncio.to_thread(self.database.avatar_file, user_id)
        response: dict[str, Any] = {
            "user_id": user_id,
            "avatar_version": avatar["version"],
            "avatar_mime": avatar["mime"],
        }
        path = avatar.get("resolved_path")
        if path:
            candidate = Path(str(path))
            avatar_root = (self.config.uploads_dir / "avatars").resolve()
            try:
                if not candidate.resolve().is_relative_to(avatar_root):
                    raise DatabaseError("头像路径无效")
                raw = candidate.read_bytes()
                if len(raw) > MAX_AVATAR_SIZE or not _valid_avatar(raw, str(avatar["mime"])):
                    raise DatabaseError("头像文件无效")
                response["image_data"] = base64.b64encode(raw).decode("ascii")
            except OSError:
                response["load_failed"] = True
        await session.send("profile.avatar.result", response, request_id=request_id)

    async def _broadcast_presence(self, user_id: int, online: bool) -> None:
        try:
            user = await asyncio.to_thread(self.database.get_user, user_id)
        except DatabaseError:
            return
        await self._broadcast_all("presence.changed", {"user": user, "online": online})

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

    async def _message_status(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        client_request_id = str(payload.get("client_request_id", "")).strip()
        if not client_request_id:
            raise DatabaseError("请求 ID 不能为空")
        message = await asyncio.to_thread(self.database.get_message_by_request, int(session.user["id"]), client_request_id)
        result: dict[str, Any] = {"client_request_id": client_request_id, "found": message is not None}
        if message:
            result["message_id"] = message["id"]
        await session.send("message.status", result, request_id=request_id)

    async def _message_send(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        prior = await asyncio.to_thread(self.database.get_message_by_request, int(session.user["id"]), request_id)
        if prior:
            await session.send("ack", {"action": "message.send", "message_id": prior["id"], "duplicate": True}, request_id=request_id)
            return
        message_type = str(payload.get("message_type", "text"))
        content = str(payload.get("content", ""))
        if message_type not in {"text", "image", "sticker"}:
            raise DatabaseError("消息类型无效")
        if len(content) > MAX_TEXT_LENGTH:
            raise DatabaseError("文本消息过长")
        if scope_type == "group" and await asyncio.to_thread(
            self.database.is_muted, int(scope_id), int(session.user["id"])
        ):
            raise DatabaseError("你已被该群禁言")
        if scope_type == "private":
            await asyncio.to_thread(self.database.assert_private_can_send, scope_id)
        reply_to_id = int(payload["reply_to_id"]) if payload.get("reply_to_id") is not None else None
        reply_preview = None
        if reply_to_id is not None:
            original = await asyncio.to_thread(self.database.authorized_message, int(session.user["id"]), reply_to_id)
            if original["conversation_type"] != scope_type or original["conversation_id"] != scope_id or original["recalled"]:
                raise DatabaseError("引用消息不属于当前会话或已撤回")
            if original["message_type"] == "image":
                preview_content = "[图片]"
            elif original["message_type"] == "sticker":
                preview_content = f"[表情包] {original.get('sticker_name') or ''}".strip()
            elif str((original.get("card") or {}).get("kind")) == "summary":
                preview_content = "[群聊总结]"
            elif str((original.get("card") or {}).get("kind")) == "todo":
                preview_content = "[待办事项]"
            else:
                preview_content = original["content"][:80]
            reply_preview = f"{original['sender_name']}: {preview_content}"
        image_path = image_name = image_mime = None
        if message_type in {"image", "sticker"}:
            image_path, image_name, image_mime = await self._store_image(payload)
        elif not content.strip():
            raise DatabaseError("消息内容不能为空")
        sticker_id = sticker_name = sticker_category = None
        if message_type == "sticker":
            supplied_id = str(payload.get("sticker_id") or request_id).strip()[:80]
            sticker_id = f"user:{int(session.user['id'])}:{supplied_id or request_id}"
            sticker_name = str(payload.get("sticker_name") or Path(image_name or "表情包").stem).strip()[:80]
            sticker_name = sticker_name or "表情包"
            sticker_category = "用户表情"
        normalized_content = content.strip()
        raw_mentions = self._normalize_mention_offsets(
            content,
            payload.get("mentions"),
            str(payload.get("mention_index_unit", "unicode_codepoints")),
        )
        mentions = await self._validate_mentions(scope_type, scope_id, normalized_content, raw_mentions)
        message = await asyncio.to_thread(
            self.database.save_message,
            scope_type,
            scope_id,
            int(session.user["id"]),
            await asyncio.to_thread(self.database.name_in_scope, int(session.user["id"]), scope_type, scope_id),
            message_type,
            normalized_content,
            image_path=image_path,
            image_name=image_name,
            image_mime=image_mime,
            sticker_id=sticker_id,
            sticker_name=sticker_name,
            sticker_category=sticker_category,
            sticker_tags=[],
            mentions=mentions,
            reply_to_id=reply_to_id,
            reply_preview=reply_preview,
            client_request_id=request_id,
        )
        if message.get("duplicate"):
            await session.send("ack", {"action": "message.send", "message_id": message["id"], "duplicate": True}, request_id=request_id)
            return
        if scope_type != "private":
            await asyncio.to_thread(
                self.database.set_read_cursor, int(session.user["id"]), scope_type, scope_id, int(message["id"])
            )
        wire_message = await self._with_image_data(message)
        await self.emit_to_scope(scope_type, scope_id, "message.event", {"message": wire_message})
        await session.send("ack", {"action": "message.send", "message_id": message["id"]}, request_id=request_id)
        triggered: list[str] = []
        for item in mentions:
            if item["kind"] != "agent":
                continue
            agent_id = str(item.get("agent_id") or AGENT_MEMBER_ID)
            if agent_id not in triggered:
                triggered.append(agent_id)
        if triggered:
            prompt = normalized_content
            for item in reversed(mentions):
                if item["kind"] == "agent":
                    start = int(item["start"])
                    prompt = prompt[:start] + prompt[start + int(item["length"]):]
            prompt = prompt.strip() or "请总结最近 50 条消息"
            for agent_id in triggered[:4]:
                self._track(
                    asyncio.create_task(
                        self.agent.invoke(
                            int(session.user["id"]), scope_type, scope_id, prompt, agent_id
                        )
                    )
                )

    async def _validate_mentions(
        self, scope_type: str, scope_id: str, content: str, raw_mentions: Any
    ) -> list[dict[str, Any]]:
        if raw_mentions in (None, []):
            return []
        if scope_type != "group" or not isinstance(raw_mentions, list):
            raise DatabaseError("提及信息仅适用于群聊")
        members = await asyncio.to_thread(self.database.group_members, int(scope_id))
        allowed = {f"user:{member['id']}": member for member in members}
        agent_rows = await asyncio.to_thread(
            self.database.group_agents, int(scope_id), enabled_only=True
        )
        agents = {agent["member_id"]: agent for agent in agent_rows}
        normalized: list[dict[str, Any]] = []
        previous_end = -1
        for raw in sorted(raw_mentions, key=lambda item: int(item.get("start", -1)) if isinstance(item, dict) else -1):
            if not isinstance(raw, dict):
                raise DatabaseError("提及信息无效")
            member_id = str(raw.get("member_id", ""))
            start = int(raw.get("start", -1))
            length = int(raw.get("length", 0))
            if member_id in agents:
                kind = "agent"
                display = str(agents[member_id]["name"])
                if str(raw.get("display") or content[start + 1:start + length]) != display:
                    raise DatabaseError("提及名称与 Agent 不一致")
            elif member_id in allowed:
                member = allowed[member_id]
                names = {str(member[key]) for key in ("nickname", "display_name", "username") if member.get(key)}
                kind = "user"
                display = str(raw.get("display") or content[start + 1:start + length])
                if display not in names:
                    raise DatabaseError("提及名称与成员不一致")
            else:
                raise DatabaseError("被提及成员不属于当前群聊")
            token = f"@{display}"
            if start < 0 or length != len(token) or start < previous_end or content[start:start + length] != token:
                raise DatabaseError("提及位置与消息文本不一致")
            previous_end = start + length
            item = {
                "member_id": member_id,
                "kind": kind,
                "display": display,
                "start": start,
                "length": length,
            }
            if kind == "agent":
                item["agent_id"] = agents[member_id]["id"]
            normalized.append(item)
        return normalized

    @staticmethod
    def _normalize_mention_offsets(content: str, raw_mentions: Any, index_unit: str) -> Any:
        if not isinstance(raw_mentions, list):
            return raw_mentions
        if index_unit not in {"unicode_codepoints", "utf16"}:
            raise DatabaseError("不支持的提及位置单位")
        leading = len(content) - len(content.lstrip())
        normalized: list[Any] = []
        for raw in raw_mentions:
            if not isinstance(raw, dict):
                normalized.append(raw)
                continue
            item = dict(raw)
            start = int(item.get("start", -1))
            length = int(item.get("length", 0))
            if index_unit == "utf16" and start >= 0 and length >= 0:
                end = ChatServer._utf16_to_codepoint_index(content, start + length)
                start = ChatServer._utf16_to_codepoint_index(content, start)
                length = end - start
            item["start"] = start - leading
            item["length"] = length
            normalized.append(item)
        return normalized

    @staticmethod
    def _utf16_to_codepoint_index(text: str, position: int) -> int:
        if position < 0:
            return position
        units = 0
        for index, character in enumerate(text):
            if units >= position:
                return index
            units += len(character.encode("utf-16-le")) // 2
            if units > position:
                raise DatabaseError("UTF-16 提及位置落在字符中间")
        if units != position:
            raise DatabaseError("UTF-16 提及位置超出消息范围")
        return len(text)

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

    async def _store_image(self, payload: dict[str, Any]) -> tuple[str, str, str]:
        encoded = str(payload.get("image_data", ""))
        mime = str(payload.get("image_mime", ""))
        original_name = Path(str(payload.get("image_name", "image"))).name[:120]
        if mime not in ALLOWED_IMAGE_MIME:
            raise DatabaseError("仅支持 PNG、JPEG、GIF 和 WebP 图片")
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_IMAGE_SIZE:
            raise DatabaseError("图片不能为空且不能超过2 MiB")
        if not _valid_image_signature(raw, mime):
            raise DatabaseError("图片内容与声明格式不一致")
        extension = mimetypes.guess_extension(mime) or ".img"
        filename = f"{uuid.uuid4().hex}{extension}"
        path = self.config.uploads_dir / filename
        await asyncio.to_thread(path.write_bytes, raw)
        return filename, original_name, mime

    async def _with_image_data(self, message: dict[str, Any]) -> dict[str, Any]:
        result = dict(message)
        image_path = result.get("image_path")
        if image_path and not result.get("recalled"):
            path = self.config.uploads_dir / Path(str(image_path)).name
            try:
                raw = await asyncio.to_thread(path.read_bytes)
                result["image_data"] = base64.b64encode(raw).decode("ascii")
            except OSError:
                result["image_missing"] = True
        result.pop("image_path", None)
        return result

    async def _media_get(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        message = await asyncio.to_thread(
            self.database.authorized_message,
            int(session.user["id"]),
            int(payload["message_id"]),
        )
        if message.get("message_type") not in {"image", "sticker"} or message.get("recalled"):
            raise DatabaseError("该消息没有可用图片")
        wire = await self._with_image_data(message)
        if not wire.get("image_data"):
            raise DatabaseError("图片文件不存在")
        await session.send("media.result", {"message": wire}, request_id=request_id)

    async def _history_sync(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        page = await asyncio.to_thread(
            self.database.history_page,
            int(session.user["id"]),
            scope_type,
            scope_id,
            before_id=(int(payload["before_id"]) if payload.get("before_id") is not None else None),
            after_id=(int(payload["after_id"]) if payload.get("after_id") is not None else None),
            limit=int(payload.get("limit", 50)),
        )
        if payload.get("media_mode") != "metadata":
            page["messages"] = [
                await self._with_image_data(message) for message in page["messages"]
            ]
        await session.send(
            "history.result",
            {
                "conversation_type": scope_type,
                "conversation_id": scope_id,
                **page,
            },
            request_id=request_id,
        )

    async def _history_search(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        page = await asyncio.to_thread(
            self.database.search_messages,
            int(session.user["id"]),
            scope_type,
            scope_id,
            str(payload.get("keyword", "")),
            int(payload.get("limit", 50)),
            int(payload["before_id"]) if payload.get("before_id") is not None else None,
            paged=True,
        )
        await session.send("history.result", {"conversation_type": scope_type, "conversation_id": scope_id,
                           "messages": page["messages"], "has_more": page["has_more"],
                           "keyword": str(payload.get("keyword", "")), "before_id": payload.get("before_id"),
                           "search": True}, request_id=request_id)

    async def _history_events(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        page = await asyncio.to_thread(self.database.message_events_for_user, int(session.user["id"]), max(0, int(payload.get("after_event_id", 0))))
        await session.send("history.events", page, request_id=request_id)

    async def _message_edit(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        message_id = int(payload["message_id"])
        original = await asyncio.to_thread(self.database.authorized_message, int(session.user["id"]), message_id)
        raw_content = str(payload.get("content", ""))
        content = raw_content.strip()
        if not content or len(content) > MAX_TEXT_LENGTH:
            raise DatabaseError("消息内容为空或过长")
        raw_mentions = self._normalize_mention_offsets(raw_content, payload.get("mentions", []), str(payload.get("mention_index_unit", "unicode_codepoints")))
        mentions = await self._validate_mentions(original["conversation_type"], original["conversation_id"], content, raw_mentions)
        message = await asyncio.to_thread(self.database.edit_message, message_id, int(session.user["id"]), content, mentions)
        await self.emit_to_scope(message["conversation_type"], message["conversation_id"], "message.edited", {"message": message})
        await session.send("ack", {"action": "message.edit", "message_id": message_id}, request_id=request_id)

    async def _history_read(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        message_id = int(payload["message_id"])
        advanced = await asyncio.to_thread(
            self.database.set_read_cursor,
            int(session.user["id"]),
            scope_type,
            scope_id,
            message_id,
        )
        await session.send("ack", {"action": "history.read"}, request_id=request_id)
        if advanced and scope_type == "private":
            await self.emit_to_scope(scope_type, scope_id, "private.read", {
                "conversation_id": scope_id,
                "reader_id": int(session.user["id"]),
                "message_id": message_id,
            })

    async def _message_recall(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        message = await asyncio.to_thread(self.database.recall_message, int(payload["message_id"]), int(session.user["id"]))
        await self.emit_to_scope(message["conversation_type"], message["conversation_id"], "message.recalled", {"message": message})
        await session.send("ack", {"action": "message.recall", "message_id": message["id"]}, request_id=request_id)

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
            if not _valid_avatar(raw, mime):
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
                if len(raw) > MAX_AVATAR_SIZE or not _valid_avatar(raw, str(avatar["mime"])):
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
        from .tools import tool_catalog

        await session.send("tool.list", {"tools": tool_catalog()}, request_id=request_id)

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

    async def _refresh_groups_for_all(self) -> None:
        for session in list(self.sessions):
            if not session.user:
                continue
            groups = await asyncio.to_thread(self.database.list_groups, int(session.user["id"]))
            with contextlib.suppress(Exception):
                await session.send("group.list", {"groups": groups})

    async def emit_to_scope(self, scope_type: str, scope_id: str, message_type: str, payload: dict[str, Any]) -> None:
        if message_type in {"agent.result", "tool.call.event", "agent.sticker.result"}:
            payload = {**payload, "conversation_type": scope_type, "conversation_id": scope_id}
        recipients: set[int]
        if scope_type == "group":
            members = await asyncio.to_thread(self.database.group_members, int(scope_id))
            recipients = {int(member["id"]) for member in members}
        elif scope_type == "private":
            recipients = set(dm_participants(scope_id) or ())
        else:
            return
        await self._send_to_users(recipients, message_type, payload)

    async def _send_to_users(self, user_ids: set[int], message_type: str, payload: dict[str, Any]) -> None:
        tasks = []
        for user_id in user_ids:
            for session in list(self.sessions_by_user.get(user_id, set())):
                tasks.append(session.send(message_type, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_all(self, message_type: str, payload: dict[str, Any]) -> None:
        tasks = [session.send(message_type, payload) for session in list(self.sessions) if session.user]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def online_user_ids(self) -> set[int]:
        return set(self.sessions_by_user)

    def admin_status_snapshot(self) -> dict[str, Any]:
        sockets = self._server.sockets if self._server else []
        return {
            "server_version": "2.1",
            "tcp_listening": bool(self._server and self._server.is_serving()),
            "tcp_addresses": [str(sock.getsockname()) for sock in sockets or []],
            "uptime_seconds": max(0, int(time.time() - self.started_at)),
            "connection_count": len(self.sessions),
            "authenticated_connections": sum(len(items) for items in self.sessions_by_user.values()),
            "online_users": len(self.sessions_by_user),
        }

    async def publish_agent_status(self) -> None:
        await self._broadcast_all("agent.status", self.agent.status())

    async def _scheduler_loop(self) -> None:
        while not self._closing:
            if not self.agent.enabled:
                await asyncio.sleep(15)
                continue
            try:
                schedules = await asyncio.to_thread(self.database.due_schedules)
                for schedule in schedules:
                    group_id = int(schedule["group_id"])
                    if group_id in self._scheduled_groups:
                        continue
                    self._scheduled_groups.add(group_id)
                    task = asyncio.create_task(self.agent.run_schedule(schedule))
                    task.add_done_callback(lambda _task, gid=group_id: self._scheduled_groups.discard(gid))
                    self._track(task)
            except Exception:
                LOGGER.exception("定时总结调度失败")
            await asyncio.sleep(15)

    async def _agent_health_loop(self) -> None:
        while not self._closing:
            previous = self.agent.online
            online = await self.agent.refresh_health() if self.agent.enabled else False
            if online != previous:
                await self.publish_agent_status()
            await asyncio.sleep(30)

