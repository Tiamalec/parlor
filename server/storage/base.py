"""Shared SQLite primitives used by storage domains."""

from __future__ import annotations

import sqlite3
from typing import Any


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
        low, high = int(parts[1]), int(parts[2])
        return (low, high) if low > 0 and high > 0 and low != high else None
    except ValueError:
        return None
