from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from server.agent import AgentService
from server.config import Config
from server.database import Database


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
    def test_natural_language_defaults_to_conversation(self) -> None:
        self.assertEqual(AgentService._task_from_prompt("你能总结一下你是谁吗")[0], "question")
        self.assertEqual(AgentService._task_from_prompt("你现在是什么模型？")[0], "identity")
        self.assertEqual(AgentService._task_from_prompt("当前使用哪个供应商")[0], "identity")
        self.assertEqual(AgentService._task_from_prompt("待办是什么意思")[0], "question")
        self.assertEqual(AgentService._task_from_prompt("总结最近50条消息")[0], "summary")
        self.assertEqual(AgentService._task_from_prompt("提取最近50条消息中的待办")[0], "todo")
        self.assertEqual(AgentService._task_from_prompt("搜索 TCP 联调")[0], "search")

    def test_agent_reads_authorized_history_logs_tools_and_saves_result(self) -> None:
        asyncio.run(self._scenario())

    def test_running_agent_keeps_provider_snapshot_during_reconfigure(self) -> None:
        asyncio.run(self._provider_snapshot_scenario())

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

