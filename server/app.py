from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import mimetypes
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.protocol import ProtocolError, encode_frame, envelope, read_frame

from .agent import AgentService
from .config import Config
from .database import Database, DatabaseError, dm_conversation_id, dm_participants


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
        self.sessions: set[ClientSession] = set()
        self.sessions_by_user: dict[int, set[ClientSession]] = {}
        self.agent = AgentService(config, self.database, self.emit_to_scope)
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
                "profile.avatar.get": self._profile_avatar_get,
                "auth.logout": self._auth_logout,
                "group.list": self._group_list,
                "group.create": self._group_create,
                "group.join": self._group_join,
                "group.leave": self._group_leave,
                "group.members": self._group_members,
                "conversation.list": self._conversation_list,
                "message.send": self._message_send,
                "message.recall": self._message_recall,
                "media.get": self._media_get,
                "history.sync": self._history_sync,
                "history.search": self._history_search,
                "history.read": self._history_read,
                "moderation.mute": self._moderation_mute,
                "moderation.unmute": self._moderation_unmute,
                "agent.invoke": self._agent_invoke,
                "agent.status": self._agent_status,
                "agent.activity.list": self._agent_activity_list,
                "agent.schedule.set": self._agent_schedule,
                "tool.calls.list": self._tool_calls_list,
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
            raise DatabaseError("用户名或密码错误")
        await self._attach_user(session, user)
        await session.send("auth.result", await self._bootstrap(user), request_id=request_id)

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

    async def _bootstrap(self, user: dict[str, Any], *, registered: bool = False) -> dict[str, Any]:
        users, groups, offline = await asyncio.gather(
            asyncio.to_thread(self.database.list_users),
            asyncio.to_thread(self.database.list_groups, int(user["id"])),
            asyncio.to_thread(self.database.sync_for_user, int(user["id"])),
        )
        for item in users:
            item["online"] = int(item["id"]) in self.sessions_by_user
        offline = [await self._with_image_data(message) for message in offline]
        return {
            "ok": True,
            "registered": registered,
            "user": user,
            "users": users,
            "groups": groups,
            "offline_messages": offline,
            "agent_online": self.agent.enabled and self.agent.online,
            "capabilities": {
                "conversation_list": True,
                "history_pagination": True,
                "media_get": True,
                "avatar_profiles": True,
                "structured_mentions": True,
                "mention_offsets_codepoints": True,
                "agent_activity": True,
                "system_status": user.get("role") == "admin",
            },
        }

    async def _presence_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        users = await asyncio.to_thread(self.database.list_users)
        for user in users:
            user["online"] = int(user["id"]) in self.sessions_by_user
        await session.send("presence.list", {"users": users}, request_id=request_id)

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
        members.append(
            {
                "id": AGENT_MEMBER_ID,
                "member_id": AGENT_MEMBER_ID,
                "username": "AI Agent",
                "kind": "agent",
                "group_role": "agent",
                "online": self.agent.enabled and self.agent.online,
                "avatar_version": 0,
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

    async def _message_send(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        message_type = str(payload.get("message_type", "text"))
        content = str(payload.get("content", ""))
        if message_type not in {"text", "image"}:
            raise DatabaseError("消息类型无效")
        if len(content) > MAX_TEXT_LENGTH:
            raise DatabaseError("文本消息过长")
        if scope_type == "group" and await asyncio.to_thread(
            self.database.is_muted, int(scope_id), int(session.user["id"])
        ):
            raise DatabaseError("你已被该群禁言")
        image_path = image_name = image_mime = None
        if message_type == "image":
            image_path, image_name, image_mime = await self._store_image(payload)
        elif not content.strip():
            raise DatabaseError("消息内容不能为空")
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
            str(session.user["username"]),
            message_type,
            normalized_content,
            image_path=image_path,
            image_name=image_name,
            image_mime=image_mime,
            mentions=mentions,
        )
        await asyncio.to_thread(
            self.database.set_read_cursor, int(session.user["id"]), scope_type, scope_id, int(message["id"])
        )
        wire_message = await self._with_image_data(message)
        await self.emit_to_scope(scope_type, scope_id, "message.event", {"message": wire_message})
        await session.send("ack", {"action": "message.send", "message_id": message["id"]}, request_id=request_id)
        if any(item["kind"] == "agent" for item in mentions):
            prompt = normalized_content
            for item in reversed(mentions):
                if item["kind"] == "agent":
                    start = int(item["start"])
                    prompt = prompt[:start] + prompt[start + int(item["length"]):]
            prompt = prompt.strip() or "请总结最近 50 条消息"
            self._track(
                asyncio.create_task(
                    self.agent.invoke(int(session.user["id"]), scope_type, scope_id, prompt)
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
        normalized: list[dict[str, Any]] = []
        previous_end = -1
        for raw in sorted(raw_mentions, key=lambda item: int(item.get("start", -1)) if isinstance(item, dict) else -1):
            if not isinstance(raw, dict):
                raise DatabaseError("提及信息无效")
            member_id = str(raw.get("member_id", ""))
            start = int(raw.get("start", -1))
            length = int(raw.get("length", 0))
            if member_id == AGENT_MEMBER_ID:
                kind, display = "agent", "AI Agent"
            elif member_id in allowed:
                kind, display = "user", str(allowed[member_id]["username"])
            else:
                raise DatabaseError("被提及成员不属于当前群聊")
            token = f"@{display}"
            if start < 0 or length != len(token) or start < previous_end or content[start:start + length] != token:
                raise DatabaseError("提及位置与消息文本不一致")
            previous_end = start + length
            normalized.append(
                {
                    "member_id": member_id,
                    "kind": kind,
                    "display": display,
                    "start": start,
                    "length": length,
                }
            )
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
        if message.get("message_type") != "image" or message.get("recalled"):
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
        messages = await asyncio.to_thread(
            self.database.search_messages,
            int(session.user["id"]),
            scope_type,
            scope_id,
            str(payload.get("keyword", "")),
            int(payload.get("limit", 50)),
        )
        wire = [await self._with_image_data(message) for message in messages]
        await session.send("history.result", {"conversation_type": scope_type, "conversation_id": scope_id, "messages": wire, "search": True}, request_id=request_id)

    async def _history_read(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        await asyncio.to_thread(
            self.database.set_read_cursor,
            int(session.user["id"]),
            scope_type,
            scope_id,
            int(payload["message_id"]),
        )
        await session.send("ack", {"action": "history.read"}, request_id=request_id)

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
        if scope_type == "group" and await asyncio.to_thread(
            self.database.is_muted, int(scope_id), int(session.user["id"])
        ):
            raise DatabaseError("你已被该群禁言")
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise DatabaseError("Agent 指令不能为空")
        self._track(asyncio.create_task(self.agent.invoke(int(session.user["id"]), scope_type, scope_id, prompt)))
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

