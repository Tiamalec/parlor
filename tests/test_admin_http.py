from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from server.admin.auth import AdminAuthError, AdminSessionStore
from server.admin.config_store import ProviderConfigStore
from server.admin.http import AdminHttpServer, Request
from server.admin.service import AdminService
from server.agent import AgentService
from server.config import Config
from server.database import Database


class FakeChatServer:
    def admin_status_snapshot(self) -> dict[str, Any]:
        return {
            "server_version": "test",
            "tcp_listening": True,
            "tcp_addresses": [],
            "uptime_seconds": 1,
            "connection_count": 0,
            "authenticated_connections": 0,
            "online_users": 0,
        }

    def online_user_ids(self) -> set[int]:
        return set()

    async def publish_agent_status(self) -> None:
        return None


class AdminHttpTests(unittest.TestCase):
    def test_routes_require_cookie_and_csrf(self) -> None:
        asyncio.run(self._scenario())

    async def _scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            database.register_user("admin01", "password-admin", role="admin")
            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                admin_port=0,
            )

            async def emit(*_args: object) -> None:
                return None

            agent = AgentService(config, database, emit)
            service = AdminService(
                database,
                agent,
                FakeChatServer(),
                ProviderConfigStore(root),
                AdminSessionStore(600),
                100,
            )
            http = AdminHttpServer(config, service)
            try:
                login = Request(
                    "POST",
                    "/api/v1/admin/session",
                    {},
                    {"content-type": "application/json"},
                    json.dumps(
                        {"username": "admin01", "password": "password-admin"}
                    ).encode(),
                    "127.0.0.1",
                )
                response = await http._route(login)
                self.assertEqual(response.status, 200)
                body = json.loads(response.body)
                csrf = body["csrf_token"]
                cookie_header = response.headers["Set-Cookie"]  # type: ignore[index]
                cookie = cookie_header.split(";", 1)[0]

                dashboard = await http._route(
                    Request(
                        "GET",
                        "/api/v1/admin/dashboard",
                        {},
                        {"cookie": cookie},
                        b"",
                        "127.0.0.1",
                    )
                )
                self.assertTrue(json.loads(dashboard.body)["tcp_listening"])
                presets = await http._route(
                    Request("GET", "/api/v1/admin/model-presets", {}, {"cookie": cookie}, b"", "127.0.0.1")
                )
                self.assertTrue(any(item["id"] == "gemini" for item in json.loads(presets.body)["presets"]))
                search_view = await http._route(Request(
                    "GET", "/api/v1/admin/search-provider", {}, {"cookie": cookie}, b"", "127.0.0.1"
                ))
                self.assertFalse(json.loads(search_view.body)["enabled"])
                with self.assertRaises(AdminAuthError):
                    await http._route(Request(
                        "POST", "/api/v1/admin/search-provider", {}, {"cookie": cookie},
                        b'{"provider":"brave","api_key":"secret","enabled":true}', "127.0.0.1"
                    ))
                with patch("server.admin.service.search", new_callable=AsyncMock, return_value=[
                    {"title": "Example", "url": "https://example.org", "snippet": "ok"}
                ]):
                    search_saved = await http._route(Request(
                        "POST", "/api/v1/admin/search-provider", {},
                        {"cookie": cookie, "x-csrf-token": csrf},
                        b'{"provider":"brave","api_key":"secret","enabled":true}', "127.0.0.1"
                    ))
                self.assertTrue(json.loads(search_saved.body)["enabled"])
                self.assertNotIn("secret", search_saved.body.decode())
                profile_payload = json.dumps(
                    {
                        "name": "测试 OpenAI",
                        "preset_id": "openai",
                        "base_url": "https://api.openai.com/v1",
                        "model": "test-model",
                        "api_key": "http-route-secret",
                    }
                ).encode()
                created_response = await http._route(
                    Request(
                        "POST",
                        "/api/v1/admin/model-profiles",
                        {},
                        {"cookie": cookie, "x-csrf-token": csrf},
                        profile_payload,
                        "127.0.0.1",
                    )
                )
                self.assertEqual(created_response.status, 201)
                created = json.loads(created_response.body)
                self.assertNotIn("api_key", created)
                agent.probe_provider = AsyncMock(return_value=(True, None))  # type: ignore[method-assign]
                tested = await http._route(
                    Request(
                        "POST",
                        f"/api/v1/admin/model-profiles/{created['id']}/test",
                        {},
                        {"cookie": cookie, "x-csrf-token": csrf},
                        b"{}",
                        "127.0.0.1",
                    )
                )
                self.assertTrue(json.loads(tested.body)["available"])
                with self.assertRaises(AdminAuthError):
                    await http._route(
                        Request(
                            "POST",
                            "/api/v1/admin/agents/enabled",
                            {},
                            {"cookie": cookie},
                            b'{"enabled":false}',
                            "127.0.0.1",
                        )
                    )
                disabled = await http._route(
                    Request(
                        "POST",
                        "/api/v1/admin/agents/enabled",
                        {},
                        {"cookie": cookie, "x-csrf-token": csrf},
                        b'{"enabled":false}',
                        "127.0.0.1",
                    )
                )
                self.assertFalse(json.loads(disabled.body)["enabled"])
                with self.assertRaises(ValueError):
                    await http._route(
                        Request(
                            "POST",
                            "/api/v1/admin/agents/enabled",
                            {},
                            {"cookie": cookie, "x-csrf-token": csrf},
                            b'{"enabled":"false"}',
                            "127.0.0.1",
                        )
                    )
                with database._connect() as conn:
                    conn.execute("UPDATE users SET role='user' WHERE username='admin01'")
                with self.assertRaises(AdminAuthError):
                    await http._route(
                        Request(
                            "GET",
                            "/api/v1/admin/dashboard",
                            {},
                            {"cookie": cookie},
                            b"",
                            "127.0.0.1",
                        )
                    )
            finally:
                await http.close()

    def test_request_parser_rejects_duplicate_headers(self) -> None:
        async def scenario() -> None:
            config = Config.from_env()
            server = object.__new__(AdminHttpServer)
            server.config = config
            reader = asyncio.StreamReader()
            reader.feed_data(
                b"GET / HTTP/1.1\r\nHost: localhost\r\nHost: duplicate\r\n\r\n"
            )
            reader.feed_eof()
            with self.assertRaises(ValueError):
                await server._read_request(reader, "127.0.0.1")

        asyncio.run(scenario())

    def test_admin_listener_rejects_non_loopback_bind(self) -> None:
        config = replace(Config.from_env(), admin_host="0.0.0.0", admin_cookie_secure=True)
        server = object.__new__(AdminHttpServer)
        server.config = config
        with self.assertRaises(RuntimeError):
            server._validate_bind_policy()


if __name__ == "__main__":
    unittest.main()

