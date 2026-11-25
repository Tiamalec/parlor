"""Image validation for chat uploads and avatars."""

from __future__ import annotations

import struct


MAX_IMAGE_SIZE = 2 * 1024 * 1024
MAX_AVATAR_SIZE = 2 * 1024 * 1024
MAX_AVATAR_DIMENSION = 4096
MAX_AVATAR_PIXELS = 16_000_000
ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def valid_image_signature(raw: bytes, mime: str) -> bool:
    if mime == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    return False


def image_dimensions(raw: bytes, mime: str) -> tuple[int, int] | None:
    """Read dimensions from supported image headers without decoding pixel data."""
    if mime == "image/png" and len(raw) >= 24:
        return struct.unpack(">II", raw[16:24])
    if mime == "image/gif" and len(raw) >= 10:
        return struct.unpack("<HH", raw[6:10])
    if mime == "image/webp" and len(raw) >= 30:
        chunk = raw[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(raw[24:27], "little"), 1 + int.from_bytes(raw[27:30], "little")
        if chunk == b"VP8 " and raw[23:26] == b"\x9d\x01\x2a":
            width, height = struct.unpack("<HH", raw[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if chunk == b"VP8L" and len(raw) >= 25 and raw[20] == 0x2F:
            bits = int.from_bytes(raw[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        return None
    if mime == "image/jpeg":
        offset = 2
        while offset + 4 <= len(raw):
            if raw[offset] != 0xFF:
                offset += 1
                continue
            marker = raw[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(raw):
                break
            segment_length = int.from_bytes(raw[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(raw):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if segment_length >= 7:
                    height = int.from_bytes(raw[offset + 3:offset + 5], "big")
                    width = int.from_bytes(raw[offset + 5:offset + 7], "big")
                    return width, height
                break
            offset += segment_length
    return None


def valid_avatar(raw: bytes, mime: str) -> bool:
    if not valid_image_signature(raw, mime):
        return False
    dimensions = image_dimensions(raw, mime)
    if not dimensions:
        return False
    width, height = dimensions
    return (
        0 < width <= MAX_AVATAR_DIMENSION
        and 0 < height <= MAX_AVATAR_DIMENSION
        and width * height <= MAX_AVATAR_PIXELS
    )
