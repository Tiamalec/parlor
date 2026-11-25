"""Message, history and media handlers."""

from __future__ import annotations

from ..constants import AGENT_MEMBER_ID, MAX_TEXT_LENGTH
from ..media import ALLOWED_IMAGE_MIME, MAX_IMAGE_SIZE, valid_image_signature
from typing import Any
from ..session import ClientSession
from ..database import DatabaseError
from pathlib import Path
import asyncio
import base64
import mimetypes
import uuid


class MessageHandlers:
    """Message, history and media handlers."""

    async def _message_status(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        client_request_id = str(payload.get("client_request_id", "")).strip()
        if not client_request_id:
            raise DatabaseError("请求 ID 不能为空")
        message = await asyncio.to_thread(self.database.get_message_by_request, int(session.user["id"]), client_request_id)
        result: dict[str, Any] = {"client_request_id": client_request_id, "found": message is not None}
        if message:
            result["message_id"] = message["id"]
        await session.send("message.status", result, request_id=request_id)

    async def _message_send(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        prior = await asyncio.to_thread(self.database.get_message_by_request, int(session.user["id"]), request_id)
        if prior:
            await session.send("ack", {"action": "message.send", "message_id": prior["id"], "duplicate": True}, request_id=request_id)
            return
        message_type = str(payload.get("message_type", "text"))
        content = str(payload.get("content", ""))
        if message_type not in {"text", "image", "sticker"}:
            raise DatabaseError("消息类型无效")
        if len(content) > MAX_TEXT_LENGTH:
            raise DatabaseError("文本消息过长")
        if scope_type == "group" and await asyncio.to_thread(
            self.database.is_muted, int(scope_id), int(session.user["id"])
        ):
            raise DatabaseError("你已被该群禁言")
        if scope_type == "private":
            await asyncio.to_thread(self.database.assert_private_can_send, scope_id)
        reply_to_id = int(payload["reply_to_id"]) if payload.get("reply_to_id") is not None else None
        reply_preview = None
        if reply_to_id is not None:
            original = await asyncio.to_thread(self.database.authorized_message, int(session.user["id"]), reply_to_id)
            if original["conversation_type"] != scope_type or original["conversation_id"] != scope_id or original["recalled"]:
                raise DatabaseError("引用消息不属于当前会话或已撤回")
            if original["message_type"] == "image":
                preview_content = "[图片]"
            elif original["message_type"] == "sticker":
                preview_content = f"[表情包] {original.get('sticker_name') or ''}".strip()
            elif str((original.get("card") or {}).get("kind")) == "summary":
                preview_content = "[群聊总结]"
            elif str((original.get("card") or {}).get("kind")) == "todo":
                preview_content = "[待办事项]"
            else:
                preview_content = original["content"][:80]
            reply_preview = f"{original['sender_name']}: {preview_content}"
        image_path = image_name = image_mime = None
        if message_type in {"image", "sticker"}:
            image_path, image_name, image_mime = await self._store_image(payload)
        elif not content.strip():
            raise DatabaseError("消息内容不能为空")
        sticker_id = sticker_name = sticker_category = None
        if message_type == "sticker":
            supplied_id = str(payload.get("sticker_id") or request_id).strip()[:80]
            sticker_id = f"user:{int(session.user['id'])}:{supplied_id or request_id}"
            sticker_name = str(payload.get("sticker_name") or Path(image_name or "表情包").stem).strip()[:80]
            sticker_name = sticker_name or "表情包"
            sticker_category = "用户表情"
        normalized_content = content.strip()
        raw_mentions = self._normalize_mention_offsets(
            content,
            payload.get("mentions"),
            str(payload.get("mention_index_unit", "unicode_codepoints")),
        )
        mentions = await self._validate_mentions(scope_type, scope_id, normalized_content, raw_mentions)
        message = await asyncio.to_thread(
            self.database.save_message,
            scope_type,
            scope_id,
            int(session.user["id"]),
            await asyncio.to_thread(self.database.name_in_scope, int(session.user["id"]), scope_type, scope_id),
            message_type,
            normalized_content,
            image_path=image_path,
            image_name=image_name,
            image_mime=image_mime,
            sticker_id=sticker_id,
            sticker_name=sticker_name,
            sticker_category=sticker_category,
            sticker_tags=[],
            mentions=mentions,
            reply_to_id=reply_to_id,
            reply_preview=reply_preview,
            client_request_id=request_id,
        )
        if message.get("duplicate"):
            await session.send("ack", {"action": "message.send", "message_id": message["id"], "duplicate": True}, request_id=request_id)
            return
        if scope_type != "private":
            await asyncio.to_thread(
                self.database.set_read_cursor, int(session.user["id"]), scope_type, scope_id, int(message["id"])
            )
        wire_message = await self._with_image_data(message)
        await self.emit_to_scope(scope_type, scope_id, "message.event", {"message": wire_message})
        await session.send("ack", {"action": "message.send", "message_id": message["id"]}, request_id=request_id)
        triggered: list[str] = []
        for item in mentions:
            if item["kind"] != "agent":
                continue
            agent_id = str(item.get("agent_id") or AGENT_MEMBER_ID)
            if agent_id not in triggered:
                triggered.append(agent_id)
        if triggered:
            prompt = normalized_content
            for item in reversed(mentions):
                if item["kind"] == "agent":
                    start = int(item["start"])
                    prompt = prompt[:start] + prompt[start + int(item["length"]):]
            prompt = prompt.strip() or "请总结最近 50 条消息"
            for agent_id in triggered[:4]:
                self._track(
                    asyncio.create_task(
                        self.agent.invoke(
                            int(session.user["id"]), scope_type, scope_id, prompt, agent_id
                        )
                    )
                )

    async def _validate_mentions(
        self, scope_type: str, scope_id: str, content: str, raw_mentions: Any
    ) -> list[dict[str, Any]]:
        if raw_mentions in (None, []):
            return []
        if scope_type != "group" or not isinstance(raw_mentions, list):
            raise DatabaseError("提及信息仅适用于群聊")
        members = await asyncio.to_thread(self.database.group_members, int(scope_id))
        allowed = {f"user:{member['id']}": member for member in members}
        agent_rows = await asyncio.to_thread(
            self.database.group_agents, int(scope_id), enabled_only=True
        )
        agents = {agent["member_id"]: agent for agent in agent_rows}
        normalized: list[dict[str, Any]] = []
        previous_end = -1
        for raw in sorted(raw_mentions, key=lambda item: int(item.get("start", -1)) if isinstance(item, dict) else -1):
            if not isinstance(raw, dict):
                raise DatabaseError("提及信息无效")
            member_id = str(raw.get("member_id", ""))
            start = int(raw.get("start", -1))
            length = int(raw.get("length", 0))
            if member_id in agents:
                kind = "agent"
                display = str(agents[member_id]["name"])
                if str(raw.get("display") or content[start + 1:start + length]) != display:
                    raise DatabaseError("提及名称与 Agent 不一致")
            elif member_id in allowed:
                member = allowed[member_id]
                names = {str(member[key]) for key in ("nickname", "display_name", "username") if member.get(key)}
                kind = "user"
                display = str(raw.get("display") or content[start + 1:start + length])
                if display not in names:
                    raise DatabaseError("提及名称与成员不一致")
            else:
                raise DatabaseError("被提及成员不属于当前群聊")
            token = f"@{display}"
            if start < 0 or length != len(token) or start < previous_end or content[start:start + length] != token:
                raise DatabaseError("提及位置与消息文本不一致")
            previous_end = start + length
            item = {
                "member_id": member_id,
                "kind": kind,
                "display": display,
                "start": start,
                "length": length,
            }
            if kind == "agent":
                item["agent_id"] = agents[member_id]["id"]
            normalized.append(item)
        return normalized

    def _normalize_mention_offsets(self, content: str, raw_mentions: Any, index_unit: str) -> Any:
        if not isinstance(raw_mentions, list):
            return raw_mentions
        if index_unit not in {"unicode_codepoints", "utf16"}:
            raise DatabaseError("不支持的提及位置单位")
        leading = len(content) - len(content.lstrip())
        normalized: list[Any] = []
        for raw in raw_mentions:
            if not isinstance(raw, dict):
                normalized.append(raw)
                continue
            item = dict(raw)
            start = int(item.get("start", -1))
            length = int(item.get("length", 0))
            if index_unit == "utf16" and start >= 0 and length >= 0:
                end = self._utf16_to_codepoint_index(content, start + length)
                start = self._utf16_to_codepoint_index(content, start)
                length = end - start
            item["start"] = start - leading
            item["length"] = length
            normalized.append(item)
        return normalized

    @staticmethod
    def _utf16_to_codepoint_index(text: str, position: int) -> int:
        if position < 0:
            return position
        units = 0
        for index, character in enumerate(text):
            if units >= position:
                return index
            units += len(character.encode("utf-16-le")) // 2
            if units > position:
                raise DatabaseError("UTF-16 提及位置落在字符中间")
        if units != position:
            raise DatabaseError("UTF-16 提及位置超出消息范围")
        return len(text)

    async def _store_image(self, payload: dict[str, Any]) -> tuple[str, str, str]:
        encoded = str(payload.get("image_data", ""))
        mime = str(payload.get("image_mime", ""))
        original_name = Path(str(payload.get("image_name", "image"))).name[:120]
        if mime not in ALLOWED_IMAGE_MIME:
            raise DatabaseError("仅支持 PNG、JPEG、GIF 和 WebP 图片")
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_IMAGE_SIZE:
            raise DatabaseError("图片不能为空且不能超过2 MiB")
        if not valid_image_signature(raw, mime):
            raise DatabaseError("图片内容与声明格式不一致")
        extension = mimetypes.guess_extension(mime) or ".img"
        filename = f"{uuid.uuid4().hex}{extension}"
        path = self.config.uploads_dir / filename
        await asyncio.to_thread(path.write_bytes, raw)
        return filename, original_name, mime

    async def _with_image_data(self, message: dict[str, Any]) -> dict[str, Any]:
        result = dict(message)
        image_path = result.get("image_path")
        if image_path and not result.get("recalled"):
            path = self.config.uploads_dir / Path(str(image_path)).name
            try:
                raw = await asyncio.to_thread(path.read_bytes)
                result["image_data"] = base64.b64encode(raw).decode("ascii")
            except OSError:
                result["image_missing"] = True
        result.pop("image_path", None)
        return result

    async def _media_get(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        message = await asyncio.to_thread(
            self.database.authorized_message,
            int(session.user["id"]),
            int(payload["message_id"]),
        )
        if message.get("message_type") not in {"image", "sticker"} or message.get("recalled"):
            raise DatabaseError("该消息没有可用图片")
        wire = await self._with_image_data(message)
        if not wire.get("image_data"):
            raise DatabaseError("图片文件不存在")
        await session.send("media.result", {"message": wire}, request_id=request_id)

    async def _history_sync(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        page = await asyncio.to_thread(
            self.database.history_page,
            int(session.user["id"]),
            scope_type,
            scope_id,
            before_id=(int(payload["before_id"]) if payload.get("before_id") is not None else None),
            after_id=(int(payload["after_id"]) if payload.get("after_id") is not None else None),
            limit=int(payload.get("limit", 50)),
        )
        if payload.get("media_mode") != "metadata":
            page["messages"] = [
                await self._with_image_data(message) for message in page["messages"]
            ]
        await session.send(
            "history.result",
            {
                "conversation_type": scope_type,
                "conversation_id": scope_id,
                **page,
            },
            request_id=request_id,
        )

    async def _history_search(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        page = await asyncio.to_thread(
            self.database.search_messages,
            int(session.user["id"]),
            scope_type,
            scope_id,
            str(payload.get("keyword", "")),
            int(payload.get("limit", 50)),
            int(payload["before_id"]) if payload.get("before_id") is not None else None,
            paged=True,
        )
        await session.send("history.result", {"conversation_type": scope_type, "conversation_id": scope_id,
                           "messages": page["messages"], "has_more": page["has_more"],
                           "keyword": str(payload.get("keyword", "")), "before_id": payload.get("before_id"),
                           "search": True}, request_id=request_id)

    async def _history_events(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        page = await asyncio.to_thread(self.database.message_events_for_user, int(session.user["id"]), max(0, int(payload.get("after_event_id", 0))))
        await session.send("history.events", page, request_id=request_id)

    async def _message_edit(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        message_id = int(payload["message_id"])
        original = await asyncio.to_thread(self.database.authorized_message, int(session.user["id"]), message_id)
        raw_content = str(payload.get("content", ""))
        content = raw_content.strip()
        if not content or len(content) > MAX_TEXT_LENGTH:
            raise DatabaseError("消息内容为空或过长")
        raw_mentions = self._normalize_mention_offsets(raw_content, payload.get("mentions", []), str(payload.get("mention_index_unit", "unicode_codepoints")))
        mentions = await self._validate_mentions(original["conversation_type"], original["conversation_id"], content, raw_mentions)
        message = await asyncio.to_thread(self.database.edit_message, message_id, int(session.user["id"]), content, mentions)
        await self.emit_to_scope(message["conversation_type"], message["conversation_id"], "message.edited", {"message": message})
        await session.send("ack", {"action": "message.edit", "message_id": message_id}, request_id=request_id)

    async def _history_read(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        scope_type, scope_id = await self._resolve_scope(session, payload)
        message_id = int(payload["message_id"])
        advanced = await asyncio.to_thread(
            self.database.set_read_cursor,
            int(session.user["id"]),
            scope_type,
            scope_id,
            message_id,
        )
        await session.send("ack", {"action": "history.read"}, request_id=request_id)
        if advanced and scope_type == "private":
            await self.emit_to_scope(scope_type, scope_id, "private.read", {
                "conversation_id": scope_id,
                "reader_id": int(session.user["id"]),
                "message_id": message_id,
            })

    async def _message_recall(self, session: ClientSession, payload: dict[str, Any], request_id: str) -> None:
        message = await asyncio.to_thread(self.database.recall_message, int(payload["message_id"]), int(session.user["id"]))
        await self.emit_to_scope(message["conversation_type"], message["conversation_id"], "message.recalled", {"message": message})
        await session.send("ack", {"action": "message.recall", "message_id": message["id"]}, request_id=request_id)
