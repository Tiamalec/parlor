"""Runtime state shared by Agent service parts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..model_provider import ModelProvider


EventCallback = Callable[[str, str, str, dict[str, Any]], Awaitable[None]]
TodoEventCallback = Callable[[int, str, dict[str, Any]], Awaitable[None]]


@dataclass
class AgentRuntime:
    provider: ModelProvider
    fallback_provider: ModelProvider | None
    semaphore: asyncio.Semaphore
    timeout_seconds: int
    generation: int
    identity: str
    agent: dict[str, Any]
    model_label: str
    model_name: str
