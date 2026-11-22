"""Per-conversation composer drafts with debounced local persistence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .models import ConversationKey


def _clean_mentions(mentions: Any) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    if not isinstance(mentions, (list, tuple)):
        return cleaned
    for item in mentions:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start", -1))
            length = int(item.get("length", 0))
        except (TypeError, ValueError):
            continue
        if start < 0 or length <= 0:
            continue
        cleaned.append(
            {
                "start": start,
                "length": length,
                "member_id": str(item.get("member_id", "")),
                "kind": str(item.get("kind", "user")),
                "display": str(item.get("display", "")),
            }
        )
    return sorted(cleaned, key=lambda item: item["start"])


@dataclass(frozen=True, slots=True)
class Draft:
    """Unsent composer state for one stable conversation key.

    Text, styled mention ranges, and the reply target are persisted.  The
    sticker picker, the transient mention candidate popup, and an uncommitted
    IME preedit string are UI-session state and are intentionally not stored.
    """

    text: str = ""
    mentions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    reply_to_id: int | None = None
    updated_at: float = 0.0

    @classmethod
    def create(
        cls,
        text: str,
        mentions: Any = None,
        reply_to_id: int | None = None,
    ) -> "Draft":
        parsed_reply = None
        if reply_to_id is not None:
            try:
                parsed_reply = int(reply_to_id)
            except (TypeError, ValueError):
                parsed_reply = None
        return cls(
            text=str(text),
            mentions=tuple(_clean_mentions(mentions)),
            reply_to_id=parsed_reply,
            updated_at=time.time(),
        )

    @classmethod
    def from_payload(cls, payload: Any) -> "Draft | None":
        if not isinstance(payload, dict):
            return None
        text = payload.get("text", "")
        if not isinstance(text, str):
            return None
        reply = payload.get("reply_to_id")
        try:
            parsed_reply = int(reply) if reply is not None else None
        except (TypeError, ValueError):
            parsed_reply = None
        try:
            updated = float(payload.get("updated_at", 0) or 0)
        except (TypeError, ValueError):
            updated = 0.0
        return cls(
            text=text,
            mentions=tuple(_clean_mentions(payload.get("mentions"))),
            reply_to_id=parsed_reply,
            updated_at=updated,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "mentions": [dict(item) for item in self.mentions],
            "reply_to_id": self.reply_to_id,
            "updated_at": self.updated_at,
        }

    def preview(self, limit: int = 52) -> str:
        clean = " ".join(self.text.split())
        if len(clean) <= limit:
            return clean
        return f"{clean[: max(1, limit - 1)]}…"

    def same_content(self, other: "Draft | None") -> bool:
        return bool(
            other is not None
            and self.text == other.text
            and self.mentions == other.mentions
            and self.reply_to_id == other.reply_to_id
        )


class DraftStore:
    """In-memory drafts keyed by conversation, persisted as one JSON document per account."""

    SETTINGS_PREFIX = "chat/drafts"

    def __init__(self, settings: Any = None, *, account: str = "anonymous") -> None:
        self._settings = settings
        self._account = self._normalize_account(account)
        self._drafts: dict[ConversationKey, Draft] = {}
        self._dirty = False
        self._load()

    @staticmethod
    def _normalize_account(account: Any) -> str:
        value = str(account or "").strip()
        return value or "anonymous"

    @property
    def account(self) -> str:
        return self._account

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _settings_key(self) -> str:
        return f"{self.SETTINGS_PREFIX}/{quote(self._account, safe='')}"

    def _load(self) -> None:
        self._drafts = {}
        self._dirty = False
        if self._settings is None:
            return
        raw = self._settings.value(self._settings_key(), "")
        if not raw:
            return
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = bytes(raw).decode("utf-8")
            payload = json.loads(str(raw))
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return
        records = payload.get("drafts") if isinstance(payload, dict) else None
        if not isinstance(records, dict):
            return
        for key_text, record in records.items():
            try:
                kind, identifier = str(key_text).split(":", 1)
            except ValueError:
                continue
            if not kind or not identifier:
                continue
            draft = Draft.from_payload(record)
            if draft is not None:
                self._drafts[ConversationKey(kind, identifier)] = draft

    def set_account(self, account: Any) -> dict[ConversationKey, Draft]:
        normalized = self._normalize_account(account)
        if normalized == self._account:
            return dict(self._drafts)
        self.flush()
        self._account = normalized
        self._load()
        return dict(self._drafts)

    def get(self, key: ConversationKey) -> Draft | None:
        return self._drafts.get(key)

    def put(self, key: ConversationKey, draft: Draft) -> None:
        if not draft.text:
            self.remove(key)
            return
        if self._drafts.get(key) == draft:
            return
        self._drafts[key] = draft
        self._dirty = True

    def remove(self, key: ConversationKey) -> bool:
        if key not in self._drafts:
            return False
        self._drafts.pop(key, None)
        self._dirty = True
        return True

    def all(self) -> dict[ConversationKey, Draft]:
        return dict(self._drafts)

    def previews(self) -> dict[ConversationKey, str]:
        return {key: draft.preview() for key, draft in self._drafts.items() if draft.text}

    def flush(self) -> None:
        if not self._dirty or self._settings is None:
            self._dirty = False
            return
        payload = {
            "version": 1,
            "drafts": {
                key.storage_id: draft.to_payload()
                for key, draft in self._drafts.items()
                if draft.text
            },
        }
        self._settings.setValue(
            self._settings_key(),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        sync = getattr(self._settings, "sync", None)
        if callable(sync):
            sync()
        self._dirty = False
