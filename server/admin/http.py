from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from server.config import Config

from .auth import AdminAuthError, AdminSession
from .service import AdminRequestError, AdminService


LOGGER = logging.getLogger("chatroom.admin")
COOKIE_NAME = "chatroom_admin_session"


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes
    source_ip: str

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            value = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdminRequestError("请求 JSON 无效") from exc
        if not isinstance(value, dict):
            raise AdminRequestError("请求 JSON 必须是对象")
        return value

    def cookie(self, name: str) -> str | None:
        for item in self.headers.get("cookie", "").split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key == name:
                return value
        return None


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] | None = None

    @classmethod
    def json(cls, value: Any, status: int = 200, headers: dict[str, str] | None = None) -> "Response":
        return cls(
            status,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers=headers,
        )


class AdminHttpServer:
    def __init__(self, config: Config, service: AdminService) -> None:
        self.config = config
        self.service = service
        self._server: asyncio.AbstractServer | None = None
        self._connection_tasks: set[asyncio.Task[Any]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self.static_dir = Path(__file__).with_name("static")

    async def start(self) -> None:
        self._validate_bind_policy()
        self._server = await asyncio.start_server(
            self._handle_connection, self.config.admin_host, self.config.admin_port
        )
        addresses = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        LOGGER.info("管理面板已启动：http://%s", addresses)

    async def serve_forever(self) -> None:
        if not self._server:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._writers):
            writer.close()
        tasks = [task for task in self._connection_tasks if task is not asyncio.current_task()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.service.close()

    def _validate_bind_policy(self) -> None:
        if self.config.admin_host not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("管理面板只允许监听本机地址；远程访问请使用 HTTPS 反向代理")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task:
            self._connection_tasks.add(task)
        self._writers.add(writer)
        peer = writer.get_extra_info("peername")
        source_ip = str(peer[0]) if isinstance(peer, tuple) and peer else "unknown"
        try:
            request = await asyncio.wait_for(
                self._read_request(reader, source_ip), timeout=10
            )
            response = await self._route(request)
        except asyncio.TimeoutError:
            response = self._error(408, "请求超时")
        except AdminAuthError as exc:
            status = 403 if "CSRF" in str(exc) else 401
            response = self._error(status, str(exc))
        except AdminRequestError as exc:
            response = self._error(400, str(exc))
        except ValueError as exc:
            response = self._error(400, str(exc))
        except Exception:
            LOGGER.exception("处理管理请求失败")
            response = self._error(500, "服务器内部错误")
        try:
            writer.write(self._encode_response(response))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self._writers.discard(writer)
            if task:
                self._connection_tasks.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_request(self, reader: asyncio.StreamReader, source_ip: str) -> Request:
        request_line = await reader.readline()
        if not request_line or len(request_line) > 8192:
            raise AdminRequestError("请求行无效")
        try:
            method, target, version = request_line.decode("ascii").strip().split(" ", 2)
        except (UnicodeDecodeError, ValueError) as exc:
            raise AdminRequestError("请求行无效") from exc
        if version != "HTTP/1.1" or method not in {"GET", "POST", "DELETE"}:
            raise AdminRequestError("不支持的 HTTP 请求")
        headers: dict[str, str] = {}
        total_header_bytes = len(request_line)
        while True:
            line = await reader.readline()
            total_header_bytes += len(line)
            if total_header_bytes > 16 * 1024:
                raise AdminRequestError("请求头过大")
            if line in {b"\r\n", b"\n"}:
                break
            if not line:
                raise AdminRequestError("请求头不完整")
            try:
                name, separator, value = line.decode("iso-8859-1").partition(":")
            except UnicodeDecodeError as exc:
                raise AdminRequestError("请求头无效") from exc
            if not separator:
                raise AdminRequestError("请求头无效")
            key = name.strip().lower()
            if key in headers:
                raise AdminRequestError("不允许重复请求头")
            headers[key] = value.strip()
        if "transfer-encoding" in headers:
            raise AdminRequestError("不支持分块请求")
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError as exc:
            raise AdminRequestError("Content-Length 无效") from exc
        if content_length < 0 or content_length > self.config.admin_max_body_bytes:
            raise AdminRequestError("请求体过大")
        if content_length and headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise AdminRequestError("请求体必须是 application/json")
        body = await reader.readexactly(content_length) if content_length else b""
        parsed = urlsplit(target)
        if not parsed.path.startswith("/"):
            raise AdminRequestError("请求路径无效")
        return Request(method, parsed.path, parse_qs(parsed.query), headers, body, source_ip)

    async def _route(self, request: Request) -> Response:
        if request.method == "GET" and request.path in {"/", "/index.html", "/app.js", "/styles.css"}:
            return self._static(request.path)
        if request.path == "/api/v1/admin/session" and request.method == "POST":
            payload = request.json()
            token, session = await self.service.login(
                str(payload.get("username", "")), str(payload.get("password", "")), request.source_ip
            )
            cookie = self._session_cookie(token)
            return Response.json(session.public_dict(), headers={"Set-Cookie": cookie})

        token = request.cookie(COOKIE_NAME)
        is_write = request.method in {"POST", "DELETE"}
        csrf = request.headers.get("x-csrf-token", "") if is_write else None
        session = self.service.sessions.require(token, request.source_ip, csrf_token=csrf)
        await self.service.require_current_admin(session, token)

        if request.path == "/api/v1/admin/session" and request.method == "DELETE":
            await self.service.logout(session, token)
            return Response.json(
                {"ok": True}, headers={"Set-Cookie": self._expired_cookie()}
            )
        if request.path == "/api/v1/admin/me" and request.method == "GET":
            return Response.json(session.public_dict())
        if request.path == "/api/v1/admin/stickers" and request.method == "GET":
            return Response.json({"stickers": await self.service.stickers()})
        if request.path == "/api/v1/admin/stickers" and request.method == "POST":
            return Response.json(await self.service.save_sticker(session, request.json()), status=201)
        sticker_prefix = "/api/v1/admin/stickers/"
        if request.path.startswith(sticker_prefix):
            suffix = request.path[len(sticker_prefix):]
            parts = suffix.split("/")
            if len(parts) == 1 and parts[0]:
                if request.method == "POST":
                    return Response.json(await self.service.save_sticker(session, request.json(), parts[0]))
                if request.method == "DELETE":
                    return Response.json(await self.service.delete_sticker(session, parts[0]))
            if len(parts) == 2 and parts[0] and parts[1] == "media" and request.method == "GET":
                raw, mime = await self.service.sticker_media(parts[0])
                return Response(200, raw, content_type=mime)
        if request.path == "/api/v1/admin/dashboard" and request.method == "GET":
            return Response.json(await self.service.dashboard())
        if request.path == "/api/v1/admin/logs" and request.method == "GET":
            return Response.json(
                {
                    "logs": self.service.logs.list(
                        self._query_int(request, "after", 0),
                        self._query_int(request, "limit", 100),
                    )
                }
            )
        if request.path == "/api/v1/admin/agents/status" and request.method == "GET":
            return Response.json(self.service.agent.status())
        if request.path == "/api/v1/admin/agents" and request.method == "GET":
            return Response.json({"agents": await self.service.agent_cards(session)})
        if request.path == "/api/v1/admin/role-cards" and request.method == "GET":
            return Response.json({"role_cards": await self.service.role_cards(session)})
        if request.path == "/api/v1/admin/role-cards" and request.method == "POST":
            return Response.json(
                await self.service.save_role_card(session, request.json()), status=201
            )
        role_card_prefix = "/api/v1/admin/role-cards/"
        if request.path.startswith(role_card_prefix):
            agent_id = request.path[len(role_card_prefix):]
            if agent_id and "/" not in agent_id:
                if request.method == "POST":
                    return Response.json(
                        await self.service.save_role_card(
                            session, request.json(), agent_id
                        )
                    )
                if request.method == "DELETE":
                    return Response.json(
                        await self.service.delete_role_card(session, agent_id)
                    )
        if request.path == "/api/v1/admin/agents/enabled" and request.method == "POST":
            payload = request.json()
            if "enabled" not in payload or type(payload["enabled"]) is not bool:
                raise AdminRequestError("enabled 必须是 boolean")
            return Response.json(
                await self.service.set_agent_enabled(session, payload["enabled"])
            )
        if request.path == "/api/v1/admin/agents/runs" and request.method == "GET":
            return Response.json(
                {"runs": await self.service.agent_runs(self._query_int(request, "limit", 100))}
            )
        if request.path == "/api/v1/admin/agents/tool-calls" and request.method == "GET":
            return Response.json(
                {"tool_calls": await self.service.tool_calls(self._query_int(request, "limit", 100))}
            )
        if request.path == "/api/v1/admin/model-provider" and request.method == "GET":
            return Response.json(self.service.provider_view())
        if request.path == "/api/v1/admin/model-provider/test-apply" and request.method == "POST":
            return Response.json(await self.service.test_and_apply_provider(session, request.json()))
        if request.path == "/api/v1/admin/model-presets" and request.method == "GET":
            return Response.json(self.service.provider_presets())
        if request.path == "/api/v1/admin/model-profiles" and request.method == "GET":
            return Response.json(self.service.model_profiles())
        if request.path == "/api/v1/admin/model-profiles" and request.method == "POST":
            return Response.json(await self.service.save_model_profile(session, request.json()), status=201)
        if request.path == "/api/v1/admin/model-profiles/discover-models" and request.method == "POST":
            return Response.json(await self.service.discover_models(request.json()))
        profile_prefix = "/api/v1/admin/model-profiles/"
        if request.path.startswith(profile_prefix):
            suffix = request.path[len(profile_prefix):]
            parts = suffix.split("/")
            profile_id = parts[0]
            if not profile_id or len(parts) > 2:
                return self._error(404, "接口不存在")
            if len(parts) == 1 and request.method == "POST":
                return Response.json(await self.service.save_model_profile(session, request.json(), profile_id))
            if len(parts) == 1 and request.method == "DELETE":
                return Response.json(await self.service.delete_model_profile(session, profile_id))
            if len(parts) == 2 and parts[1] == "test" and request.method == "POST":
                return Response.json(await self.service.test_model_profile(session, profile_id))
            if len(parts) == 2 and parts[1] == "activate" and request.method == "POST":
                return Response.json(await self.service.activate_model_profile(session, profile_id))
        if request.path == "/api/v1/admin/users" and request.method == "GET":
            return Response.json({"users": await self.service.users()})
        if request.path == "/api/v1/admin/groups" and request.method == "GET":
            return Response.json({"groups": await self.service.groups()})
        if request.path == "/api/v1/admin/audit" and request.method == "GET":
            return Response.json(
                {"audit": await self.service.audit(self._query_int(request, "limit", 100))}
            )
        return self._error(404, "接口不存在")

    def _static(self, path: str) -> Response:
        name = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
        if name not in {"index.html", "app.js", "styles.css"}:
            return self._error(404, "资源不存在")
        try:
            body = (self.static_dir / name).read_bytes()
        except OSError:
            return self._error(404, "资源不存在")
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        return Response(200, body, content_type)

    def _session_cookie(self, token: str) -> str:
        parts = [
            f"{COOKIE_NAME}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Strict",
            f"Max-Age={self.config.admin_session_ttl_seconds}",
        ]
        if self.config.admin_cookie_secure:
            parts.append("Secure")
        return "; ".join(parts)

    def _expired_cookie(self) -> str:
        parts = [f"{COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Strict", "Max-Age=0"]
        if self.config.admin_cookie_secure:
            parts.append("Secure")
        return "; ".join(parts)

    @staticmethod
    def _query_int(request: Request, name: str, default: int) -> int:
        values = request.query.get(name)
        if not values:
            return default
        try:
            return int(values[0])
        except ValueError:
            return default

    @staticmethod
    def _error(status: int, message: str) -> Response:
        return Response.json({"error": message}, status=status)

    @staticmethod
    def _encode_response(response: Response) -> bytes:
        try:
            reason = HTTPStatus(response.status).phrase
        except ValueError:
            reason = "Unknown"
        headers = {
            "Content-Type": response.content_type,
            "Content-Length": str(len(response.body)),
            "Connection": "close",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        }
        headers.update(response.headers or {})
        head = [f"HTTP/1.1 {response.status} {reason}"]
        head.extend(f"{name}: {value}" for name, value in headers.items())
        return ("\r\n".join(head) + "\r\n\r\n").encode("iso-8859-1") + response.body

