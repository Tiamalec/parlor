"""Personal todo handlers."""

from __future__ import annotations

from typing import Any
from ..session import ClientSession
from ..database import DatabaseError
import asyncio


class TodoHandlers:
    """Personal todo handlers."""

    async def _todo_list(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        result = await asyncio.to_thread(self.todos.list, int(session.user["id"]))
        await session.send("todo.list", result, request_id=request_id)

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
