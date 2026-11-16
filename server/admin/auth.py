from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any


class AdminAuthError(ValueError):
    pass


@dataclass(frozen=True)
class AdminSession:
    user_id: int
    username: str
    csrf_token: str
    source_ip: str
    expires_at: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "user": {"id": self.user_id, "username": self.username, "role": "admin"},
            "csrf_token": self.csrf_token,
            "expires_at": self.expires_at,
        }


class AdminSessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = max(300, int(ttl_seconds))
        self._sessions: dict[str, AdminSession] = {}
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, user: dict[str, Any], source_ip: str) -> tuple[str, AdminSession]:
        self.cleanup()
        token = secrets.token_urlsafe(32)
        session = AdminSession(
            user_id=int(user["id"]),
            username=str(user["username"]),
            csrf_token=secrets.token_urlsafe(24),
            source_ip=source_ip,
            expires_at=time.time() + self.ttl_seconds,
        )
        self._sessions[self._digest(token)] = session
        self._failures.pop(source_ip, None)
        return token, session

    def get(self, token: str | None, source_ip: str) -> AdminSession:
        self.cleanup()
        if not token:
            raise AdminAuthError("请先登录")
        session = self._sessions.get(self._digest(token))
        if not session or session.source_ip != source_ip:
            raise AdminAuthError("管理会话无效或已过期")
        return session

    def require(
        self, token: str | None, source_ip: str, *, csrf_token: str | None = None
    ) -> AdminSession:
        session = self.get(token, source_ip)
        if csrf_token is not None and not secrets.compare_digest(session.csrf_token, csrf_token):
            raise AdminAuthError("CSRF 校验失败")
        return session

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(self._digest(token), None)

    def allow_login(self, source_ip: str) -> bool:
        now = time.time()
        failures = self._failures[source_ip]
        while failures and failures[0] <= now - 300:
            failures.popleft()
        allowed = len(failures) < 8
        if not failures:
            self._failures.pop(source_ip, None)
        return allowed

    def record_failure(self, source_ip: str) -> None:
        if source_ip not in self._failures and len(self._failures) >= 4096:
            self._failures.pop(next(iter(self._failures)))
        self._failures[source_ip].append(time.time())

    def cleanup(self) -> None:
        now = time.time()
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)

