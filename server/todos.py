"""Account-scoped personal todo operations shared by the wire API and tools."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import unicodedata
from typing import Any

from .database import Database, DatabaseError


PRIORITIES = {"low", "normal", "high"}
SOURCES = {"manual", "agent_extracted", "chat_message"}
ACTORS = {"manual", "agent"}
TODO_LIST_LIMIT = 200


def normalize_todo_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"[\s\-—_，。,.!?！？:：;；、（）()\[\]【】\"'“”‘’`*#]+", "", text)
    text = re.sub(
        r"^(?:去)?(?:参加|出席|准备|完成|处理|跟进|安排|提交|整理)+"
        r"(?:一场|一个|一次)?",
        "",
        text,
    )
    return text[:300]


def extracted_todo_key(
    owner_id: int,
    title: str,
    conversation_type: str,
    conversation_id: str,
    source_message_id: int | None,
    due_at: float | None = None,
) -> str:
    material = "|".join(
        (
            str(owner_id),
            normalize_todo_title(title),
            conversation_type,
            conversation_id,
            str(source_message_id or ""),
            str(due_at if due_at is not None else ""),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class TodoService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["completed"] = item["completed_at"] is not None
        item["manual_edited"] = int(item.get("manual_revision") or 0) > 0
        return item

    @staticmethod
    def _text(value: Any, label: str, limit: int, *, required: bool = False) -> str:
        if not isinstance(value, str):
            raise DatabaseError(f"{label}格式不正确")
        text = value.strip()
        if (required and not text) or len(text) > limit:
            raise DatabaseError(f"{label}长度不正确")
        return text

    @staticmethod
    def _due(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DatabaseError("截止时间格式不正确")
        due = float(value)
        if not math.isfinite(due) or due < 0 or due > 253402300799:
            raise DatabaseError("截止时间超出范围")
        return due

    @staticmethod
    def _revision(conn: sqlite3.Connection, owner_id: int) -> int:
        conn.execute(
            "INSERT INTO todo_revisions(owner_id,revision) VALUES (?,1) "
            "ON CONFLICT(owner_id) DO UPDATE SET revision=revision+1",
            (owner_id,),
        )
        return int(
            conn.execute(
                "SELECT revision FROM todo_revisions WHERE owner_id=?", (owner_id,)
            ).fetchone()[0]
        )

    @staticmethod
    def _revision_value(conn: sqlite3.Connection, owner_id: int) -> int:
        row = conn.execute(
            "SELECT revision FROM todo_revisions WHERE owner_id=?", (owner_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def _get_with_conn(
        self, conn: sqlite3.Connection, owner_id: int, todo_id: int
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM personal_todos WHERE id=? AND owner_id=?",
            (todo_id, owner_id),
        ).fetchone()
        if not row:
            raise DatabaseError("待办不存在")
        return row

    def get(self, owner_id: int, todo_id: int) -> dict[str, Any]:
        with self.database._connect() as conn:
            row = self._get_with_conn(conn, owner_id, todo_id)
            revision = self._revision_value(conn, owner_id)
        return {"todo": self._row(row), "revision": revision}

    def list(
        self,
        owner_id: int,
        *,
        completed: bool | None = None,
        due_from: float | None = None,
        due_before: float | None = None,
        priority: str | None = None,
        query: str = "",
        sort: str = "due",
        limit: int = TODO_LIST_LIMIT,
    ) -> dict[str, Any]:
        if sort not in {"due", "priority"}:
            raise DatabaseError("排序方式不支持")
        if priority not in {None, "all", *PRIORITIES}:
            raise DatabaseError("优先级不支持")
        clauses = ["owner_id=?"]
        values: list[Any] = [owner_id]
        if completed is not None:
            clauses.append("completed_at IS " + ("NOT NULL" if completed else "NULL"))
        if due_from is not None:
            clauses.append("due_at>=?")
            values.append(self._due(due_from))
        if due_before is not None:
            clauses.append("due_at<?")
            values.append(self._due(due_before))
        if priority and priority != "all":
            clauses.append("priority=?")
            values.append(priority)
        checked_query = self._text(query, "搜索内容", 200) if query else ""
        if checked_query:
            clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            escaped = (
                checked_query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            values.extend((f"%{escaped}%", f"%{escaped}%"))
        checked_limit = max(1, min(int(limit), TODO_LIST_LIMIT))
        order = (
            "CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
            "due_at IS NULL, due_at, id"
            if sort == "priority"
            else "due_at IS NULL, due_at, CASE priority WHEN 'high' THEN 0 "
            "WHEN 'normal' THEN 1 ELSE 2 END, id"
        )
        values.append(checked_limit)
        with self.database._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM personal_todos WHERE {' AND '.join(clauses)} "
                f"ORDER BY {order} LIMIT ?",
                values,
            ).fetchall()
            revision = self._revision_value(conn, owner_id)
        return {"todos": [self._row(row) for row in rows], "revision": revision}

    def find_matches(
        self,
        owner_id: int,
        title: str,
        *,
        source_conversation_type: str | None = None,
        source_conversation_id: str | None = None,
        source_message_ids: list[int] | None = None,
        due_at: float | None = None,
        include_title_only: bool = False,
    ) -> list[dict[str, Any]]:
        normalized = normalize_todo_title(title)
        if not normalized:
            return []
        source_ids = {int(value) for value in (source_message_ids or []) if value}
        with self.database._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM personal_todos WHERE owner_id=? ORDER BY id",
                (owner_id,),
            ).fetchall()
        matches: list[dict[str, Any]] = []
        for row in rows:
            item = self._row(row)
            if normalize_todo_title(item.get("title")) != normalized:
                continue
            same_source = (
                source_conversation_type is not None
                and str(item.get("source_conversation_type") or "")
                == source_conversation_type
                and str(item.get("source_conversation_id") or "")
                == str(source_conversation_id or "")
            )
            same_message = bool(
                source_ids and int(item.get("source_message_id") or 0) in source_ids
            )
            due_matches = due_at is None or item.get("due_at") is None or (
                math.isclose(float(item["due_at"]), float(due_at))
            )
            if include_title_only or (same_message and due_matches) or (
                not source_ids and same_source
            ):
                matches.append(item)
        return matches

    def create(
        self,
        owner_id: int,
        *,
        title: str,
        description: str = "",
        due_at: float | None = None,
        priority: str = "normal",
        source_type: str = "manual",
        source_conversation_type: str | None = None,
        source_conversation_id: str | None = None,
        source_message_id: int | None = None,
        actor: str = "manual",
        agent_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        title = self._text(title, "任务名称", 200, required=True)
        description = self._text(description, "任务描述", 4000)
        due_at = self._due(due_at)
        if priority not in PRIORITIES or source_type not in SOURCES:
            raise DatabaseError("优先级或来源类型不支持")
        if actor not in ACTORS:
            raise DatabaseError("修改来源不支持")
        if actor == "agent" and source_type == "manual":
            raise DatabaseError("Agent 创建的待办必须保留来源")
        if source_type == "manual" and any(
            (source_conversation_type, source_conversation_id, source_message_id)
        ):
            raise DatabaseError("手动待办不能设置消息来源")
        if source_type != "manual":
            if source_conversation_type not in {"group", "private"} or not source_conversation_id:
                raise DatabaseError("来源会话不完整")
            self.database._assert_access(
                owner_id, source_conversation_type, source_conversation_id
            )
        if source_message_id is not None:
            if isinstance(source_message_id, bool):
                raise DatabaseError("关联消息格式不正确")
            source_message_id = int(source_message_id)
            message = self.database.authorized_message(owner_id, source_message_id)
            if (message["conversation_type"], message["conversation_id"]) != (
                source_conversation_type,
                source_conversation_id,
            ):
                raise DatabaseError("关联消息不属于来源会话")
        normalized_title = normalize_todo_title(title)
        if not normalized_title:
            raise DatabaseError("任务名称格式不正确")
        if source_type != "manual" and source_message_id is not None and not idempotency_key:
            idempotency_key = extracted_todo_key(
                owner_id,
                title,
                str(source_conversation_type or ""),
                str(source_conversation_id or ""),
                source_message_id,
                due_at,
            )
        now = time.time()
        with self.database._connect() as conn:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM personal_todos WHERE owner_id=? AND idempotency_key=?",
                    (owner_id, idempotency_key),
                ).fetchone()
                if existing:
                    return {
                        "todo": self._row(existing),
                        "revision": self._revision_value(conn, owner_id),
                        "created": False,
                        "matched": True,
                    }
                legacy_key = extracted_todo_key(
                    owner_id,
                    title,
                    str(source_conversation_type or ""),
                    str(source_conversation_id or ""),
                    source_message_id,
                )
                if legacy_key != idempotency_key:
                    existing = conn.execute(
                        "SELECT * FROM personal_todos WHERE owner_id=? AND idempotency_key=?",
                        (owner_id, legacy_key),
                    ).fetchone()
                    if existing:
                        due_matches = (
                            existing["due_at"] is None
                            or due_at is None
                            or math.isclose(float(existing["due_at"]), float(due_at))
                        )
                        if due_matches:
                            return {
                                "todo": self._row(existing),
                                "revision": self._revision_value(conn, owner_id),
                                "created": False,
                                "matched": True,
                            }
            try:
                cursor = conn.execute(
                    """INSERT INTO personal_todos
                    (owner_id,title,normalized_title,description,due_at,priority,source_type,
                     source_conversation_type,source_conversation_id,source_message_id,
                     idempotency_key,version,manual_revision,last_modified_by,created_by_agent_id,
                     created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                    (
                        owner_id,
                        title,
                        normalized_title,
                        description,
                        due_at,
                        priority,
                        source_type,
                        source_conversation_type,
                        source_conversation_id,
                        source_message_id,
                        idempotency_key,
                        1 if actor == "manual" else 0,
                        actor,
                        agent_id if actor == "agent" else None,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                if idempotency_key:
                    existing = conn.execute(
                        "SELECT * FROM personal_todos WHERE owner_id=? AND idempotency_key=?",
                        (owner_id, idempotency_key),
                    ).fetchone()
                    if existing:
                        return {
                            "todo": self._row(existing),
                            "revision": self._revision_value(conn, owner_id),
                            "created": False,
                            "matched": True,
                        }
                raise DatabaseError("待办创建冲突，请刷新后重试")
            revision = self._revision(conn, owner_id)
            row = conn.execute(
                "SELECT * FROM personal_todos WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return {
            "todo": self._row(row),
            "revision": revision,
            "created": True,
            "matched": False,
        }

    def update(
        self,
        owner_id: int,
        todo_id: int,
        *,
        actor: str = "manual",
        allow_manual_override: bool = False,
        expected_version: int | None = None,
        **changes: Any,
    ) -> dict[str, Any]:
        allowed = {"title", "description", "due_at", "priority", "completed"}
        if not changes or set(changes) - allowed:
            raise DatabaseError("待办更新字段不支持")
        if actor not in ACTORS:
            raise DatabaseError("修改来源不支持")
        values: dict[str, Any] = {}
        if "title" in changes:
            title = self._text(changes["title"], "任务名称", 200, required=True)
            values["title"] = title
            values["normalized_title"] = normalize_todo_title(title)
        if "description" in changes:
            values["description"] = self._text(
                changes["description"], "任务描述", 4000
            )
        if "due_at" in changes:
            values["due_at"] = self._due(changes["due_at"])
        if "priority" in changes:
            if changes["priority"] not in PRIORITIES:
                raise DatabaseError("优先级不支持")
            values["priority"] = changes["priority"]
        if "completed" in changes:
            if not isinstance(changes["completed"], bool):
                raise DatabaseError("完成状态格式不正确")
            values["completed_at"] = time.time() if changes["completed"] else None
        with self.database._connect() as conn:
            current = self._get_with_conn(conn, owner_id, todo_id)
            current_item = self._row(current)
            if (
                actor == "agent"
                and current_item.get("manual_edited")
                and not allow_manual_override
            ):
                raise DatabaseError("该待办已由用户手动修改，Agent 需要明确更新授权")
            if expected_version is not None and int(current["version"]) != int(
                expected_version
            ):
                raise DatabaseError("待办已更新，请刷新后重试")
            unchanged = all(
                current_item.get(key) == value
                for key, value in values.items()
                if key != "completed_at"
            )
            if "completed_at" in values:
                unchanged = unchanged and bool(
                    current_item.get("completed") == (values["completed_at"] is not None)
                )
            if unchanged:
                return {
                    "todo": current_item,
                    "revision": self._revision_value(conn, owner_id),
                    "updated": False,
                }
            values["updated_at"] = time.time()
            values["version"] = int(current["version"]) + 1
            values["last_modified_by"] = actor
            if actor == "manual":
                values["manual_revision"] = int(current["manual_revision"] or 0) + 1
            assignments = ", ".join(f"{key}=?" for key in values)
            params = [*values.values(), todo_id, owner_id]
            where = "id=? AND owner_id=?"
            if expected_version is not None:
                where += " AND version=?"
                params.append(int(expected_version))
            cursor = conn.execute(
                f"UPDATE personal_todos SET {assignments} WHERE {where}", params
            )
            if not cursor.rowcount:
                raise DatabaseError("待办已更新，请刷新后重试")
            revision = self._revision(conn, owner_id)
            row = self._get_with_conn(conn, owner_id, todo_id)
        return {"todo": self._row(row), "revision": revision, "updated": True}

    def complete(
        self,
        owner_id: int,
        todo_id: int,
        completed: bool = True,
        *,
        actor: str = "manual",
        allow_manual_override: bool = False,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        return self.update(
            owner_id,
            todo_id,
            actor=actor,
            allow_manual_override=allow_manual_override,
            expected_version=expected_version,
            completed=completed,
        )

    def reopen(
        self,
        owner_id: int,
        todo_id: int,
        *,
        actor: str = "manual",
        allow_manual_override: bool = False,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        return self.complete(
            owner_id,
            todo_id,
            False,
            actor=actor,
            allow_manual_override=allow_manual_override,
            expected_version=expected_version,
        )

    def delete(
        self,
        owner_id: int,
        todo_id: int,
        *,
        actor: str = "manual",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        with self.database._connect() as conn:
            current = self._get_with_conn(conn, owner_id, todo_id)
            if expected_version is not None and int(current["version"]) != int(
                expected_version
            ):
                raise DatabaseError("待办已更新，请刷新后重试")
            cursor = conn.execute(
                "DELETE FROM personal_todos WHERE id=? AND owner_id=?",
                (todo_id, owner_id),
            )
            if not cursor.rowcount:
                raise DatabaseError("待办不存在")
            revision = self._revision(conn, owner_id)
        return {
            "todo_id": todo_id,
            "owner_id": owner_id,
            "revision": revision,
            "deleted_by": actor,
        }

    def resolve_message(self, owner_id: int, todo_id: int) -> dict[str, Any]:
        with self.database._connect() as conn:
            row = self._get_with_conn(conn, owner_id, todo_id)
        if row["source_message_id"] is None:
            raise DatabaseError("待办没有可跳转的消息")
        message = self.database.authorized_message(owner_id, int(row["source_message_id"]))
        return {
            "todo_id": todo_id,
            "message_id": message["id"],
            "conversation_type": message["conversation_type"],
            "conversation_id": message["conversation_id"],
        }
