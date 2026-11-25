"""Sticker catalog and delivery state."""

from __future__ import annotations

from typing import Any
from .base import DatabaseError
from pathlib import Path
import json
from ..stickers import normalize_sticker_agent_ids
import secrets
import sqlite3
import time


class StickersMixin:
    """Sticker catalog and delivery state."""

    @staticmethod
    def _sticker_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        try:
            tags = json.loads(str(data.get("tags") or "[]"))
        except json.JSONDecodeError:
            tags = []
        data["tags"] = tags if isinstance(tags, list) else []
        data["enabled"] = bool(data.get("enabled", 0))
        data["animated"] = bool(data.get("animated", 0))
        data["shared"] = bool(data.get("shared", 1))
        data["agent_ids"] = [str(item) for item in (data.get("agent_ids") or [])]
        return data

    @staticmethod
    def _sticker_agent_ids(
        conn: sqlite3.Connection, agent_ids: Any
    ) -> list[str]:
        checked = normalize_sticker_agent_ids(agent_ids)
        if not checked:
            return []
        placeholders = ",".join("?" for _ in checked)
        found = {
            str(row["id"])
            for row in conn.execute(
                f"SELECT id FROM agents WHERE id IN ({placeholders})", checked
            ).fetchall()
        }
        missing = [agent_id for agent_id in checked if agent_id not in found]
        if missing:
            raise DatabaseError(f"角色卡不存在：{', '.join(missing)}")
        return checked

    @staticmethod
    def _sticker_agent_map(
        conn: sqlite3.Connection, sticker_ids: list[str]
    ) -> dict[str, list[str]]:
        if not sticker_ids:
            return {}
        mapping: dict[str, list[str]] = {}
        for start in range(0, len(sticker_ids), 500):
            chunk = sticker_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""SELECT sticker_id,agent_id FROM sticker_agents
                WHERE sticker_id IN ({placeholders}) ORDER BY created_at,agent_id""",
                chunk,
            ).fetchall()
            for row in rows:
                mapping.setdefault(str(row["sticker_id"]), []).append(str(row["agent_id"]))
        return mapping

    def list_stickers(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        clause = " WHERE enabled=1" if enabled_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM stickers{clause} ORDER BY category COLLATE NOCASE,name COLLATE NOCASE"
            ).fetchall()
            mapping = self._sticker_agent_map(conn, [str(row["id"]) for row in rows])
        stickers = [self._sticker_dict(row) for row in rows]
        for sticker in stickers:
            sticker["agent_ids"] = mapping.get(str(sticker["id"]), [])
        return stickers

    def list_stickers_for_agent(
        self, agent_id: str, *, enabled_only: bool = True
    ) -> list[dict[str, Any]]:
        clauses = [
            "(s.shared=1 OR EXISTS (SELECT 1 FROM sticker_agents sa WHERE sa.sticker_id=s.id AND sa.agent_id=?))"
        ]
        if enabled_only:
            clauses.append("s.enabled=1")
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT s.* FROM stickers s WHERE {' AND '.join(clauses)} "
                "ORDER BY s.category COLLATE NOCASE,s.name COLLATE NOCASE",
                (str(agent_id),),
            ).fetchall()
            mapping = self._sticker_agent_map(conn, [str(row["id"]) for row in rows])
        stickers = [self._sticker_dict(row) for row in rows]
        for sticker in stickers:
            sticker["agent_ids"] = mapping.get(str(sticker["id"]), [])
        return stickers

    def get_sticker(self, sticker_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM stickers WHERE id=?", (str(sticker_id),)).fetchone()
            mapping = (
                self._sticker_agent_map(conn, [str(row["id"])]) if row else {}
            )
        if not row:
            raise DatabaseError("表情包不存在")
        sticker = self._sticker_dict(row)
        sticker["agent_ids"] = mapping.get(str(sticker["id"]), [])
        return sticker

    def create_sticker(
        self,
        *,
        name: str,
        category: str,
        tags: list[str],
        path: str,
        mime: str,
        animated: bool = False,
        enabled: bool = True,
        shared: bool = True,
        agent_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        sticker_id = f"sticker_{secrets.token_hex(8)}"
        now = time.time()
        try:
            with self._connect() as conn:
                checked_agents = self._sticker_agent_ids(conn, agent_ids or [])
                conn.execute(
                    """INSERT INTO stickers(id,name,category,tags,path,mime,animated,enabled,shared,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        sticker_id,
                        name,
                        category,
                        json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
                        path,
                        mime,
                        int(bool(animated)),
                        int(bool(enabled)),
                        int(bool(shared)),
                        now,
                        now,
                    ),
                )
                conn.executemany(
                    "INSERT INTO sticker_agents(sticker_id,agent_id,created_at) VALUES(?,?,?)",
                    [(sticker_id, agent_id, now) for agent_id in checked_agents],
                )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("表情包名称已存在") from exc
        return self.get_sticker(sticker_id)

    def update_sticker(self, sticker_id: str, values: dict[str, Any]) -> dict[str, Any]:
        self.get_sticker(sticker_id)
        values = dict(values)
        has_agent_ids = "agent_ids" in values
        raw_agent_ids = values.pop("agent_ids", None)
        allowed = {
            "name", "category", "tags", "path", "mime", "animated", "enabled", "shared",
        }
        unknown = set(values) - allowed
        if unknown:
            raise DatabaseError(f"不支持的表情包字段：{', '.join(sorted(unknown))}")
        updates: dict[str, Any] = {}
        if "name" in values:
            updates["name"] = str(values["name"])
        if "category" in values:
            updates["category"] = str(values["category"])
        if "tags" in values:
            updates["tags"] = json.dumps(
                list(values["tags"] or []), ensure_ascii=False, separators=(",", ":")
            )
        if "path" in values:
            updates["path"] = str(values["path"])
        if "mime" in values:
            updates["mime"] = str(values["mime"])
        if "animated" in values:
            updates["animated"] = int(bool(values["animated"]))
        if "enabled" in values:
            updates["enabled"] = int(bool(values["enabled"]))
        if "shared" in values:
            updates["shared"] = int(bool(values["shared"]))
        if not updates and not has_agent_ids:
            return self.get_sticker(sticker_id)
        now = time.time()
        updates["updated_at"] = now
        try:
            with self._connect() as conn:
                checked_agents = (
                    self._sticker_agent_ids(conn, raw_agent_ids)
                    if has_agent_ids
                    else None
                )
                assignments = ",".join(f"{name}=?" for name in updates)
                conn.execute(
                    f"UPDATE stickers SET {assignments} WHERE id=?",
                    (*updates.values(), sticker_id),
                )
                if checked_agents is not None:
                    conn.execute("DELETE FROM sticker_agents WHERE sticker_id=?", (sticker_id,))
                    conn.executemany(
                        "INSERT INTO sticker_agents(sticker_id,agent_id,created_at) VALUES(?,?,?)",
                        [(sticker_id, agent_id, now) for agent_id in checked_agents],
                    )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("表情包名称已存在") from exc
        return self.get_sticker(sticker_id)

    def delete_sticker(self, sticker_id: str) -> str:
        sticker = self.get_sticker(sticker_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM stickers WHERE id=?", (sticker_id,))
        return str(sticker.get("path") or "")

    def sticker_file(self, sticker_id: str) -> dict[str, Any]:
        sticker = self.get_sticker(sticker_id)
        stored = Path(str(sticker.get("path") or ""))
        resolved = stored if stored.is_absolute() else (self.uploads_dir / stored)
        return {**sticker, "resolved_path": resolved}

    def sticker_auto_enabled(self, agent_id: str, scope_type: str, scope_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT auto_sticker,enabled FROM agents WHERE id=?", (str(agent_id),)
            ).fetchone()
            if not row or not row["enabled"] or not row["auto_sticker"]:
                return False
            if scope_type == "group":
                joined = conn.execute(
                    """SELECT ga.auto_sticker FROM group_agents ga WHERE ga.group_id=? AND ga.agent_id=?""",
                    (int(scope_id), str(agent_id)),
                ).fetchone()
                if not joined or not joined["auto_sticker"]:
                    return False
        return True

    def sticker_delivery_state(self, agent_id: str, scope_type: str, scope_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT last_sent_at,last_sticker_id FROM agent_sticker_state
                WHERE agent_id=? AND scope_type=? AND scope_id=?""",
                (str(agent_id), scope_type, str(scope_id)),
            ).fetchone()
        return dict(row) if row else {"last_sent_at": 0.0, "last_sticker_id": None}

    def mark_sticker_sent(
        self, agent_id: str, scope_type: str, scope_id: str, sticker_id: str, now: float | None = None
    ) -> None:
        stamp = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_sticker_state(agent_id,scope_type,scope_id,last_sent_at,last_sticker_id)
                VALUES(?,?,?,?,?)
                ON CONFLICT(agent_id,scope_type,scope_id) DO UPDATE SET
                last_sent_at=excluded.last_sent_at,last_sticker_id=excluded.last_sticker_id""",
                (str(agent_id), scope_type, str(scope_id), stamp, sticker_id),
            )
