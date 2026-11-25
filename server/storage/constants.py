"""Storage-layer constants shared by repositories."""

from __future__ import annotations

import re
from pathlib import Path


PBKDF2_ITERATIONS = 310_000
USERNAME_RE = re.compile(r"^[\w\-\u4e00-\u9fff]{3,20}$", re.UNICODE)
PRESET_AVATAR_DIR = Path(__file__).resolve().parent.parent / "assets" / "agent_avatars"
