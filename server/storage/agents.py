"""Agent cards, avatars, tools and group bindings."""

from __future__ import annotations

from typing import Any
from ..agent_config import BUILTIN_AGENT_IDS, BUILTIN_AGENT_PRESETS, DEFAULT_AGENT_CARD, DEFAULT_AGENT_ID, normalize_agent_card, normalize_agent_description, normalize_agent_name
from ..tools import CONFIGURABLE_AGENT_TOOL_NAMES, DEFAULT_AGENT_TOOL_NAMES, require_tool
from .base import DatabaseError
from .constants import PRESET_AVATAR_DIR
from pathlib import Path
import json
import secrets
import sqlite3
import time


class AgentsMixin:
    """Agent cards, avatars, tools and group bindings."""

    @staticmethod
    def agent_member_id(agent_id: str) -> str:
        return DEFAULT_AGENT_ID if agent_id == DEFAULT_AGENT_ID else f"agent:{agent_id}"

    @staticmethod
    def _agent_dict(
        row: sqlite3.Row | dict[str, Any], *, user_id: int | None = None, actor_is_admin: bool = False
    ) -> dict[str, Any]:
        data = dict(row)
        try:
            card = normalize_agent_card(json.loads(str(data.get("card") or "{}")))
        except (ValueError, TypeError, json.JSONDecodeError):
            card = normalize_agent_card(DEFAULT_AGENT_CARD)
        data["card"] = card
        data["enabled"] = bool(data.get("enabled", 0))
        data["memory_enabled"] = bool(data.get("memory_enabled", 0))
        data["auto_sticker"] = bool(data.get("auto_sticker", 1))
        if "group_auto_sticker" in data:
            data["group_auto_sticker"] = bool(data["group_auto_sticker"])
        data["builtin"] = data.get("owner_id") is None and data["id"] != DEFAULT_AGENT_ID
        data["member_id"] = AgentsMixin.agent_member_id(str(data["id"]))
        data["kind"] = "agent"
        data["username"] = data["name"]
        data["display_name"] = data["name"]
        data["can_manage"] = user_id is not None and (
            data.get("owner_id") == user_id or actor_is_admin
        )
        data.pop("owner_role", None)
        return data

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT a.*,u.role AS owner_role FROM agents a
                LEFT JOIN users u ON u.id=a.owner_id WHERE a.id=?""",
                (str(agent_id),),
            ).fetchone()
        if not row:
            raise DatabaseError("Agent 不存在")
        return self._agent_dict(row)

    def list_agents(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            actor = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
            rows = conn.execute(
                """SELECT a.*,u.role AS owner_role,
                    (SELECT COUNT(*) FROM group_agents ga WHERE ga.agent_id=a.id AND ga.enabled=1) AS group_count
                FROM agents a LEFT JOIN users u ON u.id=a.owner_id
                ORDER BY CASE WHEN a.id='agent' THEN 0 ELSE 1 END,a.name COLLATE NOCASE"""
            ).fetchall()
        admin = bool(actor and actor["role"] == "admin")
        agents: list[dict[str, Any]] = []
        for row in rows:
            item = self._agent_dict(row, user_id=user_id, actor_is_admin=admin)
            if not item["enabled"] and not (admin or item["owner_id"] == user_id):
                continue
            item["group_count"] = int(item.get("group_count", 0) or 0)
            item["owned"] = item.get("owner_id") == user_id
            agents.append(item)
        return agents

    def create_agent(
        self,
        owner_id: int | None,
        *,
        name: str,
        description: str = "",
        card: Any = None,
        model_profile_id: str | None = None,
        enabled: bool = True,
        memory_enabled: bool = True,
        auto_sticker: bool = True,
        preset: bool = False,
    ) -> dict[str, Any]:
        checked_name = normalize_agent_name(name)
        checked_description = normalize_agent_description(description)
        checked_card = normalize_agent_card(card)
        profile_id = str(model_profile_id or "").strip()[:64] or None
        agent_id = f"agent_{secrets.token_hex(8)}"
        stored_owner_id = None if preset else owner_id
        now = time.time()
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO agents(
                        id,owner_id,name,description,card,model_profile_id,enabled,memory_enabled,auto_sticker,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        agent_id,
                        stored_owner_id,
                        checked_name,
                        checked_description,
                        json.dumps(checked_card, ensure_ascii=False, separators=(",", ":")),
                        profile_id,
                        int(bool(enabled)),
                        int(bool(memory_enabled)),
                        int(bool(auto_sticker)),
                        now,
                        now,
                    ),
                )
                for tool_name in DEFAULT_AGENT_TOOL_NAMES:
                    conn.execute(
                        "INSERT INTO agent_tools(agent_id,tool_name,enabled,updated_at) VALUES(?,?,1,?)",
                        (agent_id, tool_name, now),
                    )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("Agent 名称已存在") from exc
        created = self.get_agent(agent_id)
        created["can_manage"] = True
        created["owned"] = owner_id is not None and not preset
        return created

    def _assert_agent_manager(self, agent_id: str, actor_id: int) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        with self._connect() as conn:
            actor = conn.execute("SELECT role FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or not (actor["role"] == "admin" or agent.get("owner_id") == actor_id):
            raise DatabaseError("没有管理该 Agent 的权限")
        return agent

    def update_agent(self, agent_id: str, actor_id: int, values: dict[str, Any]) -> dict[str, Any]:
        agent = self._assert_agent_manager(agent_id, actor_id)
        allowed = {
            "name", "description", "card", "model_profile_id", "enabled", "memory_enabled",
            "auto_sticker",
        }
        unknown = set(values) - allowed
        if unknown:
            raise DatabaseError(f"不支持的 Agent 字段：{', '.join(sorted(unknown))}")
        updates: dict[str, Any] = {}
        if "name" in values:
            updates["name"] = normalize_agent_name(values["name"])
        if "description" in values:
            updates["description"] = normalize_agent_description(values["description"])
        if "card" in values:
            updates["card"] = json.dumps(normalize_agent_card(values["card"]), ensure_ascii=False, separators=(",", ":"))
        if "model_profile_id" in values:
            updates["model_profile_id"] = str(values.get("model_profile_id") or "").strip()[:64] or None
        if "enabled" in values:
            updates["enabled"] = int(bool(values["enabled"]))
        if "memory_enabled" in values:
            updates["memory_enabled"] = int(bool(values["memory_enabled"]))
        if "auto_sticker" in values:
            updates["auto_sticker"] = int(bool(values["auto_sticker"]))
        if not updates:
            return agent
        updates["updated_at"] = time.time()
        assignments = ",".join(f"{name}=?" for name in updates)
        try:
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE agents SET {assignments} WHERE id=?",
                    (*updates.values(), agent_id),
                )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("Agent 名称已存在") from exc
        return self.get_agent(agent_id)

    def delete_agent(self, agent_id: str, actor_id: int) -> None:
        if agent_id == DEFAULT_AGENT_ID:
            raise DatabaseError("默认 Agent 不能删除，可以停用")
        if agent_id in BUILTIN_AGENT_IDS:
            raise DatabaseError("预设 Agent 不能删除，可以停用")
        self._assert_agent_manager(agent_id, actor_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))

    def set_agent_avatar(self, agent_id: str, path: str | None, mime: str | None) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        stored_path = path
        if path:
            try:
                stored_path = Path(path).resolve().relative_to(self.uploads_dir.resolve()).as_posix()
            except ValueError:
                stored_path = path
        with self._connect() as conn:
            conn.execute(
                "UPDATE agents SET avatar_path=?,avatar_mime=?,avatar_version=avatar_version+1,updated_at=? WHERE id=?",
                (stored_path, mime, time.time(), agent_id),
            )
        result = self.get_agent(agent_id)
        result["previous_avatar_path"] = agent.get("avatar_path")
        return result

    def _seed_preset_avatars(self, conn: sqlite3.Connection, now: float) -> None:
        """Attach bundled portraits to untouched built-in role cards."""
        avatar_dir = self.uploads_dir / "agent-avatars"
        for preset in BUILTIN_AGENT_PRESETS:
            agent_id = str(preset["id"])
            source = PRESET_AVATAR_DIR / f"{agent_id}.png"
            if not source.is_file():
                continue
            row = conn.execute(
                "SELECT avatar_version,avatar_path FROM agents WHERE id=?",
                (agent_id,),
            ).fetchone()
            if row is None or int(row["avatar_version"] or 0) > 0 or row["avatar_path"]:
                continue
            avatar_dir.mkdir(parents=True, exist_ok=True)
            self._secure_path(avatar_dir, 0o700)
            target = avatar_dir / f"{agent_id}-preset.png"
            try:
                target.write_bytes(source.read_bytes())
            except OSError:
                continue
            self._secure_path(target, 0o600)
            stored = target.resolve().relative_to(self.uploads_dir.resolve()).as_posix()
            conn.execute(
                "UPDATE agents SET avatar_path=?,avatar_mime=?,avatar_version=1,updated_at=? WHERE id=?",
                (stored, "image/png", now, agent_id),
            )

    def agent_avatar_file(self, agent_id: str) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        stored_path = agent.get("avatar_path")
        resolved_path = None
        if stored_path:
            candidate = Path(str(stored_path))
            resolved_path = candidate if candidate.is_absolute() else self.uploads_dir / candidate
        return {
            "path": stored_path,
            "resolved_path": resolved_path,
            "mime": agent.get("avatar_mime"),
            "version": int(agent.get("avatar_version", 0) or 0),
        }

    def agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        self.get_agent(agent_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tool_name,enabled FROM agent_tools WHERE agent_id=?",
                (agent_id,),
            ).fetchall()
        states = {str(row["tool_name"]): bool(row["enabled"]) for row in rows}
        from ..tools import tool_catalog

        return [
            {
                **item,
                "enabled": states.get(item["name"], item["name"] in DEFAULT_AGENT_TOOL_NAMES),
            }
            for item in tool_catalog()
        ]

    def set_agent_tools(self, agent_id: str, actor_id: int, tool_names: Any) -> list[dict[str, Any]]:
        self._assert_agent_manager(agent_id, actor_id)
        from ..tools import validate_tool_names

        enabled = set(validate_tool_names(tool_names))
        now = time.time()
        with self._connect() as conn:
            for name in CONFIGURABLE_AGENT_TOOL_NAMES:
                conn.execute(
                    """INSERT INTO agent_tools(agent_id,tool_name,enabled,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(agent_id,tool_name) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
                    (agent_id, name, int(name in enabled), now),
                )
        return self.agent_tools(agent_id)

    def agent_tool_enabled(self, agent_id: str, tool_name: str) -> bool:
        require_tool(tool_name)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM agent_tools WHERE agent_id=? AND tool_name=?",
                (agent_id, tool_name),
            ).fetchone()
        return bool(row and row["enabled"])

    def group_agents(self, group_id: int, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        clauses = (
            ["ga.group_id=?", "ga.enabled=1", "a.enabled=1"]
            if enabled_only
            else ["ga.group_id=?"]
        )
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT a.*,ga.enabled AS joined_enabled,ga.auto_sticker AS group_auto_sticker,
                ga.added_by,ga.created_at AS joined_at
                FROM group_agents ga JOIN agents a ON a.id=ga.agent_id
                WHERE {' AND '.join(clauses)} ORDER BY CASE WHEN a.id='agent' THEN 0 ELSE 1 END,a.name COLLATE NOCASE""",
                (group_id,),
            ).fetchall()
        return [self._agent_dict(row) for row in rows]

    def set_group_agent(
        self, group_id: int, agent_id: str, actor_id: int, enabled: bool
    ) -> list[dict[str, Any]]:
        if not self.can_manage_group(group_id, actor_id):
            raise DatabaseError("没有管理群组 Agent 的权限")
        self.get_agent(agent_id)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO group_agents(group_id,agent_id,enabled,added_by,created_at) VALUES(?,?,?,?,?)
                ON CONFLICT(group_id,agent_id) DO UPDATE SET enabled=excluded.enabled""",
                (group_id, agent_id, int(bool(enabled)), actor_id, now),
            )
        return self.group_agents(group_id)

    def set_group_auto_sticker(
        self, group_id: int, agent_id: str, actor_id: int, enabled: bool
    ) -> list[dict[str, Any]]:
        if not self.can_manage_group(group_id, actor_id):
            raise DatabaseError("没有管理群组表情包设置的权限")
        self.get_agent(agent_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM group_agents WHERE group_id=? AND agent_id=?",
                (group_id, agent_id),
            ).fetchone()
            if not row:
                raise DatabaseError("该 Agent 尚未加入当前群组")
            conn.execute(
                "UPDATE group_agents SET auto_sticker=? WHERE group_id=? AND agent_id=?",
                (int(bool(enabled)), group_id, agent_id),
            )
        return self.group_agents(group_id)

    def group_tool_enabled(self, group_id: int, agent_id: str, tool_name: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM group_agent_tools WHERE group_id=? AND agent_id=? AND tool_name=?",
                (group_id, agent_id, tool_name),
            ).fetchone()
        return True if not row else bool(row["enabled"])

    def set_group_tools(
        self, group_id: int, agent_id: str, actor_id: int, tool_names: Any
    ) -> list[dict[str, Any]]:
        if not self.can_manage_group(group_id, actor_id):
            raise DatabaseError("没有管理群组工具权限的权限")
        self.get_agent(agent_id)
        from ..tools import validate_tool_names

        enabled = set(validate_tool_names(tool_names))
        now = time.time()
        with self._connect() as conn:
            for name in CONFIGURABLE_AGENT_TOOL_NAMES:
                conn.execute(
                    """INSERT INTO group_agent_tools(group_id,agent_id,tool_name,enabled,updated_by,updated_at)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(group_id,agent_id,tool_name) DO UPDATE SET
                    enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (group_id, agent_id, name, int(name in enabled), actor_id, now),
                )
        return self.group_tool_states(group_id, agent_id)

    def group_tool_states(self, group_id: int, agent_id: str) -> list[dict[str, Any]]:
        tools = self.agent_tools(agent_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tool_name,enabled FROM group_agent_tools WHERE group_id=? AND agent_id=?",
                (group_id, agent_id),
            ).fetchall()
        overrides = {str(row["tool_name"]): bool(row["enabled"]) for row in rows}
        for item in tools:
            item["group_enabled"] = overrides.get(item["name"], True)
        return tools
