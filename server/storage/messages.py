"""Messages, private conversations, history and search."""

from __future__ import annotations

from typing import Any
from .base import DatabaseError, dm_conversation_id, dm_participants
from ..stickers import extract_sticker_directive
import json
import sqlite3
import time


class MessagesMixin:
    """Messages, private conversations, history and search."""

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
            if conversation_type == "private":
                participants = dm_participants(conversation_id)
                if not participants or (sender_id is not None and sender_id not in participants):
                    raise DatabaseError("私聊会话无效")
                conn.execute("BEGIN IMMEDIATE")
                blocked = conn.execute(
                    "SELECT 1 FROM private_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
                    (participants[0], participants[1], participants[1], participants[0]),
                ).fetchone()
                if blocked:
                    raise DatabaseError("当前私聊不可发送")
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
            if conversation_type == "private" and conversation_id == dm_conversation_id(*participants):
                conn.executemany(
                    "INSERT OR IGNORE INTO private_conversations(user_id,peer_id,opened_at) VALUES(?,?,?)",
                    [(participants[0], participants[1], now), (participants[1], participants[0], now)],
                )
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

    def private_state(self, user_id: int, peer_id: int) -> dict[str, Any]:
        if user_id == peer_id:
            raise DatabaseError("私聊会话无效")
        self.get_user(peer_id)
        scope_id = dm_conversation_id(user_id, peer_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT blocker_id FROM private_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
                (user_id, peer_id, peer_id, user_id),
            ).fetchall()
        blockers = {int(row["blocker_id"]) for row in rows}
        return {"conversation_id": scope_id, "can_send": not blockers, "blocked_by_me": user_id in blockers}

    def open_private(self, user_id: int, peer_id: int) -> dict[str, Any]:
        if user_id == peer_id:
            raise DatabaseError("不能与自己私聊")
        self.get_user(peer_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO private_conversations(user_id,peer_id,opened_at) VALUES(?,?,?)",
                (user_id, peer_id, time.time()),
            )
        return self.list_conversations(user_id, target_scope=dm_conversation_id(user_id, peer_id))[0]

    def set_private_block(self, user_id: int, peer_id: int, blocked: bool) -> dict[str, Any]:
        if user_id == peer_id:
            raise DatabaseError("不能屏蔽自己")
        self.get_user(peer_id)
        with self._connect() as conn:
            if blocked:
                conn.execute(
                    "INSERT OR IGNORE INTO private_blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)",
                    (user_id, peer_id, time.time()),
                )
            else:
                conn.execute(
                    "DELETE FROM private_blocks WHERE blocker_id=? AND blocked_id=?",
                    (user_id, peer_id),
                )
        return self.private_state(user_id, peer_id)

    def assert_private_can_send(self, conversation_id: str) -> None:
        participants = dm_participants(conversation_id)
        if not participants:
            raise DatabaseError("私聊会话无效")
        with self._connect() as conn:
            blocked = conn.execute(
                "SELECT 1 FROM private_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
                (participants[0], participants[1], participants[1], participants[0]),
            ).fetchone()
        if blocked:
            raise DatabaseError("当前私聊不可发送")

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

    def list_conversations(self, user_id: int, limit: int = 100, *, target_scope: str | None = None) -> list[dict[str, Any]]:
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
                "SELECT peer_id,opened_at FROM private_conversations WHERE user_id=?", (user_id,)
            ).fetchall()
            legacy_rows = conn.execute(
                "SELECT DISTINCT conversation_id FROM messages WHERE conversation_type='private'"
            ).fetchall()
            scopes: list[tuple[str, str, str, str]] = [
                ("group", str(row["id"]), str(row["name"]), str(row["member_role"]))
                for row in groups
            ]
            private_opened_at = {
                dm_conversation_id(user_id, int(row["peer_id"])): float(row["opened_at"])
                for row in private_rows
            }
            private_ids = set(private_opened_at)
            private_ids.update(str(row["conversation_id"]) for row in legacy_rows)
            for scope_id in private_ids:
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
                if target_scope is not None and scope_id != target_scope:
                    continue
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
                if scope_type == "private":
                    participants = dm_participants(scope_id)
                    peer_id = participants[1] if participants[0] == user_id else participants[0]
                    unread = conn.execute(
                        "SELECT COUNT(*) AS count FROM messages WHERE conversation_type=? AND conversation_id=? AND id>? AND (sender_id IS NULL OR sender_id<>?)",
                        (scope_type, scope_id, last_read, user_id),
                    ).fetchone()
                    peer_cursor = conn.execute(
                        "SELECT last_message_id FROM read_cursors WHERE user_id=? AND conversation_type='private' AND conversation_id=?",
                        (peer_id, scope_id),
                    ).fetchone()
                    block_rows = conn.execute(
                        "SELECT blocker_id FROM private_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
                        (user_id, peer_id, peer_id, user_id),
                    ).fetchall()
                    blockers = {int(item["blocker_id"]) for item in block_rows}
                else:
                    unread = conn.execute(
                        "SELECT COUNT(*) AS count FROM messages WHERE conversation_type=? AND conversation_id=? AND id>?",
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
                conversation = {
                    "conversation_type": scope_type,
                    "conversation_id": scope_id,
                    "title": title,
                    "member_role": member_role,
                    "last_message": latest_data,
                    "last_message_preview": preview,
                    "last_message_at": latest_data.get("created_at", 0) if latest_data else 0,
                    "unread_count": int(unread["count"]) if unread else 0,
                }
                if scope_type == "private":
                    conversation.update(
                        can_send=not blockers,
                        blocked_by_me=user_id in blockers,
                        peer_last_read_message_id=int(peer_cursor["last_message_id"]) if peer_cursor else 0,
                    )
                conversations.append(conversation)
        conversations.sort(
            key=lambda item: (
                float(item["last_message_at"] or private_opened_at.get(item["conversation_id"], 0)),
                item["title"],
            ),
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

    def recent_history(
        self,
        user_id: int,
        conversation_type: str,
        conversation_id: str,
        limit: int = 50,
        *,
        after_id: int = 0,
        since: float | None = None,
        until: float | None = None,
    ) -> list[dict[str, Any]]:
        self._assert_access(user_id, conversation_type, conversation_id)
        limit = max(1, min(int(limit), 500))
        conditions = [
            "conversation_type=?",
            "conversation_id=?",
            "recalled_at IS NULL",
        ]
        params: list[Any] = [conversation_type, conversation_id]
        if after_id:
            conditions.append("id>?")
            params.append(after_id)
        if since is not None:
            conditions.append("created_at>=?")
            params.append(float(since))
        if until is not None:
            conditions.append("created_at<?")
            params.append(float(until))
        params.append(limit)
        with self._connect() as conn:
            chronological = bool(after_id or since is not None or until is not None)
            order = "ASC" if chronological else "DESC"
            rows = conn.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(conditions)} ORDER BY id {order} LIMIT ?",
                tuple(params),
            ).fetchall()
            if not chronological:
                rows = rows[::-1]
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

    def set_read_cursor(self, user_id: int, conversation_type: str, conversation_id: str, message_id: int) -> bool:
        self._assert_access(user_id, conversation_type, conversation_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            message = conn.execute(
                "SELECT 1 FROM messages WHERE id=? AND conversation_type=? AND conversation_id=?",
                (message_id, conversation_type, conversation_id),
            ).fetchone()
            if not message:
                raise DatabaseError("已读消息不属于当前会话")
            previous = conn.execute(
                "SELECT last_message_id FROM read_cursors WHERE user_id=? AND conversation_type=? AND conversation_id=?",
                (user_id, conversation_type, conversation_id),
            ).fetchone()
            if previous and int(previous["last_message_id"]) >= message_id:
                return False
            conn.execute(
                """
                INSERT INTO read_cursors(user_id,conversation_type,conversation_id,last_message_id) VALUES(?,?,?,?)
                ON CONFLICT(user_id,conversation_type,conversation_id)
                DO UPDATE SET last_message_id=MAX(last_message_id,excluded.last_message_id)
                """,
                (user_id, conversation_type, conversation_id, message_id),
            )
        return True

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
