from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from collections import deque
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from server.agent import AgentService
from server.database import Database
from server.model_provider import AgentUnavailable, ProviderSettings, create_provider, normalize_protocol
from server.provider_presets import PROVIDER_PRESETS

from .auth import AdminAuthError, AdminSession, AdminSessionStore
from .config_store import ModelProfile, ProviderConfigState, ProviderConfigStore


class AdminRequestError(ValueError):
    pass


class LogBufferHandler(logging.Handler):
    _SECRET_PATTERNS = (
        re.compile(r"(?i)(authorization\s*[:=]\s*)[^\r\n,;]+"),
        re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+"),
        re.compile(
            r"(?i)((?:api[_-]?key|password|cookie|token|secret|session|set-cookie)[\"']?\s*[:=]\s*[\"']?)[^,;\"']+"
        ),
    )

    def __init__(self, capacity: int) -> None:
        super().__init__(logging.INFO)
        self.records: deque[dict[str, Any]] = deque(maxlen=max(100, capacity))
        self._next_id = 1

    @classmethod
    def sanitize(cls, value: object) -> str:
        text = str(value).replace("\r", " ").replace("\n", " ")[:2000]
        for pattern in cls._SECRET_PATTERNS:
            text = pattern.sub(r"\1[REDACTED]", text)
        return text

    def emit(self, record: logging.LogRecord) -> None:
        try:
            item = {
                "id": self._next_id,
                "timestamp": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": self.sanitize(record.getMessage()),
            }
            self._next_id += 1
            self.records.append(item)
        except Exception:
            self.handleError(record)

    def list(self, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        return [dict(item) for item in self.records if int(item["id"]) > int(after)][-bounded:]


class AdminService:
    ERROR_MESSAGES = {
        "authentication": "API Key 无效或无权访问",
        "network": "模型服务地址不可达或请求超时",
        "not_found": "接口或模型不存在",
        "rate_limited": "模型服务限流或额度不足",
        "invalid_response": "模型服务响应格式不兼容",
        "invalid_request": "模型请求配置无效",
        "http_error": "模型服务返回错误",
        "unavailable": "模型服务不可用",
    }

    def __init__(
        self,
        database: Database,
        agent: AgentService,
        chat_server: Any,
        config_store: ProviderConfigStore,
        sessions: AdminSessionStore,
        log_capacity: int = 500,
    ) -> None:
        self.database = database
        self.agent = agent
        self.chat_server = chat_server
        self.config_store = config_store
        self.sessions = sessions
        self._config_lock = asyncio.Lock()
        self.logs = LogBufferHandler(log_capacity)
        self.logger = logging.getLogger("chatroom")
        self.logger.addHandler(self.logs)
        self._provider_state = self.config_store.load_state(
            agent.settings, agent.api_key, agent.enabled
        )
        active = self._provider_state.active()
        self.agent.apply_provider(active.settings, active.api_key, self._provider_label(active))
        self.agent.set_enabled(self._provider_state.agent_enabled)

    def close(self) -> None:
        self.logger.removeHandler(self.logs)

    async def login(
        self, username: str, password: str, source_ip: str
    ) -> tuple[str, AdminSession]:
        if not self.sessions.allow_login(source_ip):
            raise AdminAuthError("登录尝试过于频繁，请稍后再试")
        user = await asyncio.to_thread(self.database.authenticate, username, password)
        if not user or user.get("role") != "admin":
            self.sessions.record_failure(source_ip)
            await asyncio.to_thread(
                self.database.record_admin_audit,
                None,
                "session.login",
                "admin-console",
                "authentication failed",
                "denied",
                source_ip,
            )
            raise AdminAuthError("用户名或密码错误")
        token, session = self.sessions.create(user, source_ip)
        await self._audit(session, "session.login", "admin-console", "login", "succeeded")
        return token, session

    async def logout(self, session: AdminSession, token: str | None) -> None:
        self.sessions.revoke(token)
        await self._audit(session, "session.logout", "admin-console", "logout", "succeeded")

    async def require_current_admin(
        self, session: AdminSession, token: str | None
    ) -> None:
        try:
            user = await asyncio.to_thread(self.database.get_user, session.user_id)
        except Exception as exc:
            self.sessions.revoke(token)
            raise AdminAuthError("管理员权限已失效") from exc
        if user.get("role") != "admin":
            self.sessions.revoke(token)
            raise AdminAuthError("管理员权限已失效")

    async def dashboard(self) -> dict[str, Any]:
        counts = await asyncio.to_thread(self.database.system_counts)
        return {
            **self.chat_server.admin_status_snapshot(),
            "total_users": counts["users"],
            "groups": counts["groups"],
            "agent": self.agent.status(),
            "server_time": time.time(),
        }

    def provider_view(self) -> dict[str, Any]:
        value = self.agent.settings.public_dict()
        value["kind"] = value["protocol"]
        value["api_key_set"] = bool(self.agent.api_key)
        return value

    def provider_presets(self) -> dict[str, Any]:
        return {
            "presets": [preset.public_dict() for preset in PROVIDER_PRESETS.values()],
            "protocols": ["openai_chat", "ollama_native", "gemini_native"],
            "auth_types": ["none", "bearer", "x-api-key", "x-goog-api-key"],
        }

    def model_profiles(self) -> dict[str, Any]:
        return {
            "default_profile_id": self._provider_state.default_profile_id,
            "profiles": [
                profile.public_dict(self._provider_state.default_profile_id)
                for profile in self._provider_state.profiles
            ],
        }

    @staticmethod
    def _same_origin(first: str, second: str) -> bool:
        a, b = urlsplit(first), urlsplit(second)
        return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)

    def _profile_from_payload(
        self, payload: dict[str, Any], existing: ModelProfile | None = None
    ) -> ModelProfile:
        preset_id = str(payload.get("preset_id", existing.preset_id if existing else "ollama"))
        preset = PROVIDER_PRESETS.get(preset_id)
        if not preset:
            raise AdminRequestError("未知供应商预设")
        if preset_id == "custom":
            protocol = normalize_protocol(str(payload.get("protocol", existing.settings.protocol if existing else preset.protocol)))
            auth_type = str(payload.get("auth_type", existing.settings.auth_type if existing else preset.auth_type))
        else:
            protocol, auth_type = preset.protocol, preset.auth_type
        current = existing.settings if existing else ProviderSettings(
            protocol=preset.protocol,
            base_url=preset.base_url,
            model=preset.common_models[0] if preset.common_models else "manual-model-id",
            auth_type=preset.auth_type,
        )
        extra = payload.get("extra_parameters", current.extra_parameters)
        if isinstance(extra, str):
            try:
                extra = json.loads(extra) if extra.strip() else {}
            except json.JSONDecodeError as exc:
                raise AdminRequestError("高级请求参数必须是有效的 JSON 对象") from exc
        settings = ProviderSettings(
            protocol=protocol,
            base_url=str(payload.get("base_url", current.base_url if existing else preset.base_url)),
            model=str(payload.get("model", current.model)),
            auth_type=auth_type,
            temperature=float(payload.get("temperature", current.temperature)),
            max_output_tokens=int(payload.get("max_output_tokens", current.max_output_tokens)),
            timeout_seconds=int(payload.get("timeout_seconds", current.timeout_seconds)),
            reasoning_effort=str(payload.get("reasoning_effort", current.reasoning_effort)),
            max_concurrency=int(payload.get("max_concurrency", current.max_concurrency)),
            extra_parameters=extra,
        ).validated()
        supplied_key = payload.get("api_key")
        has_new_key = isinstance(supplied_key, str) and bool(supplied_key)
        clear_key = payload.get("clear_api_key") is True
        if existing and not self._same_origin(existing.settings.base_url, settings.base_url) and existing.api_key and not has_new_key and not clear_key:
            raise AdminRequestError("更换模型 API 主机时必须重新输入 API Key")
        api_key = "" if clear_key else (str(supplied_key) if has_new_key else (existing.api_key if existing else ""))
        return ModelProfile(
            id=existing.id if existing else secrets.token_hex(8),
            name=str(payload.get("name", existing.name if existing else f"{preset.name} 配置")),
            preset_id=preset_id,
            settings=settings,
            api_key=api_key,
            availability=(existing.availability if existing and existing.settings == settings and existing.api_key == api_key else "unknown"),
            last_checked_at=(existing.last_checked_at if existing and existing.settings == settings and existing.api_key == api_key else None),
            last_error_category=(existing.last_error_category if existing and existing.settings == settings and existing.api_key == api_key else None),
        ).validated()

    def _find_profile(self, profile_id: str) -> ModelProfile:
        for profile in self._provider_state.profiles:
            if profile.id == profile_id:
                return profile
        raise AdminRequestError("模型配置不存在")

    @staticmethod
    def _provider_label(profile: ModelProfile) -> str:
        preset = PROVIDER_PRESETS.get(profile.preset_id)
        if not preset or profile.preset_id == "custom":
            return profile.name
        return preset.name

    async def _persist_state(self, state: ProviderConfigState) -> None:
        await asyncio.to_thread(self.config_store.save_state, state)
        self._provider_state = state

    async def _commit_active_profile(
        self, state: ProviderConfigState, profile: ModelProfile
    ) -> None:
        previous_state = self._provider_state
        previous = previous_state.active()
        previous_online = self.agent.online
        await self._persist_state(state)
        try:
            self.agent.apply_provider(
                profile.settings, profile.api_key, self._provider_label(profile)
            )
            self.agent.online = self.agent.enabled
        except Exception as exc:
            try:
                await self._persist_state(previous_state)
                self.agent.apply_provider(
                    previous.settings, previous.api_key, self._provider_label(previous)
                )
                self.agent.online = previous_online
            except Exception:
                self.agent.set_enabled(False)
                self.logger.exception("模型配置切换回滚失败，Agent 已停用")
            raise AdminRequestError("模型切换失败，原配置已恢复") from exc

    async def _post_config_change(
        self,
        session: AdminSession,
        action: str,
        resource: str,
        detail: str,
        status: str,
        publish: bool = False,
    ) -> None:
        if publish:
            try:
                await self.chat_server.publish_agent_status()
            except Exception:
                self.logger.exception("模型状态广播失败")
        try:
            await self._audit(session, action, resource, detail, status)
        except Exception:
            self.logger.exception("模型配置审计写入失败")

    async def save_model_profile(
        self, session: AdminSession, payload: dict[str, Any], profile_id: str | None = None
    ) -> dict[str, Any]:
        async with self._config_lock:
            existing = self._find_profile(profile_id) if profile_id else None
            profile = self._profile_from_payload(payload, existing)
            profiles = list(self._provider_state.profiles)
            if (
                existing
                and existing.id == self._provider_state.default_profile_id
                and (
                    profile.preset_id != existing.preset_id
                    or profile.settings != existing.settings
                    or profile.api_key != existing.api_key
                )
            ):
                # Editing the active profile creates a draft. The active/default
                # snapshot must not change until the draft passes activation tests.
                profile = replace(profile, id=secrets.token_hex(8))
                existing = None
            if existing:
                profiles = [profile if item.id == existing.id else item for item in profiles]
            else:
                profiles.append(profile)
            state = ProviderConfigState(tuple(profiles), self._provider_state.default_profile_id, self._provider_state.agent_enabled)
            await self._persist_state(state)
            await self._post_config_change(session, "model-profile.save", profile.id, f"preset={profile.preset_id}, model={profile.settings.model}", "succeeded")
            return profile.public_dict(state.default_profile_id)

    async def delete_model_profile(self, session: AdminSession, profile_id: str) -> dict[str, Any]:
        async with self._config_lock:
            self._find_profile(profile_id)
            if profile_id == self._provider_state.default_profile_id:
                raise AdminRequestError("当前默认模型配置不能删除，请先切换到其他配置")
            profiles = tuple(item for item in self._provider_state.profiles if item.id != profile_id)
            await self._persist_state(ProviderConfigState(profiles, self._provider_state.default_profile_id, self._provider_state.agent_enabled))
            await self._post_config_change(session, "model-profile.delete", profile_id, "delete", "succeeded")
            return {"ok": True}

    async def discover_models(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(payload.get("profile_id") or "")
        existing = self._find_profile(profile_id) if profile_id else None
        profile = self._profile_from_payload(payload, existing)
        try:
            models = await create_provider(profile.settings, profile.api_key).list_models()
        except AgentUnavailable as exc:
            raise AdminRequestError(self.ERROR_MESSAGES.get(exc.category, self.ERROR_MESSAGES["unavailable"])) from exc
        return {"models": models}

    async def test_model_profile(self, session: AdminSession, profile_id: str) -> dict[str, Any]:
        async with self._config_lock:
            profile = self._find_profile(profile_id)
            online, category = await self.agent.probe_provider(profile.settings, profile.api_key)
            checked = replace(
                profile,
                availability="available" if online else "unavailable",
                last_checked_at=time.time(),
                last_error_category=category,
            )
            profiles = tuple(checked if item.id == profile_id else item for item in self._provider_state.profiles)
            await self._persist_state(ProviderConfigState(profiles, self._provider_state.default_profile_id, self._provider_state.agent_enabled))
            await self._post_config_change(session, "model-profile.test", profile_id, f"model={profile.settings.model}", "succeeded" if online else "failed")
            return {
                "available": online,
                "error_category": category,
                "message": "连接测试成功" if online else self.ERROR_MESSAGES.get(category or "unavailable", self.ERROR_MESSAGES["unavailable"]),
                "profile": checked.public_dict(self._provider_state.default_profile_id),
            }

    async def activate_model_profile(self, session: AdminSession, profile_id: str) -> dict[str, Any]:
        async with self._config_lock:
            profile = self._find_profile(profile_id)
            online, category = await self.agent.probe_provider(profile.settings, profile.api_key)
            checked = replace(profile, availability="available" if online else "unavailable", last_checked_at=time.time(), last_error_category=category)
            profiles = tuple(checked if item.id == profile_id else item for item in self._provider_state.profiles)
            if not online:
                await self._persist_state(ProviderConfigState(profiles, self._provider_state.default_profile_id, self._provider_state.agent_enabled))
                await self._post_config_change(session, "model-profile.activate", profile_id, f"model={profile.settings.model}", "failed")
                raise AdminRequestError(self.ERROR_MESSAGES.get(category or "unavailable", self.ERROR_MESSAGES["unavailable"]))
            state = ProviderConfigState(profiles, profile_id, self._provider_state.agent_enabled)
            await self._commit_active_profile(state, checked)
            await self._post_config_change(session, "model-profile.activate", profile_id, f"model={profile.settings.model}", "succeeded", publish=True)
            return checked.public_dict(profile_id)

    async def test_and_apply_provider(
        self, session: AdminSession, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._config_lock:
            return await self._test_and_apply_provider(session, payload)

    async def _test_and_apply_provider(
        self, session: AdminSession, payload: dict[str, Any]
    ) -> dict[str, Any]:
        active = self._provider_state.active()
        legacy = dict(payload)
        legacy["protocol"] = normalize_protocol(str(payload.get("kind", active.settings.protocol)))
        legacy["preset_id"] = "ollama" if legacy["protocol"] == "ollama_native" else "custom"
        candidate = self._profile_from_payload(legacy, active)
        online = await self.agent.test_provider(candidate.settings, candidate.api_key)
        category = None if online else "unavailable"
        if not online:
            await self._post_config_change(session, "model-provider.apply", candidate.settings.protocol, f"model={candidate.settings.model}", "failed")
            raise AdminRequestError(self.ERROR_MESSAGES.get(category or "unavailable", "无法连接候选模型服务，现有配置未改变"))
        candidate = replace(candidate, availability="available", last_checked_at=time.time(), last_error_category=None)
        profiles = tuple(candidate if item.id == active.id else item for item in self._provider_state.profiles)
        await self._commit_active_profile(ProviderConfigState(profiles, active.id, self._provider_state.agent_enabled), candidate)
        await self._post_config_change(session, "model-provider.apply", candidate.settings.protocol, f"model={candidate.settings.model}", "succeeded", publish=True)
        return self.provider_view()

    async def set_agent_enabled(
        self, session: AdminSession, enabled: bool
    ) -> dict[str, Any]:
        async with self._config_lock:
            return await self._set_agent_enabled(session, enabled)

    async def _set_agent_enabled(
        self, session: AdminSession, enabled: bool
    ) -> dict[str, Any]:
        state = ProviderConfigState(self._provider_state.profiles, self._provider_state.default_profile_id, bool(enabled))
        await self._persist_state(state)
        self.agent.set_enabled(enabled)
        if enabled:
            await self.agent.refresh_health()
        await self.chat_server.publish_agent_status()
        await self._audit(
            session,
            "agent.enabled",
            "agent",
            f"enabled={bool(enabled)}",
            "succeeded",
        )
        return self.agent.status()

    async def agent_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.database.list_agent_activity_admin, limit)
        return [self._safe_run(row) for row in rows]

    async def tool_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.database.recent_tool_calls, limit)
        return [self._safe_tool(row) for row in rows]

    async def users(self) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.database.list_users_admin)
        online = self.chat_server.online_user_ids()
        for row in rows:
            row["online"] = int(row["id"]) in online
        return rows

    async def groups(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.database.list_groups_admin)

    async def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.database.list_admin_audit, limit)

    async def _audit(
        self,
        session: AdminSession,
        action: str,
        resource: str,
        detail: str,
        status: str,
    ) -> None:
        await asyncio.to_thread(
            self.database.record_admin_audit,
            session.user_id,
            action,
            resource,
            LogBufferHandler.sanitize(detail),
            status,
            session.source_ip,
        )

    @staticmethod
    def _safe_run(row: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "id",
            "task_type",
            "conversation_type",
            "conversation_id",
            "requested_by",
            "requested_by_name",
            "status",
            "started_at",
            "finished_at",
            "result_message_id",
            "error",
        }
        result = {key: row.get(key) for key in allowed}
        if result.get("error"):
            result["error"] = LogBufferHandler.sanitize(result["error"])
        return result

    @staticmethod
    def _safe_tool(row: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "id",
            "run_id",
            "tool_name",
            "conversation_type",
            "conversation_id",
            "requested_by",
            "requested_by_name",
            "input_summary",
            "result_count",
            "status",
            "started_at",
            "finished_at",
            "error",
        }
        result = {key: row.get(key) for key in allowed}
        for key in ("input_summary", "error"):
            if result.get(key):
                result[key] = LogBufferHandler.sanitize(result[key])
        return result

