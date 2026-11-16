from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


PBKDF2_ITERATIONS = 310_000
USERNAME_RE = re.compile(r"^[\w\-\u4e00-\u9fff]{3,20}$", re.UNICODE)


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
                    joined_at REAL NOT NULL,
                    PRIMARY KEY(group_id,user_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_type TEXT NOT NULL CHECK(conversation_type IN ('group','private')),
                    conversation_id TEXT NOT NULL,
                    sender_id INTEGER REFERENCES users(id),
                    sender_name TEXT NOT NULL,
                    sender_kind TEXT NOT NULL DEFAULT 'user' CHECK(sender_kind IN ('user','agent')),
                    message_type TEXT NOT NULL CHECK(message_type IN ('text','image','agent')),
                    content_format TEXT NOT NULL DEFAULT 'plain_text' CHECK(content_format IN ('plain_text','markdown')),
                    content TEXT NOT NULL DEFAULT '',
                    mentions TEXT NOT NULL DEFAULT '[]',
                    image_path TEXT,
                    image_name TEXT,
                    image_mime TEXT,
                    created_at REAL NOT NULL,
                    recalled_at REAL,
                    recalled_by INTEGER REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(conversation_type,conversation_id,id);
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
                    task_type TEXT NOT NULL,
                    conversation_type TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
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
                    tool_name TEXT NOT NULL,
                    conversation_type TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    input_summary TEXT NOT NULL,
                    result_count INTEGER,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    error TEXT
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
            if "mentions" not in message_columns:
                conn.execute("ALTER TABLE messages ADD COLUMN mentions TEXT NOT NULL DEFAULT '[]'")
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
            conn.execute(
                "INSERT OR IGNORE INTO groups(name,owner_id,created_at) VALUES('大厅',NULL,?)",
                (time.time(),),
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

    def _hash_password(self, password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.password_iterations)

    def register_user(self, username: str, password: str, role: str = "user") -> dict[str, Any]:
        username = username.strip()
        if not USERNAME_RE.fullmatch(username):
            raise DatabaseError("用户名需为3至20个汉字、字母、数字、下划线或连字符")
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
            raise DatabaseError("用户名已存在") from exc
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
            "role": row["role"],
            "avatar_mime": row["avatar_mime"] if "avatar_mime" in keys else None,
            "avatar_version": int(row["avatar_version"] or 0) if "avatar_version" in keys else 0,
        }

    def get_user(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,username,role,avatar_mime,avatar_version FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
        if not row:
            raise DatabaseError("用户不存在")
        return self._public_user(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,username,role,avatar_mime,avatar_version FROM users ORDER BY username COLLATE NOCASE"
            ).fetchall()
        return [self._public_user(row) for row in rows]

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
                SELECT u.id,u.username,u.role AS global_role,gm.role AS group_role,
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
        content_format: str = "plain_text",
        image_path: str | None = None,
        image_name: str | None = None,
        image_mime: str | None = None,
        mentions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if conversation_type not in {"group", "private"}:
            raise DatabaseError("会话类型无效")
        if content_format not in {"plain_text", "markdown"}:
            raise DatabaseError("消息内容格式无效")
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages(conversation_type,conversation_id,sender_id,sender_name,sender_kind,message_type,content_format,content,mentions,image_path,image_name,image_mime,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    conversation_type,
                    conversation_id,
                    sender_id,
                    sender_name,
                    sender_kind,
                    message_type,
                    content_format,
                    content,
                    json.dumps(mentions or [], ensure_ascii=False, separators=(",", ":")),
                    image_path,
                    image_name,
                    image_mime,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._message_dict(row)

    @staticmethod
    def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
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
        return data

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
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
                    "SELECT username FROM users WHERE id=?", (peer_id,)
                ).fetchone()
                if peer:
                    scopes.append(("private", scope_id, str(peer["username"]), ""))

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
                    elif latest_data["message_type"] == "image":
                        preview = "[图片]"
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

    def search_messages(self, user_id: int, conversation_type: str, conversation_id: str, keyword: str, limit: int = 50) -> list[dict[str, Any]]:
        self._assert_access(user_id, conversation_type, conversation_id)
        keyword = keyword.strip()
        if not keyword:
            raise DatabaseError("搜索关键词不能为空")
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages WHERE conversation_type=? AND conversation_id=?
                AND recalled_at IS NULL AND content LIKE ? ESCAPE '\\'
                ORDER BY id DESC LIMIT ?
                """,
                (conversation_type, conversation_id, f"%{keyword}%", limit),
            ).fetchall()
        return [self._message_dict(row) for row in rows]

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
        data = self.get_message(message_id)
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

    def create_agent_run(self, task_type: str, conversation_type: str, conversation_id: str, requested_by: int | None) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO agent_runs(task_type,conversation_type,conversation_id,requested_by,status,started_at) VALUES(?,?,?,?,?,?)",
                (task_type, conversation_type, conversation_id, requested_by, "running", time.time()),
            )
            return int(cursor.lastrowid)

    def finish_agent_run(self, run_id: int, status: str, *, result_message_id: int | None = None, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_runs SET status=?,finished_at=?,result_message_id=?,error=? WHERE id=?",
                (status, time.time(), result_message_id, error, run_id),
            )

    def start_tool_call(self, run_id: int, tool_name: str, conversation_type: str, conversation_id: str, input_summary: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO tool_calls(run_id,tool_name,conversation_type,conversation_id,input_summary,status,started_at) VALUES(?,?,?,?,?,'running',?)""",
                (run_id, tool_name, conversation_type, conversation_id, input_summary[:500], time.time()),
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
                SELECT tc.*,ar.requested_by,u.username AS requested_by_name
                FROM tool_calls tc
                JOIN agent_runs ar ON ar.id=tc.run_id
                LEFT JOIN users u ON u.id=ar.requested_by
                WHERE tc.id=?
                """,
                (tool_id,),
            ).fetchone()
        return dict(row)

    def recent_tool_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tc.*,ar.requested_by,u.username AS requested_by_name
                FROM tool_calls tc
                JOIN agent_runs ar ON ar.id=tc.run_id
                LEFT JOIN users u ON u.id=ar.requested_by
                ORDER BY tc.id DESC LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_agent_activity(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ar.*,u.username AS requested_by_name
                FROM agent_runs ar
                LEFT JOIN users u ON u.id=ar.requested_by
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
                SELECT ar.*,u.username AS requested_by_name
                FROM agent_runs ar
                LEFT JOIN users u ON u.id=ar.requested_by
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

