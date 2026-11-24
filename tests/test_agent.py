from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from server.agent import AgentService
from server.config import Config
from server.database import Database, DatabaseError
from server.model_provider import AgentUnavailable, ProviderToolCall, ProviderTurn
from server.todos import TodoService


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def health(self) -> bool:
        return True

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if "普通对话" in system_prompt:
            if "你是谁" in user_prompt:
                return "我是这个聊天室的 Agent 助手。"
            return "在，我可以帮你回答问题。"
        if "提取待办" in system_prompt:
            return "事项：补充演示录屏\n负责人：owner\n期限：未明确"
        return "主题：TCP 联调\n结论：已完成\n未决问题：未明确"


class AgentTests(unittest.TestCase):
    def test_native_todo_tools_create_and_query_current_user_only(self) -> None:
        asyncio.run(self._native_todo_create_scenario())

    def test_todo_update_delete_confirmation_and_cross_user_isolation(self) -> None:
        asyncio.run(self._native_todo_mutation_scenario())

    def test_personal_extraction_sync_is_idempotent_and_skips_other_owners(self) -> None:
        asyncio.run(self._todo_extraction_sync_scenario())

    def test_structured_tool_fallback_uses_the_same_todo_dispatcher(self) -> None:
        asyncio.run(self._structured_todo_fallback_scenario())

    def test_read_and_write_todo_permissions_are_independently_enforced(self) -> None:
        asyncio.run(self._todo_permission_scenario())

    def test_todo_list_get_complete_and_reopen_tools(self) -> None:
        asyncio.run(self._todo_lifecycle_tools_scenario())

    def test_natural_language_defaults_to_conversation(self) -> None:
        self.assertEqual(AgentService._task_from_prompt("你能总结一下你是谁吗")[0], "question")
        self.assertEqual(AgentService._task_from_prompt("你现在是什么模型？")[0], "identity")
        self.assertEqual(AgentService._task_from_prompt("当前使用哪个供应商")[0], "identity")
        self.assertEqual(AgentService._task_from_prompt("待办是什么意思")[0], "question")
        self.assertEqual(AgentService._task_from_prompt("总结最近50条消息")[0], "summary")
        self.assertEqual(AgentService._task_from_prompt("提取最近50条消息中的待办")[0], "todo")
        self.assertEqual(AgentService._task_from_prompt("搜索 TCP 联调")[0], "search")

    def test_time_window_prompts(self) -> None:
        now = datetime(2026, 11, 24, 19, 30).timestamp()

        today = AgentService._parse_time_window("总结今天群里的消息", now)
        self.assertIsNotNone(today)
        assert today is not None
        self.assertEqual(datetime.fromtimestamp(today.start_at), datetime(2026, 11, 24))
        self.assertEqual(datetime.fromtimestamp(today.end_at), datetime(2026, 11, 24, 19, 30))
        self.assertEqual(today.label, "今天 00:00–19:30")

        yesterday = AgentService._parse_time_window("总结昨天 14:00 到 18:00 的消息", now)
        self.assertIsNotNone(yesterday)
        assert yesterday is not None
        self.assertEqual(datetime.fromtimestamp(yesterday.start_at), datetime(2026, 11, 23, 14, 0))
        self.assertEqual(datetime.fromtimestamp(yesterday.end_at), datetime(2026, 11, 23, 18, 0))

        afternoon = AgentService._parse_time_window("提取前天下午的待办", now)
        self.assertIsNotNone(afternoon)
        assert afternoon is not None
        self.assertEqual(datetime.fromtimestamp(afternoon.start_at), datetime(2026, 11, 22, 12, 0))
        self.assertEqual(datetime.fromtimestamp(afternoon.end_at), datetime(2026, 11, 22, 18, 0))

        overnight = AgentService._parse_time_window("总结昨天23点到1点的消息", now)
        self.assertIsNotNone(overnight)
        assert overnight is not None
        self.assertEqual(datetime.fromtimestamp(overnight.start_at), datetime(2026, 11, 23, 23, 0))
        self.assertEqual(datetime.fromtimestamp(overnight.end_at), datetime(2026, 11, 24, 1, 0))

        explicit = AgentService._parse_time_window("总结 11月1日 到 11月5日 的消息", now)
        self.assertIsNotNone(explicit)
        assert explicit is not None
        self.assertEqual(datetime.fromtimestamp(explicit.start_at), datetime(2026, 11, 1))
        self.assertEqual(datetime.fromtimestamp(explicit.end_at), datetime(2026, 11, 6))

        shorthand = AgentService._parse_time_window("总结11月1日到5日的消息", now)
        self.assertIsNotNone(shorthand)
        assert shorthand is not None
        self.assertEqual(datetime.fromtimestamp(shorthand.start_at), datetime(2026, 11, 1))
        self.assertEqual(datetime.fromtimestamp(shorthand.end_at), datetime(2026, 11, 6))

        numbered = AgentService._parse_time_window("总结11月1号到11月5号的消息", now)
        self.assertIsNotNone(numbered)
        assert numbered is not None
        self.assertEqual(datetime.fromtimestamp(numbered.start_at), datetime(2026, 11, 1))
        self.assertEqual(datetime.fromtimestamp(numbered.end_at), datetime(2026, 11, 6))

        recent = AgentService._parse_time_window("总结最近3天的消息", now)
        self.assertIsNotNone(recent)
        assert recent is not None
        self.assertEqual(datetime.fromtimestamp(recent.start_at), datetime(2026, 11, 22))

        monday = datetime(2026, 11, 24) - timedelta(days=datetime(2026, 11, 24).weekday())
        last_week = AgentService._parse_time_window("总结上周的消息", now)
        self.assertIsNotNone(last_week)
        assert last_week is not None
        self.assertEqual(datetime.fromtimestamp(last_week.start_at), monday - timedelta(days=7))
        self.assertEqual(datetime.fromtimestamp(last_week.end_at), monday)
        self.assertEqual(last_week.label, "11月16日–11月22日")

        cross_year = AgentService._parse_time_window("总结2025年12月31日的消息", now)
        self.assertIsNotNone(cross_year)
        assert cross_year is not None
        self.assertIn("2025年12月31日", cross_year.label)

        task, limit, _keyword, window = AgentService._task_from_prompt(
            "提取昨天下午的待办", now
        )
        self.assertEqual(task, "todo")
        self.assertEqual(limit, AgentService.TIME_RANGE_LIMIT)
        self.assertIsNotNone(window)

        _task, default_limit, _keyword, default_window = AgentService._task_from_prompt(
            "总结最近50条消息", now
        )
        self.assertEqual(default_limit, 50)
        self.assertIsNone(default_window)

    def test_agent_reads_authorized_history_logs_tools_and_saves_result(self) -> None:
        asyncio.run(self._scenario())

    def test_running_agent_keeps_provider_snapshot_during_reconfigure(self) -> None:
        asyncio.run(self._provider_snapshot_scenario())

    def test_chunked_summary_survives_one_empty_segment(self) -> None:
        asyncio.run(self._chunk_failure_scenario())

    async def _chunk_failure_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("分段测试群", owner["id"])
            database.save_message("group", str(group["id"]), owner["id"], "owner", "text", "甲" * 7000)
            database.save_message("group", str(group["id"]), owner["id"], "owner", "text", "乙" * 7000)
            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )

            async def emit(*_args: object) -> None:
                return None

            class FlakyProvider:
                def __init__(self) -> None:
                    self.calls = 0

                async def health(self) -> bool:
                    return True

                async def generate(self, system_prompt: str, user_prompt: str) -> str:
                    self.calls += 1
                    if self.calls == 1:
                        raise AgentUnavailable("模型返回了空结果", "invalid_response")
                    if self.calls == 2:
                        return "第二段包含结论"
                    return "合并后的总结"

            service = AgentService(config, database, emit)
            provider = FlakyProvider()
            service.provider = provider  # type: ignore[assignment]
            await service.invoke(owner["id"], "group", str(group["id"]), "总结最近50条消息")

            history = database.recent_history(owner["id"], "group", str(group["id"]), 10)
            self.assertEqual(history[-1]["sender_kind"], "agent")
            self.assertIn("合并后的总结", history[-1]["content"])
            self.assertEqual(provider.calls, 3)

    async def _provider_snapshot_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("快照测试群", owner["id"])
            database.save_message("group", str(group["id"]), owner["id"], "owner", "text", "旧配置上下文")
            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )

            async def emit(*_args: object) -> None:
                return None

            started = asyncio.Event()
            release = asyncio.Event()

            class BlockingProvider:
                async def health(self) -> bool:
                    return True

                async def generate(self, _system: str, _user: str) -> str:
                    started.set()
                    await release.wait()
                    return "旧 Provider 完成"

            service = AgentService(config, database, emit)
            service.provider = BlockingProvider()  # type: ignore[assignment]
            task = asyncio.create_task(
                service.invoke(owner["id"], "group", str(group["id"]), "你在吗")
            )
            await asyncio.wait_for(started.wait(), 1)
            service.apply_provider(service.settings, "new-key")
            self.assertFalse(service.online)
            release.set()
            await task
            result = database.recent_history(owner["id"], "group", str(group["id"]), 1)[0]
            self.assertEqual(result["content"], "旧 Provider 完成")
            self.assertFalse(service.online)

    async def _scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("Agent测试群", owner["id"])
            database.save_message("group", str(group["id"]), owner["id"], "owner", "text", "完成TCP联调")
            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )
            events: list[tuple[str, str, str, dict[str, Any]]] = []

            async def emit(scope_type: str, scope_id: str, event_type: str, payload: dict[str, Any]) -> None:
                events.append((scope_type, scope_id, event_type, payload))

            service = AgentService(config, database, emit)
            provider = FakeProvider()
            service.provider = provider  # type: ignore[assignment]
            await service.invoke(owner["id"], "group", str(group["id"]), "总结最近20条消息")

            history = database.recent_history(owner["id"], "group", str(group["id"]), 10)
            self.assertEqual(history[-1]["sender_kind"], "agent")
            self.assertEqual(history[-1]["content_format"], "markdown")
            self.assertIn("结论", history[-1]["content"])
            tools = database.recent_tool_calls()
            self.assertEqual({tool["tool_name"] for tool in tools}, {"read_conversation_messages", "save_agent_result"})
            self.assertTrue(all(tool["status"] == "succeeded" for tool in tools))
            self.assertTrue(any(event_type == "message.event" for _, _, event_type, _ in events))
            self.assertEqual(len(provider.calls), 1)
            self.assertIn(service.model, provider.calls[0][0])
            self.assertIn(service.provider_label, provider.calls[0][0])
            if service.api_key:
                self.assertNotIn(service.api_key, provider.calls[0][0])

    def test_task_prompts_and_current_agent_command_filtering(self) -> None:
        asyncio.run(self._task_prompt_scenario())

    def test_summary_time_range_filters_older_messages(self) -> None:
        asyncio.run(self._time_range_scenario())

    def test_personal_todo_uses_message_author_and_ignores_agent_history(self) -> None:
        asyncio.run(self._personal_todo_scenario())

    async def _personal_todo_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            colleague = database.register_user("user2", "password-user2")
            group = database.create_group("待办测试群", owner["id"])
            database.join_group(group["id"], colleague["id"])
            scope_id = str(group["id"])
            source = database.save_message(
                "group", scope_id, owner["id"], "owner", "text",
                "我明天要去参加原神的七神会议，然后 @user2 你去给我准备一下资料",
                mentions=[{"member_id": f"user:{colleague['id']}", "display": "user2", "start": 19, "length": 6}],
            )
            database.save_message(
                "group", scope_id, None, "永雏塔菲", "agent",
                "此前待办分析结论：没有明确待办", sender_kind="agent",
            )
            database.save_message(
                "group", scope_id, owner["id"], "owner", "text",
                "@永雏塔菲 提取我最近的待办",
            )
            config = replace(
                Config.from_env(), data_dir=root, database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads", agent_timeout_seconds=2,
            )

            async def emit(*_args: Any) -> None:
                return None

            class TodoProvider(FakeProvider):
                async def generate(self, system_prompt: str, user_prompt: str) -> str:
                    self.calls.append((system_prompt, user_prompt))
                    return (
                        '{"tasks":['
                        f'{{"title":"参加七神会议","owner":"owner","due":"明天","source_message_id":{source["id"]}}},'
                        f'{{"title":"准备七神会议资料","owner":"user2","source_message_id":{source["id"]}}}'
                        '],"unresolved":["资料负责人待确认"]}'
                    )

            service = AgentService(config, database, emit)
            provider = TodoProvider()
            service.provider = provider  # type: ignore[assignment]
            await service.invoke(owner["id"], "group", scope_id, "提取我最近的待办")
            prompt = provider.calls[-1][1]
            self.assertIn("我明天要去参加原神的七神会议", prompt)
            self.assertIn("用户 ID", prompt)
            self.assertIn("只提取由请求者负责的待办", prompt)
            self.assertNotIn("此前待办分析结论", prompt)
            self.assertNotIn("@永雏塔菲 提取我最近的待办", prompt)
            card = database.recent_history(owner["id"], "group", scope_id, 1)[0]["card"]
            self.assertEqual([task["title"] for task in card["tasks"]], ["参加七神会议"])
            self.assertEqual(card["tasks"][0]["owner"], "owner")
            self.assertEqual(card["unresolved"], [])
            self.assertTrue(card["personal_reconciled"])
            self.assertIn(
                "list_todos",
                {item["tool_name"] for item in database.recent_tool_calls()},
            )
            await service.invoke(owner["id"], "group", scope_id, "提取最近的待办")
            group_card = database.recent_history(owner["id"], "group", scope_id, 1)[0]["card"]
            self.assertEqual(
                [task["owner"] for task in group_card["tasks"]], ["owner", "user2"]
            )

    async def _native_todo_create_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("工具测试群", owner["id"])
            todos = TodoService(database)
            changed: list[tuple[int, str, dict[str, Any]]] = []

            async def emit(*_args: Any) -> None:
                return None

            async def todo_event(owner_id: int, event_type: str, payload: dict[str, Any]) -> None:
                changed.append((owner_id, event_type, payload))

            class NativeProvider:
                def __init__(self) -> None:
                    self.turns = [
                        ProviderTurn(
                            "",
                            [
                                ProviderToolCall(
                                    "call-create",
                                    "create_todo",
                                    {"title": "买牛奶", "priority": "high"},
                                )
                            ],
                        ),
                        ProviderTurn("已根据工具结果处理。", []),
                    ]
                    self.tools: list[list[dict[str, Any]]] = []

                async def health(self) -> bool:
                    return True

                async def generate(self, _system: str, _user: str) -> str:
                    return ""

                async def generate_tool_turn(
                    self, _system: str, _messages: list[dict[str, Any]], tools: list[dict[str, Any]]
                ) -> ProviderTurn:
                    self.tools.append(tools)
                    return self.turns.pop(0)

            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )
            service = AgentService(
                config,
                database,
                emit,
                todos=todos,
                todo_event_callback=todo_event,
            )
            provider = NativeProvider()
            service.provider = provider  # type: ignore[assignment]
            await service.invoke(
                owner["id"],
                "group",
                str(group["id"]),
                "帮我创建一个高优先级待办：买牛奶",
            )
            records = todos.list(owner["id"])["todos"]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["title"], "买牛奶")
            self.assertEqual(records[0]["priority"], "high")
            self.assertEqual(records[0]["source_conversation_id"], str(group["id"]))
            self.assertTrue(any(item[1] == "todo.changed" for item in changed))
            tool_schema = next(
                item for item in provider.tools[0] if item["name"] == "create_todo"
            )
            self.assertNotIn("owner_id", tool_schema["parameters"]["properties"])
            calls = database.recent_tool_calls()
            self.assertTrue(
                any(item["tool_name"] == "create_todo" and item["status"] == "succeeded" for item in calls)
            )

    async def _native_todo_mutation_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            other = database.register_user("other", "password-other")
            group = database.create_group("工具更新群", owner["id"])
            database.join_group(group["id"], other["id"])
            todos = TodoService(database)
            todo = todos.create(owner["id"], title="实验报告")["todo"]
            edited = todos.update(
                owner["id"], todo["id"], description="用户手动修改"
            )["todo"]

            async def emit(*_args: Any) -> None:
                return None

            class NativeProvider:
                def __init__(self, turns: list[ProviderTurn]) -> None:
                    self.turns = turns

                async def health(self) -> bool:
                    return True

                async def generate(self, _system: str, _user: str) -> str:
                    return ""

                async def generate_tool_turn(
                    self, _system: str, _messages: list[dict[str, Any]], _tools: list[dict[str, Any]]
                ) -> ProviderTurn:
                    return self.turns.pop(0)

            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )
            service = AgentService(config, database, emit, todos=todos)
            service.provider = NativeProvider(  # type: ignore[assignment]
                [
                    ProviderTurn(
                        "",
                        [
                            ProviderToolCall(
                                "call-update",
                                "update_todo",
                                {
                                    "todo_id": edited["id"],
                                    "expected_title": "实验报告",
                                    "expected_version": edited["version"],
                                    "priority": "high",
                                },
                            )
                        ],
                    ),
                    ProviderTurn("已更新。", []),
                ]
            )
            await service.invoke(
                owner["id"], "group", str(group["id"]), "把实验报告设置为高优先级"
            )
            self.assertEqual(todos.get(owner["id"], edited["id"])["todo"]["priority"], "high")

            current = todos.get(owner["id"], edited["id"])["todo"]
            service.provider = NativeProvider(  # type: ignore[assignment]
                [
                    ProviderTurn(
                        "",
                        [
                            ProviderToolCall(
                                "call-delete",
                                "delete_todo",
                                {
                                    "todo_id": current["id"],
                                    "expected_title": "实验报告",
                                    "expected_version": current["version"],
                                },
                            )
                        ],
                    ),
                    ProviderTurn("删除需要确认。", []),
                ]
            )
            await service.invoke(
                owner["id"], "group", str(group["id"]), "删除实验报告"
            )
            self.assertTrue(todos.get(owner["id"], current["id"])["todo"])
            approval = next(
                item for item in database.recent_tool_calls() if item["tool_name"] == "delete_todo"
            )
            self.assertEqual(approval["status"], "approval_required")

            service.provider = NativeProvider(  # type: ignore[assignment]
                [
                    ProviderTurn(
                        "",
                        [
                            ProviderToolCall(
                                "call-delete-confirmed",
                                "delete_todo",
                                {
                                    "todo_id": current["id"],
                                    "expected_title": "实验报告",
                                    "expected_version": current["version"],
                                },
                            )
                        ],
                    ),
                    ProviderTurn("已删除。", []),
                ]
            )
            await service.invoke(
                owner["id"], "group", str(group["id"]), "确认删除实验报告"
            )
            with self.assertRaises(DatabaseError):
                todos.get(owner["id"], current["id"])

            other_todo = todos.create(owner["id"], title="不可越权")["todo"]
            service.provider = NativeProvider(  # type: ignore[assignment]
                [
                    ProviderTurn(
                        "",
                        [
                            ProviderToolCall(
                                "call-cross-user",
                                "complete_todo",
                                {
                                    "todo_id": other_todo["id"],
                                    "expected_title": "不可越权",
                                    "expected_version": other_todo["version"],
                                },
                            )
                        ],
                    ),
                    ProviderTurn("无法访问。", []),
                ]
            )
            await service.invoke(
                other["id"], "group", str(group["id"]), "完成不可越权任务"
            )
            self.assertFalse(todos.get(owner["id"], other_todo["id"])["todo"]["completed"])

    async def _todo_extraction_sync_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            colleague = database.register_user("other", "password-other")
            group = database.create_group("同步提取群", owner["id"])
            database.join_group(group["id"], colleague["id"])
            scope_id = str(group["id"])
            source = database.save_message(
                "group",
                scope_id,
                owner["id"],
                "owner",
                "text",
                "我明天要提交实验报告，@other 你去准备答辩材料",
                mentions=[{"member_id": f"user:{colleague['id']}", "display": "other"}],
            )

            async def emit(*_args: Any) -> None:
                return None

            class SyncProvider:  # type: ignore[misc]
                async def health(self) -> bool:
                    return True

                async def generate(self, _system: str, _user: str) -> str:
                    return (
                        '{"tasks":['
                        f'{{"title":"提交实验报告","description":"提交最终报告","owner":"owner","due":"明天","priority":"high","priority_basis":"explicit","status":"open","source_message_id":{source["id"]}}},'
                        f'{{"title":"准备答辩材料","owner":"other","source_message_id":{source["id"]}}}'
                        '],"unresolved":[]}'
                    )

            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )
            todos = TodoService(database)
            service = AgentService(config, database, emit, todos=todos)
            service.provider = SyncProvider()  # type: ignore[assignment]
            await service.invoke(
                owner["id"], "group", scope_id, "提取我最近的待办并同步"
            )
            records = todos.list(owner["id"])["todos"]
            self.assertEqual([item["title"] for item in records], ["提交实验报告"])
            self.assertEqual(records[0]["source_message_id"], source["id"])
            self.assertEqual(records[0]["priority"], "high")
            message = database.recent_history(owner["id"], "group", scope_id, 1)[0]
            self.assertEqual(message["card"]["tasks"][0]["sync_status"], "created")
            await service.invoke(
                owner["id"], "group", scope_id, "提取我最近的待办并同步"
            )
            self.assertEqual(len(todos.list(owner["id"])["todos"]), 1)
            second = database.recent_history(owner["id"], "group", scope_id, 1)[0]
            self.assertEqual(second["card"]["tasks"][0]["sync_status"], "matched")

    async def _structured_todo_fallback_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("兼容模型群", owner["id"])

            async def emit(*_args: Any) -> None:
                return None

            class JsonBridgeProvider:
                def __init__(self) -> None:
                    self.calls = 0

                async def health(self) -> bool:
                    return True

                async def generate(self, system: str, _user: str) -> str:
                    self.calls += 1
                    self.last_system = system
                    if self.calls == 1:
                        return '{"tool_calls":[{"name":"create_todo","arguments":{"title":"兼容创建"}}]}'
                    return '{"response":"已根据真实工具结果创建待办。"}'

            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )
            todos = TodoService(database)
            service = AgentService(config, database, emit, todos=todos)
            provider = JsonBridgeProvider()
            service.provider = provider  # type: ignore[assignment]
            await service.invoke(
                owner["id"], "group", str(group["id"]), "帮我创建一个待办：兼容创建"
            )
            self.assertEqual([item["title"] for item in todos.list(owner["id"])["todos"]], ["兼容创建"])
            self.assertEqual(provider.calls, 2)
            self.assertIn("tool_calls", provider.last_system)

    async def _todo_permission_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("权限测试群", owner["id"])
            agent = database.create_agent(owner["id"], name="只读待办 Agent")
            database.set_group_agent(group["id"], agent["id"], owner["id"], True)
            database.set_agent_tools(
                agent["id"], owner["id"], ["list_todos", "save_agent_result"]
            )

            async def emit(*_args: Any) -> None:
                return None

            class WriteAttemptProvider:
                async def health(self) -> bool:
                    return True

                async def generate(self, _system: str, _user: str) -> str:
                    return ""

                async def generate_tool_turn(
                    self, _system: str, _messages: list[dict[str, Any]], _tools: list[dict[str, Any]]
                ) -> ProviderTurn:
                    return ProviderTurn(
                        "",
                        [ProviderToolCall("call-denied", "create_todo", {"title": "不应创建"})],
                    )

            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )
            todos = TodoService(database)
            service = AgentService(config, database, emit, todos=todos)
            service.provider = WriteAttemptProvider()  # type: ignore[assignment]
            await service.invoke(
                owner["id"], "group", str(group["id"]), "创建一个待办", agent["id"]
            )
            self.assertEqual(todos.list(owner["id"])["todos"], [])
            denied = [item for item in database.recent_tool_calls() if item["tool_name"] == "create_todo"]
            self.assertEqual(denied[0]["status"], "denied")

    async def _todo_lifecycle_tools_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("生命周期群", owner["id"])
            todos = TodoService(database)
            todo = todos.create(owner["id"], title="实验报告")["todo"]

            async def emit(*_args: Any) -> None:
                return None

            class LifecycleProvider:
                def __init__(self) -> None:
                    self.step = 0

                @staticmethod
                def _last_todo(messages: list[dict[str, Any]]) -> dict[str, Any]:
                    for message in reversed(messages):
                        if message.get("role") != "tool":
                            continue
                        payload = json.loads(str(message.get("content") or "{}"))
                        data = payload.get("data") if isinstance(payload, dict) else None
                        if isinstance(data, dict) and isinstance(data.get("todo"), dict):
                            return data["todo"]
                        if isinstance(data, dict):
                            items = data.get("todos")
                            if isinstance(items, list) and items:
                                return items[0]
                    return {}

                async def health(self) -> bool:
                    return True

                async def generate(self, _system: str, _user: str) -> str:
                    return ""

                async def generate_tool_turn(
                    self, _system: str, messages: list[dict[str, Any]], _tools: list[dict[str, Any]]
                ) -> ProviderTurn:
                    self.step += 1
                    last = self._last_todo(messages)
                    version = int(last.get("version") or todo["version"])
                    if self.step == 1:
                        return ProviderTurn("", [ProviderToolCall("l", "list_todos", {"status": "open"})])
                    if self.step == 2:
                        return ProviderTurn("", [ProviderToolCall("g", "get_todo", {"todo_id": todo["id"]})])
                    if self.step == 3:
                        return ProviderTurn(
                            "",
                            [
                                ProviderToolCall(
                                    "c",
                                    "complete_todo",
                                    {
                                        "todo_id": todo["id"],
                                        "expected_title": "实验报告",
                                        "expected_version": version,
                                    },
                                )
                            ],
                        )
                    if self.step == 4:
                        return ProviderTurn(
                            "",
                            [
                                ProviderToolCall(
                                    "r",
                                    "reopen_todo",
                                    {
                                        "todo_id": todo["id"],
                                        "expected_title": "实验报告",
                                        "expected_version": version,
                                    },
                                )
                            ],
                        )
                    return ProviderTurn("已完成查询、完成和恢复。", [])

            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )
            service = AgentService(config, database, emit, todos=todos)
            service.provider = LifecycleProvider()  # type: ignore[assignment]
            await service.invoke(
                owner["id"], "group", str(group["id"]), "完成实验报告再恢复它"
            )
            self.assertFalse(todos.get(owner["id"], todo["id"])["todo"]["completed"])
            names = [item["tool_name"] for item in database.recent_tool_calls()]
            self.assertIn("list_todos", names)
            self.assertIn("get_todo", names)
            self.assertIn("complete_todo", names)
            self.assertIn("reopen_todo", names)

    async def _time_range_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("时间区间群", owner["id"])
            scope_id = str(group["id"])
            old = database.save_message(
                "group", scope_id, owner["id"], "owner", "text", "三天前的旧消息"
            )
            middle = database.save_message(
                "group", scope_id, owner["id"], "owner", "text", "区间内的消息"
            )
            latest = database.save_message(
                "group", scope_id, owner["id"], "owner", "text", "最新消息"
            )
            now = time.time()
            with sqlite3.connect(database.path) as conn:
                conn.execute(
                    "UPDATE messages SET created_at=? WHERE id=?",
                    (now - 3 * 86400, old["id"]),
                )
                conn.execute(
                    "UPDATE messages SET created_at=? WHERE id=?",
                    (now - 3600, middle["id"]),
                )
                conn.execute(
                    "UPDATE messages SET created_at=? WHERE id=?",
                    (now - 60, latest["id"]),
                )
            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )

            async def emit(
                _scope_type: str, _scope_id: str, _event_type: str, _payload: dict[str, Any]
            ) -> None:
                return None

            service = AgentService(config, database, emit)
            provider = FakeProvider()
            service.provider = provider  # type: ignore[assignment]

            await service.invoke(owner["id"], "group", scope_id, "总结最近2天的消息")
            prompt = provider.calls[-1][1]
            self.assertIn("区间内的消息", prompt)
            self.assertIn("最新消息", prompt)
            self.assertNotIn("三天前的旧消息", prompt)
            card = database.recent_history(owner["id"], "group", scope_id, 1)[0]["card"]
            self.assertEqual(card["message_count"], 2)
            self.assertIsNotNone(card["period"])
            self.assertLessEqual(card["period"]["start_at"], card["period"]["end_at"])

    async def _task_prompt_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("Agent问答群", owner["id"])
            scope_id = str(group["id"])
            database.save_message("group", scope_id, owner["id"], "owner", "text", "我们已完成 TCP 联调")
            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=2,
            )

            async def emit(
                _scope_type: str, _scope_id: str, _event_type: str, _payload: dict[str, Any]
            ) -> None:
                return None

            service = AgentService(config, database, emit)
            provider = FakeProvider()
            service.provider = provider  # type: ignore[assignment]

            trigger = database.save_message(
                "group", scope_id, owner["id"], "owner", "text", "@Agent 你在吗"
            )
            await service.invoke(owner["id"], "group", scope_id, "你在吗")
            answer = database.recent_history(owner["id"], "group", scope_id, 1)[0]["content"]
            self.assertNotIn("主题", answer)
            self.assertNotIn("结论", answer)
            self.assertNotIn("待办事项", answer)
            self.assertNotIn("@Agent 你在吗", provider.calls[-1][1])
            self.assertEqual(database.get_message(trigger["id"])["content"], "@Agent 你在吗")

            database.save_message("group", scope_id, owner["id"], "owner", "text", "@Agent 你是谁")
            await service.invoke(owner["id"], "group", scope_id, "你是谁")
            identity = database.recent_history(owner["id"], "group", scope_id, 1)[0]["content"]
            self.assertIn("聊天室", identity)
            self.assertIn("Agent", identity)
            self.assertIn("在，我可以帮你回答问题。", provider.calls[-1][1])

            provider_call_count = len(provider.calls)
            await service.invoke(owner["id"], "group", scope_id, "你现在是什么模型？")
            model_identity = database.recent_history(owner["id"], "group", scope_id, 1)[0]["content"]
            self.assertIn(service.provider_label, model_identity)
            self.assertIn(service.model, model_identity)
            self.assertIn("协议", model_identity)
            self.assertEqual(len(provider.calls), provider_call_count)

            await service.invoke(owner["id"], "group", scope_id, "总结最近50条消息")
            summary = database.recent_history(owner["id"], "group", scope_id, 1)[0]["content"]
            self.assertTrue(summary.startswith("## 群聊总结"))
            self.assertIn("### 主要话题", summary)
            self.assertIn("### 核心结论", summary)
            self.assertIn("### 待确认事项", summary)
            summary_message = database.recent_history(owner["id"], "group", scope_id, 1)[0]
            self.assertEqual(summary_message["card"]["kind"], "summary")
            self.assertEqual(summary_message["card"]["overview"], "TCP 联调")
            self.assertEqual(
                [item["text"] for item in summary_message["card"]["conclusions"]],
                ["已完成"],
            )

            await service.invoke(owner["id"], "group", scope_id, "提取最近50条消息中的待办")
            todo = database.recent_history(owner["id"], "group", scope_id, 1)[0]["content"]
            self.assertTrue(todo.startswith("## 待办提取"))
            self.assertIn("### 明确任务", todo)
            self.assertIn("### 建议跟进", todo)
            self.assertIn("### 信息缺失", todo)
            todo_message = database.recent_history(owner["id"], "group", scope_id, 1)[0]
            self.assertEqual(todo_message["card"]["kind"], "todo")
            self.assertEqual(len(todo_message["card"]["tasks"]), 1)
            self.assertEqual(todo_message["card"]["tasks"][0]["title"], "补充演示录屏")
            self.assertEqual(todo_message["card"]["tasks"][0]["owner"], "owner")
            self.assertEqual(todo_message["card"]["tasks"][0]["due"], "")


if __name__ == "__main__":
    unittest.main()

