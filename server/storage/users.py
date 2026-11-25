"""User accounts, identity and avatars."""

from __future__ import annotations

from typing import Any
from .base import DatabaseError
from pathlib import Path
from .constants import USERNAME_RE
import hmac
import os
import sqlite3
import time


class UsersMixin:
    """User accounts, identity and avatars."""

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
