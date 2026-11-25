"""Agent memory and context summaries."""

from __future__ import annotations

from typing import Any
from .base import DatabaseError
from ..agent_config import memory_terms
import re
import sqlite3
import time


class MemoryMixin:
    """Agent memory and context summaries."""

    def _assert_agent_scope(
        self, agent_id: str, user_id: int, scope_type: str, scope_id: str
    ) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        if not agent["enabled"]:
            raise DatabaseError("Agent 已停用")
        if scope_type == "group":
            try:
                group_id = int(scope_id)
            except ValueError as exc:
                raise DatabaseError("群会话无效") from exc
            self._assert_access(user_id, "group", scope_id)
            with self._connect() as conn:
                bound = conn.execute(
                    "SELECT enabled FROM group_agents WHERE group_id=? AND agent_id=?",
                    (group_id, agent_id),
                ).fetchone()
            if not bound or not bound["enabled"]:
                raise DatabaseError("该 Agent 未加入当前群组")
        elif scope_type == "private":
            self._assert_access(user_id, "private", scope_id)
        else:
            raise DatabaseError("会话类型无效")
        return agent

    def memory_setting_enabled(
        self, agent_id: str, scope_type: str, scope_id: str, user_id: int | None = None
    ) -> bool:
        setting_user = 0 if scope_type == "group" else int(user_id or 0)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM agent_memory_settings WHERE agent_id=? AND user_id=? AND scope_type=? AND scope_id=?",
                (agent_id, setting_user, scope_type, scope_id),
            ).fetchone()
        return bool(row and row["enabled"])

    def memory_effective(
        self, agent_id: str, scope_type: str, scope_id: str, user_id: int | None = None
    ) -> bool:
        agent = self.get_agent(agent_id)
        return bool(
            agent["enabled"]
            and agent["memory_enabled"]
            and self.memory_setting_enabled(agent_id, scope_type, scope_id, user_id)
        )

    def set_memory_enabled(
        self, agent_id: str, user_id: int, scope_type: str, scope_id: str, enabled: bool
    ) -> dict[str, Any]:
        self._assert_agent_scope(agent_id, user_id, scope_type, scope_id)
        if scope_type == "group" and not self.can_manage_group(int(scope_id), user_id):
            raise DatabaseError("只有群主或管理员可以修改群共享记忆设置")
        setting_user = 0 if scope_type == "group" else user_id
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_memory_settings(agent_id,user_id,scope_type,scope_id,enabled,updated_by,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(agent_id,user_id,scope_type,scope_id) DO UPDATE SET
                enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (agent_id, setting_user, scope_type, scope_id, int(bool(enabled)), user_id, now),
            )
        return {
            "agent_id": agent_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "enabled": bool(enabled),
        }

    def _memory_dict(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled", 0))
        data.pop("owner_role", None)
        return data

    def list_memories(
        self, agent_id: str, user_id: int, scope_type: str, scope_id: str
    ) -> list[dict[str, Any]]:
        self._assert_agent_scope(agent_id, user_id, scope_type, scope_id)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT m.*,u.username AS created_by_name FROM agent_memories m
                LEFT JOIN users u ON u.id=m.created_by
                WHERE m.agent_id=? AND m.scope_type=? AND m.scope_id=? AND m.enabled=1
                AND (m.scope_type='group' OR m.owner_id=?)
                ORDER BY m.updated_at DESC,m.id DESC""",
                (agent_id, scope_type, scope_id, user_id),
            ).fetchall()
        return [self._memory_dict(row) for row in rows]

    def search_memories(
        self, agent_id: str, user_id: int, scope_type: str, scope_id: str, topic: str, limit: int = 6
    ) -> list[dict[str, Any]]:
        self._assert_agent_scope(agent_id, user_id, scope_type, scope_id)
        terms = memory_terms(topic)
        rows = self.list_memories(agent_id, user_id, scope_type, scope_id)
        scored: list[tuple[int, float, dict[str, Any]]] = []
        for memory in rows:
            content = str(memory.get("content", ""))
            score = sum(1 for term in terms if term and term in content)
            if score or not terms:
                scored.append((score, float(memory.get("updated_at", 0) or 0), memory))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = [item[2] for item in scored[: max(1, min(int(limit), 12))]]
        if selected:
            with self._connect() as conn:
                conn.executemany(
                    "UPDATE agent_memories SET last_accessed_at=? WHERE id=?",
                    [(time.time(), int(memory["id"])) for memory in selected],
                )
        return selected

    def _assert_memory_manager(self, memory: dict[str, Any], user_id: int) -> None:
        self._assert_agent_scope(
            str(memory["agent_id"]), user_id, str(memory["scope_type"]), str(memory["scope_id"])
        )
        if (
            memory["scope_type"] == "group"
            and not self.can_manage_group(int(memory["scope_id"]), user_id)
            and int(memory.get("created_by") or 0) != user_id
        ):
            raise DatabaseError("只有群主、管理员或记忆创建者可以修改群共享记忆")
        if memory["scope_type"] == "private" and int(memory.get("owner_id") or 0) != user_id:
            raise DatabaseError("无权修改其他用户的私聊记忆")

    def save_memory(
        self,
        agent_id: str,
        user_id: int,
        scope_type: str,
        scope_id: str,
        content: str,
        *,
        kind: str = "note",
        source_message_id: int | None = None,
    ) -> dict[str, Any]:
        self._assert_agent_scope(agent_id, user_id, scope_type, scope_id)
        if not self.memory_effective(agent_id, scope_type, scope_id, user_id):
            raise DatabaseError("当前会话未开启 Agent 长期记忆")
        clean = str(content or "").strip()
        if not 1 <= len(clean) <= 4000:
            raise DatabaseError("记忆内容需为1至4000个字符")
        kind = re.sub(r"[^a-z_]", "", str(kind or "note").lower())[:40] or "note"
        now = time.time()
        owner_id = user_id if scope_type == "private" else None
        group_id = int(scope_id) if scope_type == "group" else None
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO agent_memories(
                    agent_id,scope_type,scope_id,owner_id,group_id,kind,content,source_message_id,created_by,created_at,updated_at,enabled
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
                (agent_id, scope_type, scope_id, owner_id, group_id, kind, clean, source_message_id, user_id, now, now),
            )
            row = conn.execute(
                """SELECT m.*,u.username AS created_by_name FROM agent_memories m
                LEFT JOIN users u ON u.id=m.created_by WHERE m.id=?""",
                (cursor.lastrowid,),
            ).fetchone()
        return self._memory_dict(row)

    def update_memory(self, memory_id: int, user_id: int, *, content: str | None = None, kind: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agent_memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise DatabaseError("记忆不存在")
        memory = self._memory_dict(row)
        self._assert_memory_manager(memory, user_id)
        updates: dict[str, Any] = {"updated_at": time.time()}
        if content is not None:
            clean = str(content).strip()
            if not 1 <= len(clean) <= 4000:
                raise DatabaseError("记忆内容需为1至4000个字符")
            updates["content"] = clean
        if kind is not None:
            updates["kind"] = re.sub(r"[^a-z_]", "", str(kind).lower())[:40] or "note"
        assignments = ",".join(f"{name}=?" for name in updates)
        with self._connect() as conn:
            conn.execute(f"UPDATE agent_memories SET {assignments} WHERE id=?", (*updates.values(), memory_id))
            row = conn.execute(
                """SELECT m.*,u.username AS created_by_name FROM agent_memories m
                LEFT JOIN users u ON u.id=m.created_by WHERE m.id=?""",
                (memory_id,),
            ).fetchone()
        return self._memory_dict(row)

    def delete_memory(self, memory_id: int, user_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agent_memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise DatabaseError("记忆不存在")
        memory = self._memory_dict(row)
        self._assert_memory_manager(memory, user_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM agent_memories WHERE id=?", (memory_id,))

    def get_context_summary(self, agent_id: str, scope_type: str, scope_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_context_summaries WHERE agent_id=? AND scope_type=? AND scope_id=?",
                (agent_id, scope_type, scope_id),
            ).fetchone()
        return dict(row) if row else None

    def save_context_summary(
        self, agent_id: str, scope_type: str, scope_id: str, summary: str, last_message_id: int
    ) -> dict[str, Any]:
        clean = str(summary or "").strip()[:12_000]
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_context_summaries(agent_id,scope_type,scope_id,summary,last_message_id,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(agent_id,scope_type,scope_id) DO UPDATE SET
                summary=excluded.summary,last_message_id=MAX(last_message_id,excluded.last_message_id),updated_at=excluded.updated_at""",
                (agent_id, scope_type, scope_id, clean, int(last_message_id), now),
            )
        return {
            "agent_id": agent_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "summary": clean,
            "last_message_id": int(last_message_id),
            "updated_at": now,
        }
