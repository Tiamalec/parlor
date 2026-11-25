"""Group membership and moderation state."""

from __future__ import annotations

from typing import Any
from ..agent_config import DEFAULT_AGENT_ID
from .base import DatabaseError
import sqlite3
import time


class GroupsMixin:
    """Group membership and moderation state."""

    def list_groups(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT g.id,g.name,g.owner_id,
                       CASE WHEN gm.user_id IS NULL THEN 0 ELSE 1 END AS joined,
                       COALESCE(gm.role,'') AS member_role
                FROM groups g
                LEFT JOIN group_members gm ON gm.group_id=g.id AND gm.user_id=?
                ORDER BY g.id
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_group(self, name: str, owner_id: int) -> dict[str, Any]:
        name = name.strip()
        if not (2 <= len(name) <= 30):
            raise DatabaseError("群名称需为2至30个字符")
        now = time.time()
        try:
            with self._connect() as conn:
                cursor = conn.execute("INSERT INTO groups(name,owner_id,created_at) VALUES(?,?,?)", (name, owner_id, now))
                group_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO group_members(group_id,user_id,role,joined_at) VALUES(?,?,'owner',?)",
                    (group_id, owner_id, now),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO group_agents(group_id,agent_id,enabled,added_by,created_at) VALUES(?,?,1,?,?)",
                    (group_id, DEFAULT_AGENT_ID, owner_id, now),
                )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("群名称已存在") from exc
        return {"id": group_id, "name": name, "owner_id": owner_id, "joined": 1, "member_role": "owner"}

    def join_group(self, group_id: int, user_id: int) -> None:
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone()
            if not exists:
                raise DatabaseError("群不存在")
            conn.execute(
                "INSERT OR IGNORE INTO group_members(group_id,user_id,role,joined_at) VALUES(?,?,'member',?)",
                (group_id, user_id, time.time()),
            )

    def leave_group(self, group_id: int, user_id: int) -> None:
        with self._connect() as conn:
            group = conn.execute("SELECT name,owner_id FROM groups WHERE id=?", (group_id,)).fetchone()
            if not group:
                raise DatabaseError("群不存在")
            if group["name"] == "大厅":
                raise DatabaseError("不能退出大厅")
            if group["owner_id"] == user_id:
                raise DatabaseError("群主不能直接退出群")
            conn.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id))

    def group_members(self, group_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT u.id,u.username,u.display_name,gm.nickname,u.role AS global_role,gm.role AS group_role,
                       u.avatar_mime,u.avatar_version,
                       CASE WHEN mr.user_id IS NULL OR (mr.expires_at IS NOT NULL AND mr.expires_at<=?) THEN 0 ELSE 1 END AS muted
                FROM group_members gm
                JOIN users u ON u.id=gm.user_id
                LEFT JOIN mute_rules mr ON mr.group_id=gm.group_id AND mr.user_id=gm.user_id
                WHERE gm.group_id=? ORDER BY gm.role DESC,u.username COLLATE NOCASE
                """,
                (time.time(), group_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def is_group_member(self, group_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id)
            ).fetchone() is not None

    def can_manage_group(self, group_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.role,g.owner_id FROM users u CROSS JOIN groups g
                WHERE u.id=? AND g.id=?
                """,
                (user_id, group_id),
            ).fetchone()
        return bool(row and (row["role"] == "admin" or row["owner_id"] == user_id))

    def set_mute(self, group_id: int, target_id: int, actor_id: int, muted: bool, duration_minutes: int | None = None) -> None:
        if not self.can_manage_group(group_id, actor_id):
            raise DatabaseError("没有群管理权限")
        if target_id == actor_id:
            raise DatabaseError("不能禁言自己")
        with self._connect() as conn:
            if muted:
                if not self.is_group_member(group_id, target_id):
                    raise DatabaseError("目标用户不在群中")
                expires = time.time() + duration_minutes * 60 if duration_minutes else None
                conn.execute(
                    """
                    INSERT INTO mute_rules(group_id,user_id,muted_by,created_at,expires_at) VALUES(?,?,?,?,?)
                    ON CONFLICT(group_id,user_id) DO UPDATE SET muted_by=excluded.muted_by,created_at=excluded.created_at,expires_at=excluded.expires_at
                    """,
                    (group_id, target_id, actor_id, time.time(), expires),
                )
            else:
                conn.execute("DELETE FROM mute_rules WHERE group_id=? AND user_id=?", (group_id, target_id))

    def is_muted(self, group_id: int, user_id: int) -> bool:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute("SELECT expires_at FROM mute_rules WHERE group_id=? AND user_id=?", (group_id, user_id)).fetchone()
            if not row:
                return False
            if row["expires_at"] is not None and float(row["expires_at"]) <= now:
                conn.execute("DELETE FROM mute_rules WHERE group_id=? AND user_id=?", (group_id, user_id))
                return False
        return True
