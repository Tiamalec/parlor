from __future__ import annotations

import asyncio
import json
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


PROTOCOL_VERSION = 1
HEADER_SIZE = 4
MAX_FRAME_SIZE = 4 * 1024 * 1024


class ProtocolError(ValueError):
    """Raised when a peer sends an invalid frame."""


def envelope(message_type: str, payload: dict[str, Any] | None = None, *, request_id: str | None = None) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "request_id": request_id or str(uuid.uuid4()),
        "type": message_type,
        "timestamp": time.time(),
        "payload": payload or {},
    }


def validate_message(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    if message.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    if not isinstance(message.get("request_id"), str) or not message["request_id"]:
        raise ProtocolError("request_id is required")
    if not isinstance(message.get("type"), str) or not message["type"]:
        raise ProtocolError("type is required")
    if not isinstance(message.get("payload"), dict):
        raise ProtocolError("payload must be an object")
    return message


def encode_frame(message: dict[str, Any]) -> bytes:
    validate_message(message)
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_FRAME_SIZE:
        raise ProtocolError("frame exceeds size limit")
    return struct.pack("!I", len(raw)) + raw


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    header = await reader.readexactly(HEADER_SIZE)
    (size,) = struct.unpack("!I", header)
    if size <= 0 or size > MAX_FRAME_SIZE:
        raise ProtocolError("invalid frame size")
    raw = await reader.readexactly(size)
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON frame") from exc
    return validate_message(message)


@dataclass
class FrameDecoder:
    """Incremental decoder used by the blocking desktop client."""

    buffer: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self.buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while len(self.buffer) >= HEADER_SIZE:
            (size,) = struct.unpack("!I", self.buffer[:HEADER_SIZE])
            if size <= 0 or size > MAX_FRAME_SIZE:
                self.buffer.clear()
                raise ProtocolError("invalid frame size")
            frame_end = HEADER_SIZE + size
            if len(self.buffer) < frame_end:
                break
            raw = bytes(self.buffer[HEADER_SIZE:frame_end])
            del self.buffer[:frame_end]
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError("invalid JSON frame") from exc
            messages.append(validate_message(decoded))
        return messages

