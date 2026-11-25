from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from server.admin.auth import AdminSessionStore
from server.admin.config_store import ProviderConfigStore, SearchConfigStore
from server.admin.service import AdminRequestError, AdminService
from server.agent import AgentService
from server.app import ChatServer
from server.config import Config
from server.database import Database
from server.model_provider import ProviderToolCall, ProviderTurn
from server.search_provider import SearchError, SearchSettings, TEST_QUERY, search
from server.tools import DEFAULT_AGENT_TOOL_NAMES


class Response(io.BytesIO):
    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class SearchAdapterTests(unittest.TestCase):
    def test_adapters_normalize_five_results_and_keep_credentials_in_headers(self) -> None:
        cases = [
            ("brave", "https://api.search.brave.com", "web", "description", "X-subscription-token"),
            ("tavily", "https://api.tavily.com", "results", "content", "Authorization"),
            ("deepseek", "https://api.deepseek.com/v1/tools/search", "results", "snippet", "Authorization"),
            ("searxng", "http://127.0.0.1:9999", "results", "content", None),
        ]
        for provider, endpoint, container, field, auth_header in cases:
            with self.subTest(provider=provider):
                rows = [{"title": f"来源 {i}", "url": f"https://example.org/{i}", field: "摘要"} for i in range(7)]
                payload = {container: {"results": rows} if provider == "brave" else rows}
                requests: list[Any] = []

                def open_request(request: Any, *, timeout: int) -> Response:
                    requests.append(request)
                    self.assertEqual(timeout, 8)
                    return Response(json.dumps(payload).encode())

                settings = SearchSettings(provider, endpoint if provider == "searxng" else "", "secret" if auth_header else "", True)
                with patch("server.search_provider.URL_OPENER.open", side_effect=open_request):
                    result = asyncio.run(search(settings, "Python 文档"))
                self.assertEqual(len(result), 5)
                self.assertEqual(result[0]["snippet"], "摘要")
                request = requests[0]
                self.assertIn(endpoint, request.full_url)
                self.assertNotIn("secret", request.full_url)
                self.assertEqual(request.get_method(), "POST" if provider in {"tavily", "deepseek"} else "GET")
                if auth_header:
                    self.assertIn(auth_header, request.headers)
                if provider == "deepseek":
                    self.assertEqual(request.headers["Authorization"], "Bearer secret")
                    self.assertEqual(json.loads(request.data), {
                        "query": "Python 文档", "count": 5, "need_content": False,
                    })

    def test_deepseek_query_limit_is_checked_before_request(self) -> None:
        settings = SearchSettings("deepseek", api_key="secret", enabled=True)
        with patch("server.search_provider.URL_OPENER.open") as opener:
            with self.assertRaises(SearchError) as error:
                asyncio.run(search(settings, "字" * 129))
        self.assertEqual(error.exception.category, "invalid_request")
        opener.assert_not_called()

    def test_search_rejects_bad_response_and_reports_rate_limit(self) -> None:
        settings = SearchSettings("brave", api_key="secret", enabled=True)
        with patch("server.search_provider.URL_OPENER.open", return_value=Response(b'{"web": {}}')):
            with self.assertRaises(SearchError) as response:
                asyncio.run(search(settings, "query"))
        self.assertEqual(response.exception.category, "invalid_response")
        with patch("server.search_provider.URL_OPENER.open", side_effect=urllib.error.HTTPError("url", 429, "", {}, None)):
            with self.assertRaises(SearchError) as limited:
                asyncio.run(search(settings, "query"))
        self.assertEqual(limited.exception.category, "rate_limited")
        with patch("server.search_provider.URL_OPENER.open", return_value=Response(b'{"web":{"results":[]}}')):
            self.assertEqual(asyncio.run(search(settings, "query")), [])
        with patch("server.search_provider.URL_OPENER.open", side_effect=TimeoutError()):
            with self.assertRaises(SearchError) as timeout:
                asyncio.run(search(settings, "query"))
        self.assertEqual(timeout.exception.category, "network")

    def test_searxng_endpoint_requires_https_except_loopback(self) -> None:
        with self.assertRaises(ValueError):
            SearchSettings("searxng", "http://search.example.org", enabled=True).validated()
        with self.assertRaises(ValueError):
            SearchSettings("searxng", "https://user:password@search.example.org", enabled=True).validated()


class WebAgentTests(unittest.TestCase):
    def test_json_tool_bridge_can_request_web_search(self) -> None:
        asyncio.run(self._bridge_scenario())

    async def _bridge_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "db.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("检索组", owner["id"])
            agent = database.create_agent(owner["id"], name="搜索员")
            database.set_group_agent(group["id"], agent["id"], owner["id"], True)
            self.assertFalse(next(item for item in database.agent_tools(agent["id"]) if item["name"] == "web_search")["enabled"])
            database.set_agent_tools(agent["id"], owner["id"], [*DEFAULT_AGENT_TOOL_NAMES, "web_search"])
            config = replace(Config.from_env(), data_dir=root, database_path=root / "db.sqlite3", uploads_dir=root / "uploads")

            async def emit(*_args: Any) -> None:
                return None

            class JsonProvider:
                async def generate(self, system: str, _prompt: str) -> str:
                    if '"tool_calls"' in system:
                        return '{"tool_calls":[{"name":"web_search","arguments":{}}]}'
                    return "核查结果 [1]。"

            service = AgentService(config, database, emit)
            service.provider = JsonProvider()  # type: ignore[assignment]
            service.apply_search_settings(SearchSettings("brave", api_key="secret", enabled=True))
            with patch("server.agent_runtime.generation.search_web", new_callable=AsyncMock, return_value=[
                {"title": "资料", "url": "https://example.org", "snippet": "内容"}
            ]) as remote:
                await service.invoke(owner["id"], "group", str(group["id"]), "Python 的最新版本是什么", agent["id"])
            remote.assert_awaited_once()
            self.assertTrue(any(item["tool_name"] == "web_search" and item["status"] == "succeeded" for item in database.recent_tool_calls()))

    def test_web_tool_permission_and_question_flow(self) -> None:
        asyncio.run(self._scenario())

    async def _scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "db.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("研究组", owner["id"])
            scope_id = str(group["id"])
            agent = database.create_agent(owner["id"], name="搜索员")
            database.set_group_agent(group["id"], agent["id"], owner["id"], True)
            database.save_message("group", scope_id, owner["id"], "owner", "text", "内部私密代号 redbird")
            config = replace(Config.from_env(), data_dir=root, database_path=root / "db.sqlite3", uploads_dir=root / "uploads")
            events: list[tuple[str, dict[str, Any]]] = []

            async def emit(_scope: str, _id: str, event_type: str, payload: dict[str, Any]) -> None:
                events.append((event_type, payload))

            class Provider:
                def __init__(self) -> None:
                    self.tools: list[list[dict[str, Any]]] = []

                async def generate_tool_turn(self, _system: str, _messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ProviderTurn:
                    self.tools.append(tools)
                    return ProviderTurn("", [ProviderToolCall("web-1", "web_search", {})])

                async def generate(self, _system: str, _prompt: str) -> str:
                    return "根据资料 [1]，另见错误编号 [9] 和 [伪来源](https://fake.example)。"

            service = AgentService(config, database, emit)
            provider = Provider()
            service.provider = provider  # type: ignore[assignment]
            service.apply_search_settings(SearchSettings("brave", api_key="secret", enabled=True))
            query = "今天 Python 官方发布了什么？"
            response = [{"title": "官方新闻", "url": "https://example.org/news", "snippet": "已发布"}]
            with patch("server.agent_runtime.generation.search_web", new_callable=AsyncMock, return_value=response) as remote:
                await service.invoke(owner["id"], "group", scope_id, query, agent["id"])
                self.assertEqual(remote.await_count, 0)
                self.assertFalse(any(t["tool_name"] == "web_search" for t in database.recent_tool_calls()))

                database.set_agent_tools(agent["id"], owner["id"], [*DEFAULT_AGENT_TOOL_NAMES, "web_search"])
                await service.invoke(owner["id"], "group", scope_id, query, agent["id"])
                self.assertEqual(remote.await_count, 1)
                self.assertEqual(remote.await_args.args[1], query)
                answer = database.recent_history(owner["id"], "group", scope_id, 1)[0]["content"]
                self.assertIn("[官方新闻](https://example.org/news)", answer)
                self.assertNotIn("[9]", answer)
                self.assertNotIn("https://fake.example", answer)
                self.assertIn("web_search", [tool["name"] for tool in provider.tools[-1]])
                self.assertNotIn("redbird", database.recent_tool_calls()[0]["input_summary"])

                await service.invoke(owner["id"], "group", scope_id, "联网搜索：Python 官方新闻", agent["id"])
                self.assertEqual(remote.await_count, 2)
                self.assertEqual(remote.await_args.args[1], "Python 官方新闻")

                database.set_group_tools(group["id"], agent["id"], owner["id"], list(DEFAULT_AGENT_TOOL_NAMES))
                await service.invoke(owner["id"], "group", scope_id, "联网搜索：Python 官方新闻", agent["id"])
                self.assertEqual(remote.await_count, 2)
                denied = [t for t in database.recent_tool_calls() if t["tool_name"] == "web_search" and t["status"] == "denied"]
                self.assertTrue(denied)
                self.assertIn("联网搜索未完成", database.recent_history(owner["id"], "group", scope_id, 1)[0]["content"])

            self.assertEqual(AgentService._task_from_prompt("搜索 课程设计")[0], "search")

    def test_search_failure_and_empty_results_are_labeled(self) -> None:
        asyncio.run(self._failure_scenario())

    async def _failure_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "db.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("检索组", owner["id"])
            agent = database.create_agent(owner["id"], name="搜索员")
            database.set_group_agent(group["id"], agent["id"], owner["id"], True)
            database.set_agent_tools(agent["id"], owner["id"], [*DEFAULT_AGENT_TOOL_NAMES, "web_search"])
            config = replace(Config.from_env(), data_dir=root, database_path=root / "db.sqlite3", uploads_dir=root / "uploads")

            async def emit(*_args: Any) -> None:
                return None

            class Provider:
                async def generate(self, _system: str, _prompt: str) -> str:
                    return "这是未经核查的离线回答。"

            service = AgentService(config, database, emit)
            service.provider = Provider()  # type: ignore[assignment]
            await service.invoke(owner["id"], "group", str(group["id"]), "联网搜索：Python 新闻", agent["id"])
            missing = database.recent_history(owner["id"], "group", str(group["id"]), 1)[0]["content"]
            self.assertIn("联网搜索尚未配置或启用", missing)
            service.apply_search_settings(SearchSettings("brave", api_key="secret", enabled=True))
            for outcome, label in [(SearchError("搜索服务连接或请求超时", "network"), "未完成"), ([], "未找到")]:
                with self.subTest(label=label), patch("server.agent_runtime.generation.search_web", new_callable=AsyncMock, side_effect=outcome if isinstance(outcome, Exception) else None, return_value=outcome if isinstance(outcome, list) else None):
                    await service.invoke(owner["id"], "group", str(group["id"]), "联网搜索：Python 新闻", agent["id"])
                    answer = database.recent_history(owner["id"], "group", str(group["id"]), 1)[0]["content"]
                    self.assertIn(label, answer)


class SearchConfigTests(unittest.TestCase):
    def test_server_loads_search_config_without_admin_console(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            SearchConfigStore(root).save_search_settings(
                SearchSettings("brave", api_key="secret", enabled=True)
            )
            config = replace(Config.from_env(), data_dir=root, database_path=root / "db.sqlite3",
                             uploads_dir=root / "uploads", admin_enabled=False)
            server = ChatServer(config)
            self.assertTrue(server.agent.search_settings.enabled)
            self.assertEqual(server.agent.search_settings.provider, "brave")

    def test_admin_applies_only_tested_config_and_hides_key(self) -> None:
        asyncio.run(self._scenario())

    async def _scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "db.sqlite3", root / "uploads")
            database.initialize()
            database.register_user("admin01", "password-admin", role="admin")
            config = replace(Config.from_env(), data_dir=root, database_path=root / "db.sqlite3", uploads_dir=root / "uploads")

            async def emit(*_args: Any) -> None:
                return None

            class Chat:
                async def publish_agent_status(self) -> None:
                    return None

            agent = AgentService(config, database, emit)
            service = AdminService(database, agent, Chat(), ProviderConfigStore(root), AdminSessionStore(600))
            try:
                _, session = await service.login("admin01", "password-admin", "127.0.0.1")
                with patch("server.admin.service.search", new_callable=AsyncMock, return_value=[{"url": "https://example.org", "title": "Example", "snippet": "ok"}]) as probe:
                    public = await service.apply_search_provider(session, {"provider": "brave", "api_key": "secret", "enabled": True})
                self.assertEqual(probe.await_args.args[1], TEST_QUERY)
                self.assertNotIn("api_key", public)
                self.assertTrue(public["api_key_set"])
                store = SearchConfigStore(root)
                self.assertEqual(store.load_search_settings().api_key, "secret")
                if os.name == "posix":
                    self.assertEqual(store.settings_path.stat().st_mode & 0o777, 0o600)
                with patch("server.admin.service.search", new_callable=AsyncMock, side_effect=SearchError("HTTP 429", "rate_limited")):
                    with self.assertRaises(AdminRequestError):
                        await service.apply_search_provider(session, {"provider": "deepseek", "api_key": "new-secret", "enabled": True})
                self.assertEqual(store.load_search_settings().provider, "brave")
                self.assertEqual(agent.search_settings.provider, "brave")
                self.assertNotIn("secret", json.dumps(service.search_provider_view()))
                with patch("server.admin.service.search", new_callable=AsyncMock, return_value=[{"url": "https://example.org", "title": "Example", "snippet": "ok"}]):
                    deepseek_public = await service.apply_search_provider(session, {"provider": "deepseek", "api_key": "new-secret", "enabled": True})
                self.assertEqual(store.load_search_settings().provider, "deepseek")
                self.assertEqual(store.load_search_settings().api_key, "new-secret")
                self.assertNotIn("new-secret", json.dumps(deepseek_public))
                await service.apply_search_provider(session, {"enabled": False})
                self.assertFalse(store.load_search_settings().enabled)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
