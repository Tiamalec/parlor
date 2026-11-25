"""Async TCP chat server: lifecycle, dispatch and broadcasting."""

from __future__ import annotations

from .agent import AgentService
from typing import Any
from .session import ClientSession
from .config import Config
from .database import Database, DatabaseError, dm_participants
from shared.protocol import ProtocolError, read_frame
from .admin.config_store import SearchConfigStore
from .todos import TodoService
import asyncio
import binascii
import logging
import time

from .handlers.agents import AgentHandlers
from .handlers.auth import AuthHandlers
from .handlers.groups import GroupHandlers
from .handlers.messages import MessageHandlers
from .handlers.profile import ProfileHandlers
from .handlers.todos import TodoHandlers


LOGGER = logging.getLogger("chatroom.server")


class ChatServer(
    AgentHandlers,
    AuthHandlers,
    GroupHandlers,
    MessageHandlers,
    ProfileHandlers,
    TodoHandlers,
):

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
        self.agent.apply_search_settings(SearchConfigStore(config.data_dir).load_search_settings())
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

    async def _notify_todo_change(
        self, owner_id: int, event_type: str, payload: dict[str, Any]
    ) -> None:
        await self._send_to_users({owner_id}, event_type, payload)

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
