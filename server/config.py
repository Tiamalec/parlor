from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PermissionError(f"配置文件必须是普通文件：{path}")
    if os.name == "posix":
        if metadata.st_uid != os.getuid():
            raise PermissionError(f"配置文件所有者不安全：{path}")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError(f"配置文件权限过宽，请执行 chmod 600 {path}")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    data_dir: Path
    database_path: Path
    uploads_dir: Path
    admin_username: str | None
    admin_password: str | None
    password_pbkdf2_iterations: int
    agent_enabled: bool
    agent_provider: str
    agent_base_url: str
    agent_api_key: str
    agent_model: str
    agent_reasoning_effort: str
    agent_temperature: float
    agent_context_tokens: int
    agent_max_output_tokens: int
    agent_max_concurrency: int
    agent_timeout_seconds: int
    agent_cloud_fallback: bool
    agent_fallback_base_url: str | None
    agent_fallback_api_key: str | None
    agent_fallback_model: str | None
    admin_enabled: bool
    admin_host: str
    admin_port: int
    admin_session_ttl_seconds: int
    admin_log_capacity: int
    admin_cookie_secure: bool
    admin_max_body_bytes: int
    sticker_auto_interval_seconds: int = 90

    @classmethod
    def from_env(cls) -> "Config":
        root = Path.cwd()
        load_dotenv(root / ".env")
        data_dir = Path(os.getenv("CHAT_DATA_DIR", root / "data")).expanduser().resolve()
        return cls(
            host=os.getenv("CHAT_HOST", "127.0.0.1"),
            port=int(os.getenv("CHAT_PORT", "8765")),
            data_dir=data_dir,
            database_path=data_dir / "chatroom.sqlite3",
            uploads_dir=data_dir / "uploads",
            admin_username=os.getenv("CHAT_ADMIN_USERNAME") or None,
            admin_password=os.getenv("CHAT_ADMIN_PASSWORD") or None,
            password_pbkdf2_iterations=max(100_000, int(os.getenv("PASSWORD_PBKDF2_ITERATIONS", "310000"))),
            agent_enabled=_bool("AGENT_ENABLED", True),
            agent_provider=os.getenv("AGENT_PROVIDER", "ollama"),
            agent_base_url=os.getenv("AGENT_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/"),
            agent_api_key=os.getenv("AGENT_API_KEY", "ollama"),
            agent_model=os.getenv("AGENT_MODEL", "chatroom-qwen3.5-4b"),
            agent_reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "none"),
            agent_temperature=float(os.getenv("AGENT_TEMPERATURE", "0.2")),
            agent_context_tokens=int(os.getenv("AGENT_CONTEXT_TOKENS", "8192")),
            agent_max_output_tokens=int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "512")),
            agent_max_concurrency=max(1, int(os.getenv("AGENT_MAX_CONCURRENCY", "1"))),
            agent_timeout_seconds=max(1, int(os.getenv("AGENT_TIMEOUT_SECONDS", "45"))),
            agent_cloud_fallback=_bool("AGENT_CLOUD_FALLBACK", False),
            agent_fallback_base_url=(os.getenv("AGENT_FALLBACK_BASE_URL") or None),
            agent_fallback_api_key=(os.getenv("AGENT_FALLBACK_API_KEY") or None),
            agent_fallback_model=(os.getenv("AGENT_FALLBACK_MODEL") or None),
            admin_enabled=_bool("ADMIN_ENABLED", True),
            admin_host=os.getenv("ADMIN_HOST", "127.0.0.1"),
            admin_port=int(os.getenv("ADMIN_PORT", "8766")),
            admin_session_ttl_seconds=max(300, int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "28800"))),
            admin_log_capacity=max(100, min(int(os.getenv("ADMIN_LOG_CAPACITY", "500")), 5000)),
            admin_cookie_secure=_bool("ADMIN_COOKIE_SECURE", False),
            admin_max_body_bytes=max(1024, min(int(os.getenv("ADMIN_MAX_BODY_BYTES", "3000000")), 3000000)),
            sticker_auto_interval_seconds=max(10, int(os.getenv("STICKER_AUTO_INTERVAL_SECONDS", "90"))),
        )

