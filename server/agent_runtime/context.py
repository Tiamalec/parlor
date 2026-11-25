"""Context loading, message formatting and memory compaction."""

from __future__ import annotations

from typing import Any
from ..database import DatabaseError
from ..agent_time import TimeWindow
import asyncio
from datetime import datetime
import logging
import re


LOGGER = logging.getLogger("chatroom.agent")


class AgentContextMixin:
    """Context loading, message formatting and memory compaction."""

    @staticmethod
    def _message_lines(messages: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for message in messages:
            if message.get("message_type") == "sticker":
                content = f"[表情包：{message.get('sticker_name') or '未命名'}]"
            elif message.get("message_type") == "image":
                content = f"[图片：{message.get('image_name', '未命名')}]"
            else:
                content = message.get("content") or ""
            stamp = message.get("created_at")
            sent_at = datetime.fromtimestamp(float(stamp)).strftime("%Y-%m-%d %H:%M") if stamp else "时间未知"
            author = str(message.get("sender_name") or "未知")
            if message.get("sender_kind") == "user" and message.get("sender_id") is not None:
                author += f"（用户 ID {message['sender_id']}）"
            line = f"#{message['id']} [{sent_at}] {author}: {content}".strip()
            lines.append(line)
        return lines

    @classmethod
    def _format_messages(cls, messages: list[dict[str, Any]], max_chars: int = 18_000) -> str:
        selected: list[str] = []
        used = 0
        for line in reversed(cls._message_lines(messages)):
            if used + len(line) > max_chars:
                break
            selected.append(line)
            used += len(line)
        return "\n".join(reversed(selected))

    @classmethod
    def _chunk_contexts(cls, messages: list[dict[str, Any]], max_chars: int = 12_000) -> list[str]:
        chunks: list[list[str]] = [[]]
        used = 0
        for line in cls._message_lines(messages):
            if chunks[-1] and used + len(line) > max_chars:
                chunks.append([])
                used = 0
            chunks[-1].append(line[:max_chars])
            used += min(len(line), max_chars)
        return ["\n".join(chunk) for chunk in chunks if chunk]

    @staticmethod
    def _without_current_trigger(
        messages: list[dict[str, Any]], requested_by: int, prompt: str
    ) -> list[dict[str, Any]]:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.get("sender_kind") != "user" or message.get("sender_id") != requested_by:
                continue
            match = re.match(
                r"^\s*@(?:agent|ai\s+agent)(?:\s+(.*))?\s*$",
                str(message.get("content", "")),
                re.IGNORECASE,
            )
            if not match:
                continue
            command_prompt = (match.group(1) or "").strip() or "请总结最近 50 条消息"
            if command_prompt == prompt.strip():
                return messages[:index] + messages[index + 1 :]
        return messages

    @staticmethod
    def _personal_todo_request(prompt: str) -> bool:
        return bool(re.search(r"(?:提取|整理|列出|生成).{0,24}(?:我(?:的|自己(?:的)?)?|给我).{0,16}(?:待办|todo)", prompt, re.IGNORECASE))

    async def _load_context(
        self,
        run_id: int,
        agent: dict[str, Any],
        requested_by: int,
        scope_type: str,
        scope_id: str,
        task_type: str,
        limit: int,
        keyword: str | None,
        after_id: int = 0,
        window: TimeWindow | None = None,
    ) -> list[dict[str, Any]]:
        tool_name = "search_conversation_messages" if task_type == "search" else "read_conversation_messages"
        summary = f"scope={scope_type}:{scope_id}, limit={limit}"
        if keyword:
            summary += f", keyword={keyword[:80]}"
        if window:
            summary += f", range={window.label}"
        async def load() -> tuple[list[dict[str, Any]], int]:
            if keyword:
                messages = await asyncio.to_thread(
                    self.database.search_messages, requested_by, scope_type, scope_id, keyword, limit
                )
            else:
                messages = await asyncio.to_thread(
                    self.database.recent_history,
                    requested_by,
                    scope_type,
                    scope_id,
                    limit,
                    after_id=after_id,
                    since=window.start_at if window else None,
                    until=window.end_at if window else None,
                )
            return messages, len(messages)

        return await self._execute_tool(
            run_id, agent, scope_type, scope_id, tool_name, summary, load
        )

    async def _load_memory(
        self,
        run_id: int,
        agent: dict[str, Any],
        requested_by: int,
        scope_type: str,
        scope_id: str,
        topic: str,
    ) -> str:
        if not self.database.memory_effective(
            str(agent["id"]), scope_type, scope_id, requested_by
        ):
            return ""
        definition, _mode, allowed = self._tool_policy(
            str(agent["id"]), scope_type, scope_id, "search_agent_memory"
        )
        if not allowed:
            return ""

        async def search() -> tuple[list[dict[str, Any]], int]:
            result = await asyncio.to_thread(
                self.database.search_memories,
                str(agent["id"]),
                requested_by,
                scope_type,
                scope_id,
                topic,
                6,
            )
            return result, len(result)

        memories = await self._execute_tool(
            run_id,
            agent,
            scope_type,
            scope_id,
            definition.name,
            f"topic={topic[:80]}",
            search,
        )
        if not memories:
            return ""
        lines = [f"- {memory['content'][:600]}" for memory in memories]
        return "长期记忆（可能过时，只作参考）：\n" + "\n".join(lines)

    async def _compact_context(
        self,
        run_id: int,
        agent: dict[str, Any],
        scope_type: str,
        scope_id: str,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str]:
        if len(messages) <= 40:
            return messages, ""
        older = messages[:-20]
        recent = messages[-20:]

        async def summarize() -> tuple[str, int]:
            summary = await self._generate_once(
                "context_summary",
                "请把较早的会话压缩成简短背景摘要，保留事实、决定和未决事项，不要编造。",
                self._format_messages(older),
            )
            return summary, len(older)

        try:
            summary = await self._execute_tool(
                run_id,
                agent,
                scope_type,
                scope_id,
                "summarize_agent_context",
                f"messages={len(older)}, last_id={older[-1]['id']}",
                summarize,
            )
        except DatabaseError:
            # Context compaction is an optimization; a denied tool must not
            # turn an otherwise authorized question into a failed run.
            return messages, ""
        await asyncio.to_thread(
            self.database.save_context_summary,
            str(agent["id"]),
            scope_type,
            scope_id,
            summary,
            int(older[-1]["id"]),
        )
        return recent, summary

    async def _save_long_term_memory(
        self,
        run_id: int,
        agent: dict[str, Any],
        requested_by: int,
        scope_type: str,
        scope_id: str,
        task_type: str,
        result: str,
    ) -> None:
        if task_type not in {"summary", "todo"}:
            return
        if not self.database.memory_effective(
            str(agent["id"]), scope_type, scope_id, requested_by
        ):
            return
        _definition, _mode, allowed = self._tool_policy(
            str(agent["id"]), scope_type, scope_id, "save_agent_memory"
        )
        if not allowed:
            return

        async def save() -> tuple[dict[str, Any], int]:
            memory = await asyncio.to_thread(
                self.database.save_memory,
                str(agent["id"]),
                requested_by,
                scope_type,
                scope_id,
                f"会话摘要：{result[:3500]}",
                kind=f"{task_type}_summary",
            )
            return memory, 1

        try:
            await self._execute_tool(
                run_id,
                agent,
                scope_type,
                scope_id,
                "save_agent_memory",
                f"kind={task_type}_summary, chars={min(len(result), 3500)}",
                save,
                confirmed=True,
            )
        except Exception:
            # The user-visible result has already been saved. Memory persistence
            # is best effort and must not turn a successful answer into a failure.
            LOGGER.warning("Agent 长期记忆写入失败，已保留会话结果", exc_info=True)
