"""Profile avatar handlers."""

from __future__ import annotations

from ..media import ALLOWED_IMAGE_MIME, MAX_AVATAR_SIZE, valid_avatar
from typing import Any
from ..session import ClientSession
from ..database import DatabaseError
from pathlib import Path
import asyncio
import base64
import contextlib
import mimetypes
import uuid


class ProfileHandlers:
    """Profile avatar handlers."""

    async def _profile_avatar_set(
        self, session: ClientSession, payload: dict[str, Any], request_id: str
    ) -> None:
        user_id = int(session.user["id"])
        encoded = str(payload.get("image_data", ""))
        path: str | None = None
        mime: str | None = None
        if encoded:
            mime = str(payload.get("image_mime", ""))
            if mime not in ALLOWED_IMAGE_MIME:
                raise DatabaseError("头像仅支持 PNG、JPEG、GIF 或 WebP")
            raw = base64.b64decode(encoded, validate=True)
            if not raw or len(raw) > MAX_AVATAR_SIZE:
                raise DatabaseError("头像大小需在 1 字节至 2 MiB 之间")
            if not valid_avatar(raw, mime):
                raise DatabaseError("头像文件内容与格式不匹配")
            avatar_dir = self.config.uploads_dir / "avatars"
            avatar_dir.mkdir(parents=True, exist_ok=True)
            self.database._secure_path(avatar_dir, 0o700)
            suffix = mimetypes.guess_extension(mime) or ".img"
            target = avatar_dir / f"{user_id}-{uuid.uuid4().hex}{suffix}"
            target.write_bytes(raw)
            self.database._secure_path(target, 0o600)
            path = str(target)
        user = await asyncio.to_thread(self.database.set_avatar, user_id, path, mime)
        previous = user.pop("previous_avatar_path", None)
        if previous and previous != path:
            previous_path = Path(str(previous))
            if not previous_path.is_absolute():
                previous_path = self.config.uploads_dir / previous_path
            avatar_root = (self.config.uploads_dir / "avatars").resolve()
            with contextlib.suppress(OSError, ValueError):
                if previous_path.resolve().is_relative_to(avatar_root):
                    previous_path.unlink(missing_ok=True)
        session.user.update(user)
        await session.send("ack", {"action": "profile.avatar.set"}, request_id=request_id)
        await self._broadcast_all("profile.updated", {"user": user})

    async def _profile_avatar_get(
        self, session: ClientSession, payload: dict[str, Any], request_id: str
    ) -> None:
        user_id = int(payload.get("user_id", session.user["id"]))
        avatar = await asyncio.to_thread(self.database.avatar_file, user_id)
        response: dict[str, Any] = {
            "user_id": user_id,
            "avatar_version": avatar["version"],
            "avatar_mime": avatar["mime"],
        }
        path = avatar.get("resolved_path")
        if path:
            candidate = Path(str(path))
            avatar_root = (self.config.uploads_dir / "avatars").resolve()
            try:
                if not candidate.resolve().is_relative_to(avatar_root):
                    raise DatabaseError("头像路径无效")
                raw = candidate.read_bytes()
                if len(raw) > MAX_AVATAR_SIZE or not valid_avatar(raw, str(avatar["mime"])):
                    raise DatabaseError("头像文件无效")
                response["image_data"] = base64.b64encode(raw).decode("ascii")
            except OSError:
                response["load_failed"] = True
        await session.send("profile.avatar.result", response, request_id=request_id)
