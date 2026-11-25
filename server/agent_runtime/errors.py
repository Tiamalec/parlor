"""Agent runtime exceptions."""

from __future__ import annotations

from ..database import DatabaseError


class ToolApprovalRequired(DatabaseError):
    """A write requires a fresh, explicit confirmation from the requesting user."""
