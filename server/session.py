"""One connected TCP client session."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

from shared.protocol import encode_frame, envelope


@dataclass(eq=False)
class ClientSession:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    user: dict[str, Any] | None = None
    seen_requests: set[str] = field(default_factory=set)

    async def send(self, message_type: str, payload: dict[str, Any], *, request_id: str | None = None) -> None:
        message = envelope(message_type, payload, request_id=request_id)
        async with self.send_lock:
            self.writer.write(encode_frame(message))
            await self.writer.drain()

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()
