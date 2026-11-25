"""Schedules, Agent runs, tool calls and admin audit."""

from __future__ import annotations

from typing import Any
from ..agent_config import DEFAULT_AGENT_ID
from .base import DatabaseError
import time


class ActivityMixin:
    """Schedules, Agent runs, tool calls and admin audit."""

    def set_schedule(self, group_id: int, actor_id: int, interval_minutes: int, enabled: bool = True) -> dict[str, Any]:
        if not self.can_manage_group(group_id, actor_id):
            raise DatabaseError("没有设置定时总结的权限")
        interval_minutes = int(interval_minutes)
        if not 1 <= interval_minutes <= 1440:
            raise DatabaseError("定时周期需为1至1440分钟")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_schedules(group_id,interval_minutes,enabled,last_message_id,next_run_at,updated_by,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(group_id) DO UPDATE SET interval_minutes=excluded.interval_minutes,enabled=excluded.enabled,
                next_run_at=excluded.next_run_at,updated_by=excluded.updated_by,updated_at=excluded.updated_at
                """,
                (group_id, interval_minutes, int(enabled), 0, now + interval_minutes * 60, actor_id, now),
            )
        return {"group_id": group_id, "interval_minutes": interval_minutes, "enabled": enabled, "next_run_at": now + interval_minutes * 60}

    def get_schedule(self, group_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT group_id,interval_minutes,enabled,next_run_at FROM agent_schedules WHERE group_id=?",
                (group_id,),
            ).fetchone()
        if not row:
            return {
                "group_id": group_id,
                "interval_minutes": 60,
                "enabled": False,
                "next_run_at": None,
            }
        schedule = dict(row)
        schedule["enabled"] = bool(schedule["enabled"])
        return schedule

    def due_schedules(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM agent_schedules WHERE enabled=1 AND next_run_at<=?", (now,)).fetchall()
        return [dict(row) for row in rows]

    def advance_schedule(self, group_id: int, *, last_message_id: int | None = None) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT interval_minutes FROM agent_schedules WHERE group_id=?", (group_id,)).fetchone()
            if not row:
                return
            if last_message_id is None:
                conn.execute("UPDATE agent_schedules SET next_run_at=? WHERE group_id=?", (time.time() + row["interval_minutes"] * 60, group_id))
            else:
                conn.execute(
                    "UPDATE agent_schedules SET last_message_id=MAX(last_message_id,?),next_run_at=? WHERE group_id=?",
                    (last_message_id, time.time() + row["interval_minutes"] * 60, group_id),
                )

    def create_agent_run(
        self,
        task_type: str,
        conversation_type: str,
        conversation_id: str,
        requested_by: int | None,
        *,
        agent_id: str | None = None,
        model_profile_id: str | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO agent_runs(
                    agent_id,task_type,conversation_type,conversation_id,model_profile_id,requested_by,status,started_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    agent_id or DEFAULT_AGENT_ID,
                    task_type,
                    conversation_type,
                    conversation_id,
                    model_profile_id,
                    requested_by,
                    "running",
                    time.time(),
                ),
            )
            return int(cursor.lastrowid)

    def finish_agent_run(self, run_id: int, status: str, *, result_message_id: int | None = None, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_runs SET status=?,finished_at=?,result_message_id=?,error=? WHERE id=?",
                (status, time.time(), result_message_id, error, run_id),
            )

    def start_tool_call(
        self,
        run_id: int,
        tool_name: str,
        conversation_type: str,
        conversation_id: str,
        input_summary: str,
        *,
        agent_id: str | None = None,
        tool_mode: str = "auto",
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO tool_calls(
                    run_id,agent_id,tool_name,conversation_type,conversation_id,input_summary,tool_mode,status,started_at
                ) VALUES(?,?,?,?,?,?,?,'running',?)""",
                (
                    run_id,
                    agent_id or DEFAULT_AGENT_ID,
                    tool_name,
                    conversation_type,
                    conversation_id,
                    input_summary[:500],
                    tool_mode,
                    time.time(),
                ),
            )
            return int(cursor.lastrowid)

    def finish_tool_call(self, tool_id: int, status: str, *, result_count: int | None = None, error: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tool_calls SET status=?,result_count=?,finished_at=?,error=? WHERE id=?",
                (status, result_count, time.time(), error, tool_id),
            )
            row = conn.execute(
                """
                SELECT tc.*,ar.requested_by,u.username AS requested_by_name,a.name AS agent_name
                FROM tool_calls tc
                JOIN agent_runs ar ON ar.id=tc.run_id
                LEFT JOIN users u ON u.id=ar.requested_by
                LEFT JOIN agents a ON a.id=tc.agent_id
                WHERE tc.id=?
                """,
                (tool_id,),
            ).fetchone()
        return dict(row)

    def recent_tool_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tc.*,ar.requested_by,u.username AS requested_by_name,a.name AS agent_name
                FROM tool_calls tc
                JOIN agent_runs ar ON ar.id=tc.run_id
                LEFT JOIN users u ON u.id=ar.requested_by
                LEFT JOIN agents a ON a.id=tc.agent_id
                ORDER BY tc.id DESC LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_agent_activity(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ar.*,u.username AS requested_by_name,a.name AS agent_name
                FROM agent_runs ar
                LEFT JOIN users u ON u.id=ar.requested_by
                LEFT JOIN agents a ON a.id=ar.agent_id
                ORDER BY ar.id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        allowed: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            try:
                self._assert_access(
                    user_id,
                    str(record["conversation_type"]),
                    str(record["conversation_id"]),
                )
            except DatabaseError:
                continue
            allowed.append(record)
        return allowed

    def system_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            users = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            groups = conn.execute("SELECT COUNT(*) AS count FROM groups").fetchone()
        return {
            "users": int(users["count"]) if users else 0,
            "groups": int(groups["count"]) if groups else 0,
        }

    def list_agent_activity_admin(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ar.*,u.username AS requested_by_name,a.name AS agent_name
                FROM agent_runs ar
                LEFT JOIN users u ON u.id=ar.requested_by
                LEFT JOIN agents a ON a.id=ar.agent_id
                ORDER BY ar.id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_users_admin(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT u.id,u.username,u.role,u.created_at,COUNT(gm.group_id) AS group_count
                FROM users u
                LEFT JOIN group_members gm ON gm.user_id=u.id
                GROUP BY u.id,u.username,u.role,u.created_at
                ORDER BY u.username COLLATE NOCASE LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_groups_admin(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT g.id,g.name,g.owner_id,u.username AS owner_name,g.created_at,
                       COUNT(gm.user_id) AS member_count
                FROM groups g
                LEFT JOIN users u ON u.id=g.owner_id
                LEFT JOIN group_members gm ON gm.group_id=g.id
                GROUP BY g.id,g.name,g.owner_id,u.username,g.created_at
                ORDER BY g.id LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_admin_audit(
        self,
        actor_id: int | None,
        action: str,
        resource: str,
        detail: str,
        status: str,
        source_ip: str = "",
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO admin_audit_log(actor_id,action,resource,detail,status,source_ip,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    actor_id,
                    str(action)[:80],
                    str(resource)[:160],
                    str(detail)[:1000],
                    str(status)[:40],
                    str(source_ip)[:80],
                    time.time(),
                ),
            )
            return int(cursor.lastrowid)

    def list_admin_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT aal.*,u.username AS actor_name
                FROM admin_audit_log aal
                LEFT JOIN users u ON u.id=aal.actor_id
                ORDER BY aal.id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]
