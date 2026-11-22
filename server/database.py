from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .agent_config import (
    BUILTIN_AGENT_IDS,
    BUILTIN_AGENT_PRESETS,
    DEFAULT_AGENT_CARD,
    DEFAULT_AGENT_ID,
    LEGACY_BUILTIN_AGENT_IDS,
    memory_terms,
    normalize_agent_card,
    normalize_agent_description,
    normalize_agent_name,
)
from .tools import DEFAULT_AGENT_TOOL_NAMES, require_tool
from .stickers import extract_sticker_directive, normalize_sticker_agent_ids


PBKDF2_ITERATIONS = 310_000
USERNAME_RE = re.compile(r"^[\w\-\u4e00-\u9fff]{3,20}$", re.UNICODE)
PRESET_AVATAR_DIR = Path(__file__).resolve().parent / "assets" / "agent_avatars"


class DatabaseError(ValueError):
    pass


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back, then close when used as a context manager."""

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def dm_conversation_id(user_a: int, user_b: int) -> str:
    low, high = sorted((int(user_a), int(user_b)))
    return f"dm:{low}:{high}"


def dm_participants(conversation_id: str) -> tuple[int, int] | None:
    parts = conversation_id.split(":")
    if len(parts) != 3 or parts[0] != "dm":
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


class Database:
    def __init__(self, path: Path, uploads_dir: Path, password_iterations: int = PBKDF2_ITERATIONS) -> None:
        self.path = Path(path)
        self.uploads_dir = Path(uploads_dir)
        self.password_iterations = max(100_000, int(password_iterations))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secure_path(self.path.parent, 0o700)
        conn = sqlite3.connect(self.path, timeout=5.0, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        self._secure_path(self.path, 0o600)
        self._secure_path(Path(f"{self.path}-wal"), 0o600, missing_ok=True)
        self._secure_path(Path(f"{self.path}-shm"), 0o600, missing_ok=True)
        return conn

    @staticmethod
    def _secure_path(path: Path, mode: int, *, missing_ok: bool = False) -> None:
        if os.name != "posix":
            return
        if missing_ok and not path.exists():
            return
        path.chmod(mode)

    def initialize(self, admin_username: str | None = None, admin_password: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self._secure_path(self.path.parent, 0o700)
        self._secure_path(self.uploads_dir, 0o700)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT,
                    password_hash BLOB NOT NULL,
                    salt BLOB NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
                    avatar_path TEXT,
                    avatar_mime TEXT,
                    avatar_version INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    owner_id INTEGER REFERENCES users(id),
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_members (
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','owner')),
                    nickname TEXT,
                    joined_at REAL NOT NULL,
                    PRIMARY KEY(group_id,user_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_type TEXT NOT NULL CHECK(conversation_type IN ('group','private')),
                    conversation_id TEXT NOT NULL,
                    sender_id INTEGER REFERENCES users(id),
                    sender_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
                    sender_name TEXT NOT NULL,
                    sender_kind TEXT NOT NULL DEFAULT 'user' CHECK(sender_kind IN ('user','agent')),
                    message_type TEXT NOT NULL CHECK(message_type IN ('text','image','agent','sticker','system')),
                    content_format TEXT NOT NULL DEFAULT 'plain_text' CHECK(content_format IN ('plain_text','markdown')),
                    card_payload TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    mentions TEXT NOT NULL DEFAULT '[]',
                    image_path TEXT,
                    image_name TEXT,
                    image_mime TEXT,
                    sticker_id TEXT,
                    sticker_name TEXT,
                    sticker_category TEXT,
                    sticker_tags TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    recalled_at REAL,
                    recalled_by INTEGER REFERENCES users(id),
                    reply_to_id INTEGER REFERENCES messages(id),
                    reply_preview TEXT,
                    edited_at REAL,
                    client_request_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(conversation_type,conversation_id,id);
                CREATE TABLE IF NOT EXISTS message_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    event_type TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_message_events_message ON message_events(message_id);
                CREATE TABLE IF NOT EXISTS read_cursors (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    conversation_type TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    last_message_id INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id,conversation_type,conversation_id)
                );
                CREATE TABLE IF NOT EXISTS mute_rules (
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    muted_by INTEGER NOT NULL REFERENCES users(id),
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    PRIMARY KEY(group_id,user_id)
                );
                CREATE TABLE IF NOT EXISTS agent_schedules (
                    group_id INTEGER PRIMARY KEY REFERENCES groups(id) ON DELETE CASCADE,
                    interval_minutes INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_message_id INTEGER NOT NULL DEFAULT 0,
                    next_run_at REAL NOT NULL,
                    updated_by INTEGER NOT NULL REFERENCES users(id),
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
                    task_type TEXT NOT NULL,
                    conversation_type TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    model_profile_id TEXT,
                    requested_by INTEGER REFERENCES users(id),
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    result_message_id INTEGER,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    agent_id TEXT,
                    tool_name TEXT NOT NULL,
                    conversation_type TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    input_summary TEXT NOT NULL,
                    tool_mode TEXT NOT NULL DEFAULT 'auto',
                    result_count INTEGER,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    description TEXT NOT NULL DEFAULT '',
                    avatar_path TEXT,
                    avatar_mime TEXT,
                    avatar_version INTEGER NOT NULL DEFAULT 0,
                    card TEXT NOT NULL DEFAULT '{}',
                    model_profile_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    memory_enabled INTEGER NOT NULL DEFAULT 1,
                    auto_sticker INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_agents (
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    auto_sticker INTEGER NOT NULL DEFAULT 1,
                    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(group_id,agent_id)
                );
                CREATE TABLE IF NOT EXISTS stickers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    category TEXT NOT NULL DEFAULT '通用',
                    tags TEXT NOT NULL DEFAULT '[]',
                    path TEXT NOT NULL,
                    mime TEXT NOT NULL,
                    animated INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stickers_category ON stickers(category,enabled);
                CREATE TABLE IF NOT EXISTS sticker_agents (
                    sticker_id TEXT NOT NULL REFERENCES stickers(id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(sticker_id,agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_sticker_agents_agent ON sticker_agents(agent_id,sticker_id);
                CREATE TABLE IF NOT EXISTS agent_sticker_state (
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
                    scope_id TEXT NOT NULL,
                    last_sent_at REAL NOT NULL,
                    last_sticker_id TEXT,
                    PRIMARY KEY(agent_id,scope_type,scope_id)
                );
                CREATE TABLE IF NOT EXISTS agent_tools (
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(agent_id,tool_name)
                );
                CREATE TABLE IF NOT EXISTS group_agent_tools (
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(group_id,agent_id,tool_name)
                );
                CREATE TABLE IF NOT EXISTS agent_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
                    scope_id TEXT NOT NULL,
                    owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    group_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL DEFAULT 'note',
                    content TEXT NOT NULL,
                    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_accessed_at REAL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_agent_memories_scope
                    ON agent_memories(agent_id,scope_type,scope_id,enabled);
                CREATE TABLE IF NOT EXISTS agent_memory_settings (
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
                    scope_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(agent_id,user_id,scope_type,scope_id)
                );
                CREATE TABLE IF NOT EXISTS agent_context_summaries (
                    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
                    scope_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    last_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(agent_id,scope_type,scope_id)
                );
                CREATE TABLE IF NOT EXISTS admin_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id INTEGER REFERENCES users(id),
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    source_ip TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at DESC);
                """
            )
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
        conn.executescript(
            """
            BEGIN IMMEDIATE;
            DROP TABLE IF EXISTS messages_rebuild;
            CREATE TABLE messages_rebuild (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_type TEXT NOT NULL CHECK(conversation_type IN ('group','private')),
                conversation_id TEXT NOT NULL,
                sender_id INTEGER REFERENCES users(id),
                sender_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
                sender_name TEXT NOT NULL,
                sender_kind TEXT NOT NULL DEFAULT 'user' CHECK(sender_kind IN ('user','agent')),
                message_type TEXT NOT NULL CHECK(message_type IN ('text','image','agent','sticker','system')),
                content_format TEXT NOT NULL DEFAULT 'plain_text' CHECK(content_format IN ('plain_text','markdown')),
                card_payload TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                mentions TEXT NOT NULL DEFAULT '[]',
                image_path TEXT,
                image_name TEXT,
                image_mime TEXT,
                sticker_id TEXT,
                sticker_name TEXT,
                sticker_category TEXT,
                sticker_tags TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                recalled_at REAL,
                recalled_by INTEGER REFERENCES users(id),
                reply_to_id INTEGER REFERENCES messages(id),
                reply_preview TEXT,
                edited_at REAL,
                client_request_id TEXT
            );
            INSERT INTO messages_rebuild (
                id,conversation_type,conversation_id,sender_id,sender_agent_id,sender_name,
                sender_kind,message_type,content_format,content,mentions,image_path,image_name,
                image_mime,sticker_id,sticker_name,sticker_category,sticker_tags,created_at,
                recalled_at,recalled_by,reply_to_id,reply_preview,edited_at,client_request_id
            )
            SELECT
                id,conversation_type,conversation_id,sender_id,sender_agent_id,sender_name,
                sender_kind,message_type,content_format,content,mentions,image_path,image_name,
                image_mime,sticker_id,sticker_name,sticker_category,sticker_tags,created_at,
                recalled_at,recalled_by,reply_to_id,reply_preview,edited_at,client_request_id
            FROM messages;
            DROP TABLE messages;
            ALTER TABLE messages_rebuild RENAME TO messages;
            CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(conversation_type,conversation_id,id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_request ON messages(sender_id,client_request_id) WHERE client_request_id IS NOT NULL;
            COMMIT;
            """
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

    def _hash_password(self, password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.password_iterations)

    def register_user(self, username: str, password: str, role: str = "user") -> dict[str, Any]:
        username = username.strip()
        if not USERNAME_RE.fullmatch(username):
            raise DatabaseError("账号需为3至20个汉字、字母、数字、下划线或连字符")
        if len(password) < 8:
            raise DatabaseError("密码至少需要8个字符")
        salt = os.urandom(16)
        digest = self._hash_password(password, salt)
        now = time.time()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO users(username,password_hash,salt,role,created_at) VALUES(?,?,?,?,?)",
                    (username, digest, salt, role, now),
                )
                user_id = int(cursor.lastrowid)
                lobby = conn.execute("SELECT id FROM groups WHERE name='大厅'").fetchone()
                if lobby:
                    conn.execute(
                        "INSERT OR IGNORE INTO group_members(group_id,user_id,role,joined_at) VALUES(?,?,'member',?)",
                        (lobby["id"], user_id, now),
                    )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("账号已存在") from exc
        return self.get_user(user_id)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)).fetchone()
        if not row:
            return None
        digest = self._hash_password(password, bytes(row["salt"]))
        if not hmac.compare_digest(digest, bytes(row["password_hash"])):
            return None
        return self._public_user(row)

    @staticmethod
    def _public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        keys = set(row.keys())
        return {
            "id": int(row["id"]),
            "username": row["username"],
            "display_name": row["display_name"] if "display_name" in keys else None,
            "role": row["role"],
            "avatar_mime": row["avatar_mime"] if "avatar_mime" in keys else None,
            "avatar_version": int(row["avatar_version"] or 0) if "avatar_version" in keys else 0,
        }

    def get_user(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,username,display_name,role,avatar_mime,avatar_version FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
        if not row:
            raise DatabaseError("用户不存在")
        return self._public_user(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,username,display_name,role,avatar_mime,avatar_version FROM users ORDER BY username COLLATE NOCASE"
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def update_identity(self, user_id: int, username: str, display_name: str) -> dict[str, Any]:
        username = username.strip()
        display_name = display_name.strip()
        if not USERNAME_RE.fullmatch(username):
            raise DatabaseError("账号需为3至20个汉字、字母、数字、下划线或连字符")
        if not 1 <= len(display_name) <= 30 or any(ord(char) < 32 for char in display_name):
            raise DatabaseError("昵称需为1至30个可见字符")
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE users SET username=?,display_name=? WHERE id=?",
                    (username, display_name, user_id),
                )
                if not cursor.rowcount:
                    raise DatabaseError("用户不存在")
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("账号已存在") from exc
        return self.get_user(user_id)

    def set_group_nickname(self, group_id: int, user_id: int, nickname: str) -> str | None:
        nickname = nickname.strip()
        if len(nickname) > 30 or any(ord(char) < 32 for char in nickname):
            raise DatabaseError("群内昵称需为1至30个可见字符，留空可恢复默认")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE group_members SET nickname=? WHERE group_id=? AND user_id=?",
                (nickname or None, group_id, user_id),
            )
            if not cursor.rowcount:
                raise DatabaseError("你不是该群成员")
        return nickname or None

    def name_in_scope(self, user_id: int, scope_type: str, scope_id: str) -> str:
        with self._connect() as conn:
            if scope_type == "group":
                row = conn.execute(
                    """SELECT gm.nickname,u.display_name,u.username FROM group_members gm
                    JOIN users u ON u.id=gm.user_id WHERE gm.group_id=? AND gm.user_id=?""",
                    (int(scope_id), user_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT display_name,username FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise DatabaseError("用户不存在或不在该群")
        return str((row["nickname"] if scope_type == "group" else None) or row["display_name"] or row["username"])

    def set_avatar(self, user_id: int, path: str | None, mime: str | None) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT avatar_path FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise DatabaseError("用户不存在")
            previous_path = row["avatar_path"]
            stored_path = path
            if path:
                try:
                    stored_path = Path(path).resolve().relative_to(self.uploads_dir.resolve()).as_posix()
                except ValueError:
                    stored_path = path
            conn.execute(
                "UPDATE users SET avatar_path=?,avatar_mime=?,avatar_version=avatar_version+1 WHERE id=?",
                (stored_path, mime, user_id),
            )
        user = self.get_user(user_id)
        user["previous_avatar_path"] = previous_path
        return user

    def avatar_file(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT avatar_path,avatar_mime,avatar_version FROM users WHERE id=?", (user_id,)
            ).fetchone()
        if not row:
            raise DatabaseError("用户不存在")
        stored_path = row["avatar_path"]
        resolved_path = None
        if stored_path:
            candidate = Path(str(stored_path))
            resolved_path = candidate if candidate.is_absolute() else self.uploads_dir / candidate
        return {
            "path": stored_path,
            "resolved_path": resolved_path,
            "mime": row["avatar_mime"],
            "version": int(row["avatar_version"] or 0),
        }

    @staticmethod
    def agent_member_id(agent_id: str) -> str:
        return DEFAULT_AGENT_ID if agent_id == DEFAULT_AGENT_ID else f"agent:{agent_id}"

    @staticmethod
    def _agent_dict(row: sqlite3.Row | dict[str, Any], *, user_id: int | None = None) -> dict[str, Any]:
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
        data["member_id"] = Database.agent_member_id(str(data["id"]))
        data["kind"] = "agent"
        data["username"] = data["name"]
        data["display_name"] = data["name"]
        data["can_manage"] = user_id is not None and (
            data.get("owner_id") == user_id or data.get("owner_role") == "admin"
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
            item = self._agent_dict(row, user_id=user_id)
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
        from .tools import tool_catalog

        return [
            {
                **item,
                "enabled": states.get(item["name"], item["name"] in DEFAULT_AGENT_TOOL_NAMES),
            }
            for item in tool_catalog()
        ]

    def set_agent_tools(self, agent_id: str, actor_id: int, tool_names: Any) -> list[dict[str, Any]]:
        self._assert_agent_manager(agent_id, actor_id)
        from .tools import validate_tool_names

        enabled = set(validate_tool_names(tool_names))
        now = time.time()
        with self._connect() as conn:
            for name in DEFAULT_AGENT_TOOL_NAMES:
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
        from .tools import validate_tool_names

        enabled = set(validate_tool_names(tool_names))
        now = time.time()
        with self._connect() as conn:
            for name in DEFAULT_AGENT_TOOL_NAMES:
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

    def save_message(
        self,
        conversation_type: str,
        conversation_id: str,
        sender_id: int | None,
        sender_name: str,
        message_type: str,
        content: str = "",
        *,
        sender_kind: str = "user",
        sender_agent_id: str | None = None,
        content_format: str = "plain_text",
        card_payload: dict[str, Any] | None = None,
        image_path: str | None = None,
        image_name: str | None = None,
        image_mime: str | None = None,
        sticker_id: str | None = None,
        sticker_name: str | None = None,
        sticker_category: str | None = None,
        sticker_tags: list[str] | None = None,
        mentions: list[dict[str, Any]] | None = None,
        reply_to_id: int | None = None,
        reply_preview: str | None = None,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        if conversation_type not in {"group", "private"}:
            raise DatabaseError("会话类型无效")
        if content_format not in {"plain_text", "markdown"}:
            raise DatabaseError("消息内容格式无效")
        card_json = ""
        if card_payload is not None:
            if not isinstance(card_payload, dict) or card_payload.get("kind") not in {
                "summary",
                "todo",
            }:
                raise DatabaseError("消息卡片格式无效")
            encoded_card = json.dumps(
                card_payload, ensure_ascii=False, separators=(",", ":")
            )
            card_json = encoded_card if len(encoded_card) <= 32_000 else ""
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO messages(conversation_type,conversation_id,sender_id,sender_agent_id,sender_name,sender_kind,message_type,content_format,card_payload,content,mentions,image_path,image_name,image_mime,sticker_id,sticker_name,sticker_category,sticker_tags,created_at,reply_to_id,reply_preview,client_request_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    conversation_type,
                    conversation_id,
                    sender_id,
                    sender_agent_id,
                    sender_name,
                    sender_kind,
                    message_type,
                    content_format,
                    card_json,
                    content,
                    json.dumps(mentions or [], ensure_ascii=False, separators=(",", ":")),
                    image_path,
                    image_name,
                    image_mime,
                    sticker_id,
                    sticker_name,
                    sticker_category,
                    json.dumps(sticker_tags or [], ensure_ascii=False, separators=(",", ":")),
                    now,
                    reply_to_id,
                    reply_preview,
                    client_request_id,
                ),
            )
            if not cursor.rowcount:
                row = conn.execute("SELECT * FROM messages WHERE sender_id=? AND client_request_id=?", (sender_id, client_request_id)).fetchone()
                if row:
                    return {**self._message_dict(row), "duplicate": True}
                raise DatabaseError("消息保存失败")
            row = conn.execute("SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)).fetchone()
            event_id = conn.execute("INSERT INTO message_events(message_id,event_type) VALUES(?,'message.event')", (row["id"],)).lastrowid
        return {**self._message_dict(row), "event_id": event_id}

    @staticmethod
    def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data.pop("client_request_id", None)
        raw_mentions = data.get("mentions", "[]")
        try:
            data["mentions"] = json.loads(raw_mentions) if isinstance(raw_mentions, str) else []
        except json.JSONDecodeError:
            data["mentions"] = []
        data["recalled"] = data.get("recalled_at") is not None
        data.pop("recalled_at", None)
        if data["recalled"]:
            data["content"] = ""
            data["image_path"] = None
        raw_card = data.pop("card_payload", "")
        card = None
        if raw_card and not data["recalled"]:
            try:
                parsed = json.loads(raw_card) if isinstance(raw_card, str) else None
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict) and parsed.get("kind") in {"summary", "todo"}:
                card = parsed
        data["card"] = card
        data["edited"] = data.get("edited_at") is not None
        if data.get("sender_kind") == "agent" and data.get("content"):
            data["content"], _requested_sticker = extract_sticker_directive(
                str(data["content"])
            )
        raw_tags = data.get("sticker_tags", "[]")
        try:
            tags = json.loads(raw_tags) if isinstance(raw_tags, str) else []
        except json.JSONDecodeError:
            tags = []
        data["sticker_tags"] = tags if isinstance(tags, list) else []
        if data.get("sticker_id"):
            data["sticker"] = {
                "id": data.get("sticker_id"),
                "name": data.get("sticker_name") or "表情包",
                "category": data.get("sticker_category") or "",
                "tags": data["sticker_tags"],
                "mime": data.get("image_mime"),
                "animated": str(data.get("image_mime") or "") == "image/gif",
            }
        else:
            data["sticker"] = None
        return data

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return self._message_dict(row) if row else None

    def get_message_by_request(self, sender_id: int, request_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE sender_id=? AND client_request_id=?", (sender_id, request_id)).fetchone()
        return self._message_dict(row) if row else None

    def _assert_access(self, user_id: int, conversation_type: str, conversation_id: str) -> None:
        if conversation_type == "group":
            try:
                group_id = int(conversation_id)
            except ValueError as exc:
                raise DatabaseError("群会话无效") from exc
            if not self.is_group_member(group_id, user_id):
                raise DatabaseError("无权访问该群")
        elif conversation_type == "private":
            participants = dm_participants(conversation_id)
            if not participants or user_id not in participants:
                raise DatabaseError("无权访问该私聊")
        else:
            raise DatabaseError("会话类型无效")

    def history(self, user_id: int, conversation_type: str, conversation_id: str, *, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        self._assert_access(user_id, conversation_type, conversation_id)
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE conversation_type=? AND conversation_id=? AND id>?
                ORDER BY id ASC LIMIT ?
                """,
                (conversation_type, conversation_id, after_id, limit),
            ).fetchall()
        return [self._message_dict(row) for row in rows]

    def history_page(
        self,
        user_id: int,
        conversation_type: str,
        conversation_id: str,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return one stable page while preserving chronological display order."""
        self._assert_access(user_id, conversation_type, conversation_id)
        limit = max(1, min(int(limit), 100))
        clauses = ["conversation_type=?", "conversation_id=?"]
        params: list[Any] = [conversation_type, conversation_id]
        descending = after_id is None
        if before_id is not None:
            clauses.append("id<?")
            params.append(int(before_id))
        elif after_id is not None:
            clauses.append("id>?")
            params.append(int(after_id))
            descending = False
        order = "DESC" if descending else "ASC"
        params.append(limit + 1)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(clauses)} ORDER BY id {order} LIMIT ?",
                params,
            ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        if descending:
            rows = rows[::-1]
        messages = [self._message_dict(row) for row in rows]
        return {
            "messages": messages,
            "has_more": has_more,
            "first_id": messages[0]["id"] if messages else None,
            "last_id": messages[-1]["id"] if messages else None,
        }

    def list_conversations(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """Build the modern conversation-list read model for one user."""
        limit = max(1, min(int(limit), 200))
        conversations: list[dict[str, Any]] = []
        with self._connect() as conn:
            groups = conn.execute(
                """
                SELECT g.id,g.name,gm.role AS member_role
                FROM group_members gm JOIN groups g ON g.id=gm.group_id
                WHERE gm.user_id=?
                """,
                (user_id,),
            ).fetchall()
            private_rows = conn.execute(
                "SELECT DISTINCT conversation_id FROM messages WHERE conversation_type='private'"
            ).fetchall()
            scopes: list[tuple[str, str, str, str]] = [
                ("group", str(row["id"]), str(row["name"]), str(row["member_role"]))
                for row in groups
            ]
            for row in private_rows:
                scope_id = str(row["conversation_id"])
                participants = dm_participants(scope_id)
                if not participants or user_id not in participants:
                    continue
                peer_id = participants[1] if participants[0] == user_id else participants[0]
                peer = conn.execute(
                    "SELECT username,display_name FROM users WHERE id=?", (peer_id,)
                ).fetchone()
                if peer:
                    scopes.append(("private", scope_id, str(peer["display_name"] or peer["username"]), ""))

            for scope_type, scope_id, title, member_role in scopes:
                latest = conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE conversation_type=? AND conversation_id=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (scope_type, scope_id),
                ).fetchone()
                cursor = conn.execute(
                    """
                    SELECT last_message_id FROM read_cursors
                    WHERE user_id=? AND conversation_type=? AND conversation_id=?
                    """,
                    (user_id, scope_type, scope_id),
                ).fetchone()
                last_read = int(cursor["last_message_id"]) if cursor else 0
                unread = conn.execute(
                    """
                    SELECT COUNT(*) AS count FROM messages
                    WHERE conversation_type=? AND conversation_id=? AND id>?
                    """,
                    (scope_type, scope_id, last_read),
                ).fetchone()
                latest_data = self._message_dict(latest) if latest else None
                preview = ""
                if latest_data:
                    if latest_data["recalled"]:
                        preview = "消息已撤回"
                    elif str((latest_data.get("card") or {}).get("kind")) == "summary":
                        preview = "[群聊总结]"
                    elif str((latest_data.get("card") or {}).get("kind")) == "todo":
                        preview = "[待办事项]"
                    elif latest_data["message_type"] == "image":
                        preview = "[图片]"
                    elif latest_data["message_type"] == "sticker":
                        preview = f"[表情包] {latest_data.get('sticker_name') or ''}".strip()
                    else:
                        preview = str(latest_data.get("content", "")).replace("\n", " ")[:80]
                conversations.append(
                    {
                        "conversation_type": scope_type,
                        "conversation_id": scope_id,
                        "title": title,
                        "member_role": member_role,
                        "last_message": latest_data,
                        "last_message_preview": preview,
                        "last_message_at": latest_data.get("created_at", 0) if latest_data else 0,
                        "unread_count": int(unread["count"]) if unread else 0,
                    }
                )
        conversations.sort(
            key=lambda item: (float(item["last_message_at"] or 0), item["title"]),
            reverse=True,
        )
        return conversations[:limit]

    def authorized_message(self, user_id: int, message_id: int) -> dict[str, Any]:
        message = self.get_message(message_id)
        if not message:
            raise DatabaseError("消息不存在")
        self._assert_access(
            user_id, str(message["conversation_type"]), str(message["conversation_id"])
        )
        return message

    def recent_history(self, user_id: int, conversation_type: str, conversation_id: str, limit: int = 50, *, after_id: int = 0) -> list[dict[str, Any]]:
        self._assert_access(user_id, conversation_type, conversation_id)
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            if after_id:
                rows = conn.execute(
                    """SELECT * FROM messages WHERE conversation_type=? AND conversation_id=? AND id>? AND recalled_at IS NULL ORDER BY id ASC LIMIT ?""",
                    (conversation_type, conversation_id, after_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM messages WHERE conversation_type=? AND conversation_id=? AND recalled_at IS NULL ORDER BY id DESC LIMIT ?""",
                    (conversation_type, conversation_id, limit),
                ).fetchall()[::-1]
        return [self._message_dict(row) for row in rows]

    def search_messages(self, user_id: int, conversation_type: str, conversation_id: str, keyword: str, limit: int = 50, before_id: int | None = None, *, paged: bool = False) -> Any:
        self._assert_access(user_id, conversation_type, conversation_id)
        keyword = keyword.strip()
        if not keyword:
            raise DatabaseError("搜索关键词不能为空")
        limit = max(1, min(int(limit), 200))
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages WHERE conversation_type=? AND conversation_id=?
                AND recalled_at IS NULL AND (content LIKE ? ESCAPE '\\' OR sticker_name LIKE ? ESCAPE '\\' OR sticker_tags LIKE ? ESCAPE '\\')
                AND (? IS NULL OR id<?)
                ORDER BY id DESC LIMIT ?
                """,
                (conversation_type, conversation_id, f"%{escaped}%", f"%{escaped}%", f"%{escaped}%", before_id, before_id, limit + 1),
            ).fetchall()
        page = {"messages": [self._message_dict(row) for row in rows[:limit]], "has_more": len(rows) > limit}
        return page if paged else page["messages"]

    def message_events_for_user(self, user_id: int, after_id: int, limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT e.id AS event_id, e.event_type, m.* FROM message_events e
                JOIN messages m ON m.id=e.message_id WHERE e.id>? ORDER BY e.id LIMIT ?""",
                (after_id, limit + 1),
            ).fetchall()
        events = []
        for row in rows[:limit]:
            message = self._message_dict(row)
            try:
                self._assert_access(user_id, message["conversation_type"], message["conversation_id"])
            except DatabaseError:
                continue
            message["event_id"] = row["event_id"]
            events.append({"event_type": row["event_type"], "message": message})
        return {"events": events, "has_more": len(rows) > limit,
                "cursor": rows[min(len(rows), limit) - 1]["event_id"] if rows else after_id}

    def edit_message(self, message_id: int, actor_id: int, content: str, mentions: list[dict[str, Any]]) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if not row:
                raise DatabaseError("消息不存在")
            self._assert_access(actor_id, row["conversation_type"], row["conversation_id"])
            if row["sender_id"] != actor_id or row["sender_kind"] != "user":
                raise DatabaseError("只能编辑自己的消息")
            if row["recalled_at"] is not None or row["message_type"] != "text":
                raise DatabaseError("该消息不能编辑")
            if row["content"] == content and json.loads(row["mentions"]) == mentions:
                raise DatabaseError("消息内容没有变化")
            conn.execute("UPDATE messages SET content=?,mentions=?,edited_at=? WHERE id=?",
                         (content, json.dumps(mentions, ensure_ascii=False), time.time(), message_id))
            event_id = conn.execute("INSERT INTO message_events(message_id,event_type) VALUES(?,'message.edited')", (message_id,)).lastrowid
        return {**self.get_message(message_id), "event_id": event_id}

    def set_read_cursor(self, user_id: int, conversation_type: str, conversation_id: str, message_id: int) -> None:
        self._assert_access(user_id, conversation_type, conversation_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO read_cursors(user_id,conversation_type,conversation_id,last_message_id) VALUES(?,?,?,?)
                ON CONFLICT(user_id,conversation_type,conversation_id)
                DO UPDATE SET last_message_id=MAX(last_message_id,excluded.last_message_id)
                """,
                (user_id, conversation_type, conversation_id, message_id),
            )

    def sync_for_user(self, user_id: int, limit_per_scope: int = 100) -> list[dict[str, Any]]:
        scopes: list[tuple[str, str]] = []
        with self._connect() as conn:
            groups = conn.execute("SELECT group_id FROM group_members WHERE user_id=?", (user_id,)).fetchall()
            scopes.extend(("group", str(row["group_id"])) for row in groups)
            private_scopes = conn.execute("SELECT DISTINCT conversation_id FROM messages WHERE conversation_type='private'").fetchall()
            scopes.extend(("private", row["conversation_id"]) for row in private_scopes if user_id in (dm_participants(row["conversation_id"]) or ()))
            output: list[dict[str, Any]] = []
            for scope_type, scope_id in scopes:
                cursor = conn.execute(
                    "SELECT last_message_id FROM read_cursors WHERE user_id=? AND conversation_type=? AND conversation_id=?",
                    (user_id, scope_type, scope_id),
                ).fetchone()
                after_id = int(cursor["last_message_id"]) if cursor else 0
                rows = conn.execute(
                    """SELECT * FROM messages WHERE conversation_type=? AND conversation_id=? AND id>? ORDER BY id ASC LIMIT ?""",
                    (scope_type, scope_id, after_id, limit_per_scope),
                ).fetchall()
                output.extend(self._message_dict(row) for row in rows)
        output.sort(key=lambda item: item["id"])
        return output

    def recall_message(self, message_id: int, actor_id: int) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if not row:
                raise DatabaseError("消息不存在")
            if row["recalled_at"] is not None:
                raise DatabaseError("消息已经撤回")
            actor = conn.execute("SELECT role FROM users WHERE id=?", (actor_id,)).fetchone()
            if not actor or actor["role"] != "admin":
                self._assert_access(actor_id, row["conversation_type"], row["conversation_id"])
            allowed = row["sender_id"] == actor_id and now - float(row["created_at"]) <= 120
            if actor and actor["role"] == "admin":
                allowed = True
            if row["conversation_type"] == "group" and self.can_manage_group(int(row["conversation_id"]), actor_id):
                allowed = True
            if not allowed:
                raise DatabaseError("没有权限撤回该消息或已超过两分钟")
            image_path = row["image_path"]
            conn.execute(
                "UPDATE messages SET content='',image_path=NULL,recalled_at=?,recalled_by=? WHERE id=?",
                (now, actor_id, message_id),
            )
            event_id = conn.execute("INSERT INTO message_events(message_id,event_type) VALUES(?,'message.recalled')", (message_id,)).lastrowid
        data = self.get_message(message_id)
        data["event_id"] = event_id
        if image_path:
            try:
                (self.uploads_dir / image_path).unlink(missing_ok=True)
            except OSError:
                pass
        assert data is not None
        return data

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

