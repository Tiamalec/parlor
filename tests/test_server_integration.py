from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from server.app import ChatServer
from server.config import Config
from shared.protocol import encode_frame, envelope, read_frame


class WireClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.backlog: list[dict[str, Any]] = []

    @classmethod
    async def connect(cls, port: int) -> "WireClient":
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        return cls(reader, writer)

    async def send(self, message_type: str, payload: dict[str, Any] | None = None, request_id: str | None = None) -> str:
        message = envelope(message_type, payload or {}, request_id=request_id)
        self.writer.write(encode_frame(message))
        await self.writer.drain()
        return message["request_id"]

    async def until(self, message_type: str, *, request_id: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
        for index, message in enumerate(self.backlog):
            if message["type"] == message_type and (request_id is None or message["request_id"] == request_id):
                return self.backlog.pop(index)

        async def receive() -> dict[str, Any]:
            while True:
                message = await read_frame(self.reader)
                if message["type"] == message_type and (request_id is None or message["request_id"] == request_id):
                    return message
                self.backlog.append(message)

        return await asyncio.wait_for(receive(), timeout)

    async def close(self) -> None:
        self.writer.close()
        await self.writer.wait_closed()


class ServerIntegrationTests(unittest.TestCase):
    def test_private_chat_v2_protocol(self) -> None:
        asyncio.run(self._private_chat_v2_scenario())

    async def _private_chat_v2_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(Config.from_env(), host="127.0.0.1", port=0, data_dir=root,
                             database_path=root / "chat.sqlite3", uploads_dir=root / "uploads",
                             admin_username=None, admin_password=None, agent_base_url="http://127.0.0.1:9/v1")
            server = ChatServer(config)
            await server.start()
            port = int(server._server.sockets[0].getsockname()[1])
            alice, bob = await asyncio.gather(WireClient.connect(port), WireClient.connect(port))
            try:
                identities = {}
                for client, name in ((alice, "alice"), (bob, "bob01")):
                    request = await client.send("auth.register", {"username": name, "password": "password-123"})
                    result = (await client.until("auth.result", request_id=request))["payload"]
                    self.assertTrue(result["capabilities"]["private_chat_v2"])
                    identities[name] = result["user"]["id"]
                alice_id, bob_id = identities["alice"], identities["bob01"]
                scope_id = f"dm:{min(alice_id, bob_id)}:{max(alice_id, bob_id)}"
                scope = {"conversation_type": "private", "conversation_id": scope_id}

                request = await alice.send("group.create", {"name": "read-boundary"})
                group_id = (await alice.until("group.created", request_id=request))["payload"]["group"]["id"]
                request = await alice.send("message.send", {"conversation_type": "group", "conversation_id": str(group_id), "content": "group only"})
                await alice.until("ack", request_id=request)
                group_message = (await alice.until("message.event"))["payload"]["message"]

                request = await alice.send("private.open", {"target_user_id": bob_id})
                opened = (await alice.until("private.opened", request_id=request))["payload"]["conversation"]
                self.assertEqual(opened["conversation_id"], scope_id)
                self.assertIsNone(opened["last_message"])
                self.assertTrue(opened["can_send"])
                request = await bob.send("conversation.list")
                self.assertFalse(any(item["conversation_id"] == scope_id for item in
                                     (await bob.until("conversation.list", request_id=request))["payload"]["conversations"]))
                server.database.initialize()
                request = await alice.send("conversation.list", {"limit": 1})
                ordered = (await alice.until("conversation.list", request_id=request))["payload"]["conversations"]
                self.assertEqual(len(ordered), 1)
                self.assertEqual(ordered[0]["conversation_id"], scope_id)
                self.assertEqual(ordered[0]["last_message_at"], 0)

                request = await alice.send("private.typing", {"conversation_id": scope_id, "active": True})
                typing = (await bob.until("private.typing"))["payload"]
                self.assertEqual((typing["user_id"], typing["active"]), (alice_id, True))
                await alice.until("ack", request_id=request)
                request = await alice.send("message.send", {**scope, "content": "hello"}, request_id="dm-first")
                first = (await bob.until("message.event"))["payload"]["message"]
                await alice.until("ack", request_id=request)
                await alice.until("message.event")
                request = await bob.send("message.send", {**scope, "content": "reply"}, request_id="dm-reply")
                second = (await alice.until("message.event"))["payload"]["message"]
                await bob.until("ack", request_id=request)
                await bob.until("message.event")
                request = await alice.send("message.send", {**scope, "content": "another"})
                await alice.until("ack", request_id=request)
                await alice.until("message.event")
                await bob.until("message.event")
                request = await alice.send("conversation.list")
                alice_list = (await alice.until("conversation.list", request_id=request))["payload"]["conversations"]
                private = next(item for item in alice_list if item["conversation_id"] == scope_id)
                self.assertEqual(private["unread_count"], 1)
                self.assertEqual(private["peer_last_read_message_id"], 0)
                request = await bob.send("conversation.list")
                bob_list = (await bob.until("conversation.list", request_id=request))["payload"]["conversations"]
                self.assertEqual(next(item for item in bob_list if item["conversation_id"] == scope_id)["unread_count"], 2)

                request = await alice.send("history.read", {**scope, "message_id": 999999})
                await alice.until("error", request_id=request)
                request = await alice.send("history.read", {**scope, "message_id": group_message["id"]})
                await alice.until("error", request_id=request)
                request = await alice.send("history.read", {**scope, "message_id": second["id"]})
                await alice.until("ack", request_id=request)
                read = (await bob.until("private.read"))["payload"]
                self.assertEqual((read["reader_id"], read["message_id"]), (alice_id, second["id"]))
                await alice.until("private.read")
                request = await alice.send("history.read", {**scope, "message_id": first["id"]})
                await alice.until("ack", request_id=request)
                self.assertFalse(any(item["type"] == "private.read" for item in alice.backlog))

                request = await alice.send("message.status", {"client_request_id": "dm-first"})
                status = (await alice.until("message.status", request_id=request))["payload"]
                self.assertEqual(status["message_id"], first["id"])
                request = await bob.send("message.status", {"client_request_id": "dm-first"})
                self.assertFalse((await bob.until("message.status", request_id=request))["payload"]["found"])
                reconnected = await WireClient.connect(port)
                try:
                    request = await reconnected.send("auth.login", {"username": "alice", "password": "password-123", "after_event_id": 0})
                    await reconnected.until("auth.result", request_id=request)
                    request = await reconnected.send("message.status", {"client_request_id": "dm-first"})
                    self.assertEqual((await reconnected.until("message.status", request_id=request))["payload"]["message_id"], first["id"])
                    request = await reconnected.send("message.send", {**scope, "content": "hello"}, request_id="dm-first")
                    self.assertTrue((await reconnected.until("ack", request_id=request))["payload"]["duplicate"])
                finally:
                    await reconnected.close()

                request = await alice.send("private.block.set", {"target_user_id": bob_id, "blocked": True})
                await alice.until("ack", request_id=request)
                self.assertEqual((await alice.until("private.state"))["payload"]["blocked_by_me"], True)
                peer_state = (await bob.until("private.state"))["payload"]
                self.assertFalse(peer_state["can_send"])
                self.assertFalse(peer_state["blocked_by_me"])
                for client in (alice, bob):
                    request = await client.send("message.send", {**scope, "content": "blocked"})
                    self.assertEqual((await client.until("error", request_id=request))["payload"]["message"], "当前私聊不可发送")
                with self.assertRaisesRegex(ValueError, "当前私聊不可发送"):
                    server.database.save_message("private", scope_id, None, "Agent", "agent", "blocked reply", sender_kind="agent")
                request = await bob.send("history.sync", scope)
                history = (await bob.until("history.result", request_id=request))["payload"]["messages"]
                self.assertEqual([item["content"] for item in history], ["hello", "reply", "another"])
                request = await alice.send("private.block.set", {"target_user_id": bob_id, "blocked": False})
                await alice.until("ack", request_id=request)
                self.assertTrue((await alice.until("private.state"))["payload"]["can_send"])
                self.assertTrue((await bob.until("private.state"))["payload"]["can_send"])
                server.database.save_message("private", scope_id, None, "Agent", "agent", "agent reply", sender_kind="agent")
                request = await alice.send("conversation.list")
                conversations = (await alice.until("conversation.list", request_id=request))["payload"]["conversations"]
                self.assertEqual(next(item for item in conversations if item["conversation_id"] == scope_id)["unread_count"], 1)
                request = await bob.send("conversation.list")
                conversations = (await bob.until("conversation.list", request_id=request))["payload"]["conversations"]
                self.assertEqual(next(item for item in conversations if item["conversation_id"] == scope_id)["unread_count"], 3)
            finally:
                await alice.close()
                await bob.close()
                await server.close()

    def test_identity_and_group_nickname_protocol(self) -> None:
        asyncio.run(self._identity_scenario())

    async def _identity_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(Config.from_env(), host="127.0.0.1", port=0, data_dir=root,
                             database_path=root / "chat.sqlite3", uploads_dir=root / "uploads",
                             admin_username=None, admin_password=None, agent_base_url="http://127.0.0.1:9/v1")
            server = ChatServer(config)
            await server.start()
            port = int(server._server.sockets[0].getsockname()[1])
            alice, bob = await asyncio.gather(WireClient.connect(port), WireClient.connect(port))
            try:
                for client, name in ((alice, "alice"), (bob, "bob01")):
                    request = await client.send("auth.register", {"username": name, "password": "password-123"})
                    await client.until("auth.result", request_id=request)
                request = await alice.send("group.create", {"name": "研发组"})
                group_id = (await alice.until("group.created", request_id=request))["payload"]["group"]["id"]
                request = await bob.send("group.join", {"group_id": group_id})
                await bob.until("ack", request_id=request)
                request = await bob.send("profile.identity.set", {"username": "bob_new", "display_name": "小博"})
                await bob.until("ack", request_id=request)
                self.assertEqual((await alice.until("profile.updated"))["payload"]["user"]["username"], "bob_new")
                request = await bob.send("group.nickname.set", {"group_id": group_id, "nickname": "研发小博"})
                await bob.until("ack", request_id=request)
                self.assertEqual((await alice.until("group.nickname.updated"))["payload"]["nickname"], "研发小博")
                request = await bob.send("message.send", {"conversation_type": "group", "conversation_id": str(group_id), "content": "你好"})
                self.assertEqual((await alice.until("message.event"))["payload"]["message"]["sender_name"], "研发小博")
                await bob.until("ack", request_id=request)
                request = await alice.send("group.members", {"group_id": group_id})
                members = (await alice.until("group.members", request_id=request))["payload"]["members"]
                self.assertEqual(next(member for member in members if member.get("username") == "bob_new")["nickname"], "研发小博")
            finally:
                await alice.close()
                await bob.close()
                await server.close()

    def test_message_mutations_search_and_reconnect(self) -> None:
        asyncio.run(self._message_scenario())

    async def _message_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(Config.from_env(), host="127.0.0.1", port=0, data_dir=root,
                             database_path=root / "chat.sqlite3", uploads_dir=root / "uploads",
                             admin_username=None, admin_password=None, agent_base_url="http://127.0.0.1:9/v1")
            server = ChatServer(config)
            await server.start()
            port = int(server._server.sockets[0].getsockname()[1])
            alice, bob = await asyncio.gather(WireClient.connect(port), WireClient.connect(port))
            try:
                for client, name in ((alice, "alice"), (bob, "bob01")):
                    request = await client.send("auth.register", {"username": name, "password": "password-123"})
                    await client.until("auth.result", request_id=request)
                request = await alice.send("group.create", {"name": "M1 群"})
                group = (await alice.until("group.created", request_id=request))["payload"]["group"]
                scope = {"conversation_type": "group", "conversation_id": str(group["id"])}
                request = await bob.send("group.join", {"group_id": group["id"]})
                await bob.until("ack", request_id=request)
                request = await alice.send("message.send", {**scope, "content": "first needle"}, request_id="stable-send")
                first = (await bob.until("message.event"))["payload"]["message"]
                await alice.until("message.event")
                await alice.until("ack", request_id=request)
                duplicate = await alice.send("message.send", {**scope, "content": "first needle"}, request_id="stable-send")
                # The same connection reports a duplicate without mutating the timeline.
                self.assertTrue((await alice.until("ack", request_id=duplicate))["payload"]["duplicate"])
                request = await bob.send("message.send", {**scope, "content": "reply needle", "reply_to_id": first["id"]})
                reply = (await alice.until("message.event"))["payload"]["message"]
                await bob.until("ack", request_id=request)
                self.assertEqual(reply["reply_to_id"], first["id"])
                request = await bob.send("message.edit", {"message_id": first["id"], "content": "forged"})
                await bob.until("error", request_id=request)
                request = await bob.send("history.read", {**scope, "message_id": reply["id"]})
                await bob.until("ack", request_id=request)
                cursor = reply["event_id"]
                await bob.close()
                request = await alice.send("message.edit", {"message_id": first["id"], "content": "edited needle"})
                edited = (await alice.until("message.edited"))["payload"]["message"]
                await alice.until("ack", request_id=request)
                self.assertEqual(edited["id"], first["id"])
                request = await alice.send("message.recall", {"message_id": first["id"]})
                await alice.until("message.recalled")
                await alice.until("ack", request_id=request)
                bob = await WireClient.connect(port)
                request = await bob.send("auth.login", {"username": "bob01", "password": "password-123", "after_event_id": cursor})
                auth = (await bob.until("auth.result", request_id=request))["payload"]
                self.assertEqual([item["event_type"] for item in auth["offline_events"]], ["message.edited", "message.recalled"])
                self.assertEqual(auth["offline_messages"], [])
                request = await bob.send("history.search", {**scope, "keyword": "needle", "limit": 1})
                search = (await bob.until("history.result", request_id=request))["payload"]
                self.assertEqual(search["messages"][0]["id"], reply["id"])
                self.assertFalse(search["has_more"])
                request = await bob.send("message.send", {**scope, "content": "bad", "reply_to_id": 999999})
                await bob.until("error", request_id=request)
                png = base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
                request = await alice.send("message.send", {**scope, "message_type": "image",
                                                "image_name": "photo.png", "image_mime": "image/png",
                                                "image_data": base64.b64encode(png).decode("ascii")})
                image = (await bob.until("message.event"))["payload"]["message"]
                await alice.until("ack", request_id=request)
                request = await bob.send("history.sync", {**scope, "limit": 10, "media_mode": "metadata"})
                history = (await bob.until("history.result", request_id=request))["payload"]["messages"]
                image_meta = next(item for item in history if item["id"] == image["id"])
                self.assertNotIn("image_data", image_meta)
                request = await bob.send("media.get", {"message_id": image["id"]})
                loaded = (await bob.until("media.result", request_id=request))["payload"]["message"]
                self.assertEqual(base64.b64decode(loaded["image_data"]), png)
                request = await alice.send(
                    "message.send",
                    {
                        **scope,
                        "message_type": "sticker",
                        "sticker_id": "local-happy",
                        "sticker_name": "开心",
                        "image_name": "happy.png",
                        "image_mime": "image/png",
                        "image_data": base64.b64encode(png).decode("ascii"),
                    },
                )
                sticker = (await bob.until("message.event"))["payload"]["message"]
                await alice.until("ack", request_id=request)
                self.assertEqual(sticker["message_type"], "sticker")
                self.assertEqual(sticker["sticker"]["name"], "开心")
                self.assertEqual(base64.b64decode(sticker["image_data"]), png)
            finally:
                await alice.close()
                await bob.close()
                await server.close()

    def test_three_clients_chat_disconnect_and_agent_failure_isolation(self) -> None:
        asyncio.run(self._scenario())

    def test_agent_management_memory_and_tool_protocol(self) -> None:
        asyncio.run(self._agent_management_scenario())

    async def _agent_management_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = replace(
                Config.from_env(),
                host="127.0.0.1",
                port=0,
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                admin_username=None,
                admin_password=None,
                agent_base_url="http://127.0.0.1:9/v1",
                agent_timeout_seconds=1,
            )
            server = ChatServer(config)
            await server.start()
            assert server._server and server._server.sockets
            port = int(server._server.sockets[0].getsockname()[1])
            alice, bob = await asyncio.gather(WireClient.connect(port), WireClient.connect(port))
            try:
                users: dict[str, dict[str, Any]] = {}
                for client, name in ((alice, "alice"), (bob, "bob01")):
                    request = await client.send(
                        "auth.register", {"username": name, "password": "password-123"}
                    )
                    users[name] = (await client.until("auth.result", request_id=request))["payload"]["user"]
                request = await alice.send("group.create", {"name": "Agent 协议组"})
                group_id = (await alice.until("group.created", request_id=request))["payload"]["group"]["id"]
                request = await bob.send("group.join", {"group_id": group_id})
                await bob.until("ack", request_id=request)

                request = await alice.send(
                    "agent.create",
                    {
                        "name": "协议助手",
                        "description": "协议测试",
                        "card": {
                            "personality": "严谨",
                            "background": "协议测试",
                            "language_style": "短句",
                            "behavior_rules": "不读取未授权会话",
                        },
                        "memory_enabled": True,
                    },
                )
                created = (await alice.until("agent.created", request_id=request))["payload"]["agent"]
                agent_id = created["id"]
                self.assertEqual(created["card"]["personality"], "严谨")
                request = await alice.send("agent.list")
                listed = (await alice.until("agent.list", request_id=request))["payload"]
                self.assertIn(agent_id, {item["id"] for item in listed["agents"]})
                self.assertTrue(listed["models"])
                request = await alice.send("agent.avatar.get", {"agent_id": "agent_taffy"})
                preset_avatar = (await alice.until("agent.avatar.result", request_id=request))["payload"]
                self.assertEqual(preset_avatar["avatar_version"], 1)
                self.assertEqual(preset_avatar["avatar_mime"], "image/png")
                self.assertTrue(
                    base64.b64decode(preset_avatar["image_data"]).startswith(b"\x89PNG\r\n\x1a\n")
                )

                request = await bob.send("agent.update", {"agent_id": agent_id, "name": "劫持"})
                await bob.until("error", request_id=request)
                request = await bob.send(
                    "agent.group.set", {"group_id": group_id, "agent_id": agent_id, "enabled": True}
                )
                await bob.until("error", request_id=request)
                request = await alice.send(
                    "agent.group.set", {"group_id": group_id, "agent_id": agent_id, "enabled": True}
                )
                await alice.until("agent.group.updated", request_id=request)
                request = await alice.send("group.members", {"group_id": group_id})
                members = (await alice.until("group.members", request_id=request))["payload"]["members"]
                self.assertIn(agent_id, {item["id"] for item in members if item["kind"] == "agent"})

                request = await bob.send(
                    "agent.create",
                    {"name": "越权角色", "group_id": group_id},
                )
                await bob.until("error", request_id=request)
                request = await alice.send(
                    "agent.create",
                    {
                        "name": "群内预设角色",
                        "description": "创建后直接加入当前群",
                        "group_id": group_id,
                    },
                )
                created_in_group = (
                    await alice.until("agent.created", request_id=request)
                )["payload"]
                self.assertIn(
                    created_in_group["agent"]["id"],
                    {item["id"] for item in created_in_group["group_agents"]},
                )

                mention_text = "@协议助手 你好"
                request = await alice.send(
                    "message.send",
                    {
                        "conversation_type": "group",
                        "conversation_id": group_id,
                        "message_type": "text",
                        "content": mention_text,
                        "mentions": [
                            {
                                "member_id": f"agent:{agent_id}",
                                "display": "协议助手",
                                "start": 0,
                                "length": 5,
                            }
                        ],
                    },
                )
                trigger = (await alice.until("message.event"))["payload"]["message"]
                self.assertEqual(trigger["mentions"][0]["agent_id"], agent_id)
                await alice.until("ack", request_id=request)
                await bob.until("message.event")
                result = await alice.until("agent.result", timeout=4.0)
                while result["payload"].get("status") != "failed":
                    result = await alice.until("agent.result", timeout=4.0)
                self.assertEqual(result["payload"]["agent_id"], agent_id)
                self.assertFalse(result["payload"]["success"])

                private_id = f"dm:{min(users['alice']['id'], users['bob01']['id'])}:{max(users['alice']['id'], users['bob01']['id'])}"
                private_scope = {
                    "conversation_type": "private",
                    "conversation_id": private_id,
                    "agent_id": agent_id,
                }
                request = await alice.send("memory.settings.set", {**private_scope, "enabled": True})
                await alice.until("memory.settings.updated", request_id=request)
                request = await alice.send(
                    "memory.add", {**private_scope, "content": "发布代号是青竹", "kind": "fact"}
                )
                memory = (await alice.until("memory.saved", request_id=request))["payload"]["memory"]
                request = await alice.send("memory.list", private_scope)
                listed_memories = (await alice.until("memory.list", request_id=request))["payload"]["memories"]
                self.assertEqual([item["id"] for item in listed_memories], [memory["id"]])
                request = await bob.send("memory.list", private_scope)
                bob_memories = (await bob.until("memory.list", request_id=request))["payload"]["memories"]
                self.assertEqual(bob_memories, [])
                request = await bob.send("memory.delete", {"memory_id": memory["id"]})
                await bob.until("error", request_id=request)

                group_scope = {
                    "conversation_type": "group",
                    "conversation_id": str(group_id),
                    "agent_id": agent_id,
                }
                request = await alice.send("memory.settings.set", {**group_scope, "enabled": True})
                await alice.until("memory.settings.updated", request_id=request)
                request = await alice.send(
                    "memory.add", {**group_scope, "content": "周五发布", "kind": "decision"}
                )
                group_memory = (await alice.until("memory.saved", request_id=request))["payload"]["memory"]
                request = await bob.send("memory.list", group_scope)
                shared = (await bob.until("memory.list", request_id=request))["payload"]["memories"]
                self.assertEqual([item["id"] for item in shared], [group_memory["id"]])
                request = await bob.send("memory.delete", {"memory_id": group_memory["id"]})
                await bob.until("error", request_id=request)

                request = await bob.send(
                    "agent.tool.set", {"agent_id": agent_id, "tools": ["save_agent_result"]}
                )
                await bob.until("error", request_id=request)
                request = await alice.send(
                    "agent.tool.set", {"agent_id": agent_id, "tools": ["save_agent_result"]}
                )
                tools = (await alice.until("agent.tools.updated", request_id=request))["payload"]["tools"]
                self.assertEqual(
                    [item["name"] for item in tools if item["enabled"]], ["save_agent_result"]
                )
                request = await alice.send(
                    "agent.update", {"agent_id": agent_id, "model_profile_id": "missing-profile"}
                )
                await alice.until("error", request_id=request)
                request = await alice.send("agent.delete", {"agent_id": "agent"})
                await alice.until("error", request_id=request)
                request = await alice.send("agent.delete", {"agent_id": agent_id})
                await alice.until("ack", request_id=request)
            finally:
                await alice.close()
                await bob.close()
                await server.close()

    async def _scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = Config.from_env()
            config = replace(
                base,
                host="127.0.0.1",
                port=0,
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                admin_username=None,
                admin_password=None,
                agent_base_url="http://127.0.0.1:9/v1",
                agent_timeout_seconds=1,
            )
            server = ChatServer(config)
            await server.start()
            assert server._server and server._server.sockets
            port = int(server._server.sockets[0].getsockname()[1])
            alice, bob, carol = await asyncio.gather(*(WireClient.connect(port) for _ in range(3)))
            try:
                users: dict[str, dict[str, Any]] = {}
                for client, username in ((alice, "alice"), (bob, "bob01"), (carol, "carol")):
                    request_id = await client.send("auth.register", {"username": username, "password": "password-123"})
                    result = await client.until("auth.result", request_id=request_id)
                    self.assertTrue(result["payload"]["ok"])
                    self.assertTrue(result["payload"]["capabilities"]["conversation_list"])
                    users[username] = result["payload"]["user"]

                png = base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
                avatar_id = await alice.send(
                    "profile.avatar.set",
                    {"image_mime": "image/png", "image_data": base64.b64encode(png).decode("ascii")},
                )
                await alice.until("ack", request_id=avatar_id)
                profile = await bob.until("profile.updated")
                self.assertEqual(profile["payload"]["user"]["avatar_version"], 1)
                get_avatar_id = await bob.send("profile.avatar.get", {"user_id": users["alice"]["id"]})
                avatar = await bob.until("profile.avatar.result", request_id=get_avatar_id)
                self.assertEqual(base64.b64decode(avatar["payload"]["image_data"]), png)
                oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (5000).to_bytes(4, "big") + (5000).to_bytes(4, "big")
                oversized_id = await alice.send(
                    "profile.avatar.set",
                    {"image_mime": "image/png", "image_data": base64.b64encode(oversized).decode("ascii")},
                )
                oversized_error = await alice.until("error", request_id=oversized_id)
                self.assertIn("格式不匹配", oversized_error["payload"]["message"])

                create_id = await alice.send("group.create", {"name": "网络实验组"})
                created = await alice.until("group.created", request_id=create_id)
                group_id = created["payload"]["group"]["id"]
                for client in (bob, carol):
                    join_id = await client.send("group.join", {"group_id": group_id})
                    await client.until("ack", request_id=join_id)

                members_id = await alice.send("group.members", {"group_id": group_id})
                members = await alice.until("group.members", request_id=members_id)
                self.assertEqual(next(item for item in members["payload"]["members"] if item["kind"] == "agent")["member_id"], "agent")
                self.assertTrue(members["payload"]["permissions"]["can_manage_members"])
                self.assertFalse(members["payload"]["schedule"]["enabled"])
                bob_member = next(
                    item for item in members["payload"]["members"] if item.get("id") == users["bob01"]["id"]
                )
                self.assertEqual(bob_member["available_actions"], ["mute"])

                mention_text = "@bob01 请确认"
                mention_id = await alice.send(
                    "message.send",
                    {
                        "conversation_type": "group",
                        "conversation_id": group_id,
                        "message_type": "text",
                        "content": mention_text,
                        "mentions": [
                            {"member_id": f"user:{users['bob01']['id']}", "start": 0, "length": 6}
                        ],
                    },
                )
                mentioned_events = await asyncio.gather(
                    alice.until("message.event"), bob.until("message.event"), carol.until("message.event")
                )
                self.assertEqual(mentioned_events[1]["payload"]["message"]["mentions"][0]["member_id"], f"user:{users['bob01']['id']}")
                await alice.until("ack", request_id=mention_id)
                self.assertEqual(server.database.list_agent_activity(users["alice"]["id"]), [])

                utf16_text = "  😀 @bob01 请确认"
                utf16_id = await alice.send(
                    "message.send",
                    {
                        "conversation_type": "group",
                        "conversation_id": group_id,
                        "message_type": "text",
                        "content": utf16_text,
                        "mention_index_unit": "utf16",
                        "mentions": [
                            {"member_id": f"user:{users['bob01']['id']}", "start": 5, "length": 6}
                        ],
                    },
                )
                utf16_events = await asyncio.gather(
                    alice.until("message.event"), bob.until("message.event"), carol.until("message.event")
                )
                self.assertEqual(utf16_events[0]["payload"]["message"]["content"], "😀 @bob01 请确认")
                self.assertEqual(utf16_events[0]["payload"]["message"]["mentions"][0]["start"], 2)
                await alice.until("ack", request_id=utf16_id)

                invalid_id = await alice.send(
                    "message.send",
                    {
                        "conversation_type": "group",
                        "conversation_id": group_id,
                        "message_type": "text",
                        "content": "@ghost hi",
                        "mentions": [{"member_id": "user:99999", "start": 0, "length": 6}],
                    },
                )
                invalid = await alice.until("error", request_id=invalid_id)
                self.assertIn("不属于", invalid["payload"]["message"])

                send_id = await bob.send(
                    "message.send",
                    {"conversation_type": "group", "conversation_id": group_id, "message_type": "text", "content": "三客户端群聊测试"},
                )
                event_a, event_c = await asyncio.gather(alice.until("message.event"), carol.until("message.event"))
                self.assertEqual(event_a["payload"]["message"]["content"], "三客户端群聊测试")
                self.assertEqual(event_c["payload"]["message"]["content"], "三客户端群聊测试")
                await bob.until("ack", request_id=send_id)
                own_group_event = await bob.until("message.event")
                self.assertEqual(own_group_event["payload"]["message"]["conversation_type"], "group")

                conversations_id = await alice.send("conversation.list")
                conversations = await alice.until("conversation.list", request_id=conversations_id)
                projected = next(
                    item
                    for item in conversations["payload"]["conversations"]
                    if item["conversation_type"] == "group" and str(item["conversation_id"]) == str(group_id)
                )
                self.assertEqual(projected["last_message_preview"], "三客户端群聊测试")

                status_id = await alice.send("system.status")
                denied = await alice.until("error", request_id=status_id)
                self.assertIn("管理员", denied["payload"]["message"])

                private_id = await alice.send(
                    "message.send",
                    {"conversation_type": "private", "conversation_id": users["bob01"]["id"], "message_type": "text", "content": "私聊测试"},
                )
                private_event = await bob.until("message.event")
                self.assertEqual(private_event["payload"]["message"]["conversation_type"], "private")
                await alice.until("ack", request_id=private_id)
                own_private_event = await alice.until("message.event")
                self.assertEqual(own_private_event["payload"]["message"]["content"], "私聊测试")

                await carol.close()
                offline = await alice.until("presence.changed")
                while offline["payload"].get("user", {}).get("id") != users["carol"]["id"] or offline["payload"].get("online"):
                    offline = await alice.until("presence.changed")
                self.assertFalse(offline["payload"]["online"])

                agent_id = await alice.send(
                    "message.send",
                    {
                        "conversation_type": "group",
                        "conversation_id": group_id,
                        "message_type": "text",
                        "content": "@AI Agent 总结最近20条消息",
                        "mentions": [{"member_id": "agent", "start": 0, "length": 9}],
                    },
                )
                await alice.until("ack", request_id=agent_id)
                await asyncio.gather(alice.until("message.event"), bob.until("message.event"))

                normal_id = await bob.send(
                    "message.send",
                    {"conversation_type": "group", "conversation_id": group_id, "message_type": "text", "content": "模型离线时聊天仍可用"},
                )
                normal_event = await alice.until("message.event")
                self.assertEqual(normal_event["payload"]["message"]["content"], "模型离线时聊天仍可用")
                await bob.until("ack", request_id=normal_id)
                failed = await alice.until("agent.result", timeout=4.0)
                while failed["payload"].get("status") != "failed":
                    failed = await alice.until("agent.result", timeout=4.0)
                self.assertFalse(failed["payload"]["success"])
                activity_id = await alice.send("agent.activity.list")
                activity = await alice.until("agent.activity.list", request_id=activity_id)
                self.assertEqual(activity["payload"]["activities"][0]["status"], "failed")

                logout_id = await bob.send("auth.logout")
                await bob.until("ack", request_id=logout_id)
                bob_offline = await alice.until("presence.changed")
                while bob_offline["payload"].get("user", {}).get("id") != users["bob01"]["id"]:
                    bob_offline = await alice.until("presence.changed")
                self.assertFalse(bob_offline["payload"]["online"])
            finally:
                await alice.close()
                await bob.close()
                await server.close()


if __name__ == "__main__":
    unittest.main()

