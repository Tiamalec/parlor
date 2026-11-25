"""SQLite database composition root."""

from __future__ import annotations

from .agent_config import BUILTIN_AGENT_PRESETS, DEFAULT_AGENT_CARD, DEFAULT_AGENT_ID, LEGACY_BUILTIN_AGENT_IDS
from .tools import DEFAULT_AGENT_TOOL_NAMES
from .storage.schema import MESSAGES_REBUILD_SQL, SCHEMA_SQL
from .storage.constants import PBKDF2_ITERATIONS
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import time

from .storage.activity import ActivityMixin
from .storage.agents import AgentsMixin
from .storage.base import DatabaseError, _ClosingConnection, dm_conversation_id, dm_participants
from .storage.groups import GroupsMixin
from .storage.memory import MemoryMixin
from .storage.messages import MessagesMixin
from .storage.stickers import StickersMixin
from .storage.users import UsersMixin


class Database(
    ActivityMixin,
    AgentsMixin,
    GroupsMixin,
    MemoryMixin,
    MessagesMixin,
    StickersMixin,
    UsersMixin,
):

    def __init__(self, path: Path, uploads_dir: Path, password_iterations: int = PBKDF2_ITERATIONS) -> None:
        self.path = Path(path)
        self.uploads_dir = Path(uploads_dir)
        self.password_iterations = max(100_000, int(password_iterations))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secure_path(self.path.parent, 0o700)
        conn = sqlite3.connect(self.path, timeout=5.0, factory=_ClosingConnection)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._secure_path(self.path, 0o600)
            self._secure_path(Path(f"{self.path}-wal"), 0o600, missing_ok=True)
            self._secure_path(Path(f"{self.path}-shm"), 0o600, missing_ok=True)
        except Exception:
            conn.close()
            raise
        return conn

    @staticmethod
    def _secure_path(path: Path, mode: int, *, missing_ok: bool = False) -> None:
        if os.name != "posix":
            return
        try:
            path.chmod(mode)
        except FileNotFoundError:
            if not missing_ok:
                raise

    def initialize(self, admin_username: str | None = None, admin_password: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self._secure_path(self.path.parent, 0o700)
        self._secure_path(self.uploads_dir, 0o700)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA_SQL)
            message_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "content_format" not in message_columns:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN content_format TEXT NOT NULL DEFAULT 'plain_text'"
                )
            if "card_payload" not in message_columns:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN card_payload TEXT NOT NULL DEFAULT ''"
                )
            if "mentions" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN mentions TEXT NOT NULL DEFAULT '[]'")
            if "reply_to_id" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN reply_to_id INTEGER REFERENCES messages(id)")
            if "reply_preview" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN reply_preview TEXT")
            if "edited_at" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN edited_at REAL")
            if "client_request_id" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN client_request_id TEXT")
            if "sender_agent_id" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN sender_agent_id TEXT REFERENCES agents(id)")
            for column, declaration in (
                ("sticker_id", "TEXT"),
                ("sticker_name", "TEXT"),
                ("sticker_category", "TEXT"),
                ("sticker_tags", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if column not in message_columns:
                    conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {declaration}")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_request ON messages(sender_id,client_request_id) WHERE client_request_id IS NOT NULL")
            if not conn.execute("SELECT 1 FROM message_events LIMIT 1").fetchone():
                conn.execute("INSERT INTO message_events(message_id,event_type) SELECT id,'message.event' FROM messages ORDER BY id")
            user_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "avatar_path" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
            if "avatar_mime" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_mime TEXT")
            if "avatar_version" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_version INTEGER NOT NULL DEFAULT 0")
            if "display_name" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
            member_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(group_members)")}
            if "nickname" not in member_columns:
                conn.execute("ALTER TABLE group_members ADD COLUMN nickname TEXT")
            run_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(agent_runs)")}
            if "agent_id" not in run_columns:
                conn.execute("ALTER TABLE agent_runs ADD COLUMN agent_id TEXT")
            if "model_profile_id" not in run_columns:
                conn.execute("ALTER TABLE agent_runs ADD COLUMN model_profile_id TEXT")
            tool_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tool_calls)")}
            if "agent_id" not in tool_columns:
                conn.execute("ALTER TABLE tool_calls ADD COLUMN agent_id TEXT")
            if "tool_mode" not in tool_columns:
                conn.execute("ALTER TABLE tool_calls ADD COLUMN tool_mode TEXT NOT NULL DEFAULT 'auto'")
            todo_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(personal_todos)").fetchall()
            }
            for column, declaration in (
                ("normalized_title", "TEXT NOT NULL DEFAULT ''"),
                ("idempotency_key", "TEXT"),
                ("version", "INTEGER NOT NULL DEFAULT 1"),
                ("manual_revision", "INTEGER NOT NULL DEFAULT 0"),
                ("last_modified_by", "TEXT NOT NULL DEFAULT 'manual'"),
                (
                    "created_by_agent_id",
                    "TEXT REFERENCES agents(id) ON DELETE SET NULL",
                ),
            ):
                if column not in todo_columns:
                    conn.execute(
                        f"ALTER TABLE personal_todos ADD COLUMN {column} {declaration}"
                    )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_personal_todos_idempotency "
                "ON personal_todos(owner_id,idempotency_key) "
                "WHERE idempotency_key IS NOT NULL AND idempotency_key<>''"
            )
            agent_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(agents)")}
            if "auto_sticker" not in agent_columns:
                conn.execute("ALTER TABLE agents ADD COLUMN auto_sticker INTEGER NOT NULL DEFAULT 1")
            joined_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(group_agents)")
            }
            if "auto_sticker" not in joined_columns:
                conn.execute(
                    "ALTER TABLE group_agents ADD COLUMN auto_sticker INTEGER NOT NULL DEFAULT 1"
                )
            sticker_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(stickers)")
            }
            if "shared" not in sticker_columns:
                # Existing libraries were globally selectable, so keep that behavior.
                conn.execute("ALTER TABLE stickers ADD COLUMN shared INTEGER NOT NULL DEFAULT 1")
            self._rebuild_messages_for_stickers(conn)
            setting_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(agent_memory_settings)")
            }
            if setting_columns and "user_id" not in setting_columns:
                conn.execute("DROP TABLE agent_memory_settings")
                conn.execute(
                    """CREATE TABLE agent_memory_settings (
                        agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                        user_id INTEGER NOT NULL DEFAULT 0,
                        scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
                        scope_id TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY(agent_id,user_id,scope_type,scope_id)
                    )"""
                )
            conn.execute(
                "INSERT OR IGNORE INTO groups(name,owner_id,created_at) VALUES('大厅',NULL,?)",
                (time.time(),),
            )
            now = time.time()
            conn.execute(
                """INSERT OR IGNORE INTO agents(
                    id,owner_id,name,description,card,enabled,memory_enabled,created_at,updated_at
                ) VALUES(?,?,?,?,?,1,1,?,?)""",
                (
                    DEFAULT_AGENT_ID,
                    None,
                    "AI Agent",
                    "聊天室默认协作助手，可回答问题、总结会话和提取待办。",
                    json.dumps(DEFAULT_AGENT_CARD, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            conn.executemany(
                "DELETE FROM agents WHERE id=?",
                [(legacy_id,) for legacy_id in LEGACY_BUILTIN_AGENT_IDS],
            )
            for preset in BUILTIN_AGENT_PRESETS:
                conn.execute(
                    """INSERT OR IGNORE INTO agents(
                        id,owner_id,name,description,card,enabled,memory_enabled,created_at,updated_at
                    ) VALUES(?,?,?,?,?,1,1,?,?)""",
                    (
                        preset["id"],
                        None,
                        preset["name"],
                        preset["description"],
                        json.dumps(preset["card"], ensure_ascii=False, separators=(",", ":")),
                        now,
                        now,
                    ),
                )
            self._seed_preset_avatars(conn, now)
            rows = conn.execute("SELECT id FROM agents").fetchall()
            for row in rows:
                for tool_name in DEFAULT_AGENT_TOOL_NAMES:
                    conn.execute(
                        "INSERT OR IGNORE INTO agent_tools(agent_id,tool_name,enabled,updated_at) VALUES(?,?,1,?)",
                        (row["id"], tool_name, now),
                    )
            conn.execute(
                """INSERT OR IGNORE INTO group_agents(group_id,agent_id,enabled,created_at)
                SELECT id,?,1,? FROM groups""",
                (DEFAULT_AGENT_ID, now),
            )
        if admin_username and admin_password:
            try:
                self.register_user(admin_username, admin_password, role="admin")
            except DatabaseError as exc:
                existing = self.authenticate(admin_username, admin_password)
                if not existing:
                    raise DatabaseError(
                        "管理员用户名已存在且密码不匹配；拒绝自动提升该账号"
                    ) from exc
                with self._connect() as conn:
                    conn.execute("UPDATE users SET role='admin' WHERE username=? COLLATE NOCASE", (admin_username,))

    @staticmethod
    def _rebuild_messages_for_stickers(conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        if not row or "'sticker'" in str(row["sql"] or ""):
            return
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(MESSAGES_REBUILD_SQL)
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

    def _hash_password(self, password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.password_iterations)
