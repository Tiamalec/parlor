from __future__ import annotations

import json
import logging
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from server.model_provider import ProviderSettings, normalize_protocol, validate_api_key
from server.search_provider import SearchSettings
from server.provider_presets import preset_for_legacy


LOGGER = logging.getLogger("chatroom.admin.config")


@dataclass(frozen=True)
class ModelProfile:
    id: str
    name: str
    preset_id: str
    settings: ProviderSettings
    api_key: str = ""
    availability: str = "unknown"
    last_checked_at: float | None = None
    last_error_category: str | None = None

    def validated(self) -> "ModelProfile":
        profile_id = self.id.strip()
        name = self.name.strip()
        if not profile_id or len(profile_id) > 64:
            raise ValueError("模型配置 ID 无效")
        if not name or len(name) > 80:
            raise ValueError("配置名称不能为空且不能超过 80 个字符")
        availability = self.availability if self.availability in {"unknown", "available", "unavailable"} else "unknown"
        return replace(
            self,
            id=profile_id,
            name=name,
            preset_id=self.preset_id.strip() or "custom",
            settings=self.settings.validated(),
            api_key=validate_api_key(self.api_key),
            availability=availability,
            last_error_category=(str(self.last_error_category)[:40] if self.last_error_category else None),
        )

    def public_dict(self, default_profile_id: str) -> dict[str, Any]:
        checked = self.validated()
        return {
            "id": checked.id,
            "name": checked.name,
            "preset_id": checked.preset_id,
            **checked.settings.public_dict(),
            "api_key_set": bool(checked.api_key),
            "availability": checked.availability,
            "last_checked_at": checked.last_checked_at,
            "last_error_category": checked.last_error_category,
            "is_default": checked.id == default_profile_id,
        }

    def storage_dict(self) -> dict[str, Any]:
        checked = self.validated()
        return {
            "id": checked.id,
            "name": checked.name,
            "preset_id": checked.preset_id,
            **checked.settings.public_dict(),
            "api_key": checked.api_key,
            "availability": checked.availability,
            "last_checked_at": checked.last_checked_at,
            "last_error_category": checked.last_error_category,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ModelProfile":
        protocol = normalize_protocol(str(value.get("protocol") or value.get("kind") or "openai_chat"))
        default_auth = "none" if protocol == "ollama_native" else ("x-goog-api-key" if protocol == "gemini_native" else "bearer")
        extra = value.get("extra_parameters", {})
        if not isinstance(extra, dict):
            raise ValueError("高级请求参数必须是对象")
        settings = ProviderSettings(
            protocol=protocol,
            base_url=str(value.get("base_url", "")),
            model=str(value.get("model", "")),
            auth_type=str(value.get("auth_type", default_auth)),
            temperature=float(value.get("temperature", 0.2)),
            max_output_tokens=int(value.get("max_output_tokens", 512)),
            timeout_seconds=int(value.get("timeout_seconds", 45)),
            reasoning_effort=str(value.get("reasoning_effort", "none")),
            max_concurrency=int(value.get("max_concurrency", 1)),
            extra_parameters=extra,
        )
        return cls(
            id=str(value.get("id") or value.get("config_id") or secrets.token_hex(8)),
            name=str(value.get("name") or "默认模型"),
            preset_id=str(value.get("preset_id") or preset_for_legacy(protocol, settings.base_url)),
            settings=settings,
            api_key=str(value.get("api_key") or ""),
            availability=str(value.get("availability") or "unknown"),
            last_checked_at=(float(value["last_checked_at"]) if value.get("last_checked_at") is not None else None),
            last_error_category=(str(value["last_error_category"]) if value.get("last_error_category") else None),
        ).validated()


@dataclass(frozen=True)
class ProviderConfigState:
    profiles: tuple[ModelProfile, ...]
    default_profile_id: str
    agent_enabled: bool

    def active(self) -> ModelProfile:
        for profile in self.profiles:
            if profile.id == self.default_profile_id:
                return profile
        raise ValueError("默认模型配置不存在")


class ProviderConfigStore:
    def __init__(self, data_dir: Path) -> None:
        self.directory = Path(data_dir) / "admin"
        self.settings_path = self.directory / "model-provider.json"
        self.backup_path = self.directory / "model-provider.backup.json"
        self.secrets_path = self.directory / "model-provider.secrets"

    def load_state(self, default_settings: ProviderSettings, default_api_key: str, default_enabled: bool) -> ProviderConfigState:
        try:
            value = self._secure_read_json(self.settings_path)
        except FileNotFoundError:
            return self._fallback_state(default_settings, default_api_key, default_enabled)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            LOGGER.error("模型配置快照不可用，将尝试上一份有效快照：%s", type(exc).__name__)
            try:
                backup = self._secure_read_json(self.backup_path)
                return self._state_from_v2(backup, default_enabled)
            except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
                LOGGER.error("模型配置及备份均不可用，Agent 已安全停用")
                return self._fallback_state(default_settings, default_api_key, False)
        try:
            if int(value.get("schema_version", 1)) >= 2:
                return self._state_from_v2(value, default_enabled)
            return self._migrate_v1(value, default_settings, default_api_key, default_enabled)
        except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError) as exc:
            LOGGER.error("模型配置解析失败，将尝试上一份有效快照：%s", type(exc).__name__)
            try:
                backup = self._secure_read_json(self.backup_path)
                return self._state_from_v2(backup, default_enabled)
            except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
                LOGGER.error("模型配置及备份均不可用，Agent 已安全停用")
                return self._fallback_state(default_settings, default_api_key, False)

    def _state_from_v2(self, value: dict[str, Any], default_enabled: bool) -> ProviderConfigState:
        if not isinstance(value, dict) or int(value.get("schema_version", 0)) < 2:
            raise ValueError("备份不是 v2 模型配置")
        rows = value.get("profiles")
        if not isinstance(rows, list) or not rows:
            raise ValueError("模型配置列表为空")
        default_id = str(value.get("default_profile_id") or "")
        profiles: list[ModelProfile] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                profiles.append(ModelProfile.from_dict(row))
            except (ValueError, TypeError, OverflowError):
                LOGGER.warning("已隔离损坏的模型配置项")
        ids = [profile.id for profile in profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("模型配置 ID 重复")
        state = ProviderConfigState(tuple(profiles), default_id, bool(value.get("agent_enabled", default_enabled)))
        state.active()
        return state

    def _migrate_v1(self, value: dict[str, Any], default_settings: ProviderSettings, default_api_key: str, default_enabled: bool) -> ProviderConfigState:
        merged = {**default_settings.public_dict(), **value}
        legacy_protocol = normalize_protocol(str(value.get("protocol") or value.get("kind") or default_settings.protocol))
        merged["protocol"] = legacy_protocol
        if "auth_type" not in value:
            merged["auth_type"] = "none" if legacy_protocol == "ollama_native" else ("x-goog-api-key" if legacy_protocol == "gemini_native" else "bearer")
        api_key = value.get("api_key") if isinstance(value.get("api_key"), str) else default_api_key
        config_id = str(value.get("config_id") or secrets.token_hex(8))
        if not api_key and value.get("config_id"):
            try:
                secret = self._secure_read_json(self.secrets_path)
                if isinstance(secret, dict) and secret.get("config_id") == value.get("config_id"):
                    api_key = str(secret.get("api_key") or "")
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        merged.update({"id": config_id, "name": "默认模型", "api_key": api_key})
        profile = ModelProfile.from_dict(merged)
        state = ProviderConfigState((profile,), profile.id, bool(value.get("agent_enabled", default_enabled)))
        self.save_state(state)
        self._remove_legacy_secret()
        return state

    @staticmethod
    def _fallback_state(default_settings: ProviderSettings, default_api_key: str, default_enabled: bool) -> ProviderConfigState:
        checked = default_settings.validated()
        profile = ModelProfile(
            id="bootstrap",
            name="默认模型",
            preset_id=preset_for_legacy(checked.protocol, checked.base_url),
            settings=checked,
            api_key=default_api_key,
        ).validated()
        return ProviderConfigState((profile,), profile.id, default_enabled)

    def save_state(self, state: ProviderConfigState) -> None:
        if not state.profiles:
            raise ValueError("至少需要一个模型配置")
        ids = [profile.id for profile in state.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("模型配置 ID 不能重复")
        state.active()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._chmod(self.directory, 0o700)
        value: dict[str, object] = {
            "schema_version": 2,
            "default_profile_id": state.default_profile_id,
            "agent_enabled": bool(state.agent_enabled),
            "profiles": [profile.storage_dict() for profile in state.profiles],
        }
        self._atomic_json(self.settings_path, value, 0o600)

    def _secure_read_json(self, path: Path) -> dict[str, Any]:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("模型配置必须是普通文件")
        if os.name == "posix":
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise PermissionError("模型配置文件权限不安全")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("配置根节点必须是对象")
        return value

    def _remove_legacy_secret(self) -> None:
        try:
            metadata = self.secrets_path.lstat()
            if stat.S_ISREG(metadata.st_mode) and (os.name != "posix" or metadata.st_uid == os.getuid()):
                self.secrets_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            LOGGER.warning("旧版模型密钥文件清理失败")

    def load(self, default_settings: ProviderSettings, default_api_key: str, default_enabled: bool) -> tuple[ProviderSettings, str, bool]:
        state = self.load_state(default_settings, default_api_key, default_enabled)
        active = state.active()
        return active.settings, active.api_key, state.agent_enabled

    def save(self, settings: ProviderSettings, api_key: str, enabled: bool) -> None:
        state = self.load_state(settings, api_key, enabled)
        active = state.active()
        replacement = replace(active, settings=settings.validated(), api_key=api_key)
        profiles = tuple(replacement if item.id == active.id else item for item in state.profiles)
        self.save_state(ProviderConfigState(profiles, active.id, bool(enabled)))

    def _atomic_json(self, path: Path, value: dict[str, object], mode: int) -> None:
        if path == self.settings_path:
            try:
                previous = self._secure_read_json(path)
                if int(previous.get("schema_version", 0)) >= 2:
                    self._write_json_file(self.backup_path, previous, mode)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        self._write_json_file(path, value, mode)

    def _write_json_file(self, path: Path, value: dict[str, object] | dict[str, Any], mode: int) -> None:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory, prefix=f".{path.name}.", delete=False)
        temporary = Path(handle.name)
        try:
            with handle:
                json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            self._chmod(temporary, mode)
            os.replace(temporary, path)
            self._chmod(path, mode)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _chmod(path: Path, mode: int) -> None:
        if os.name == "posix":
            path.chmod(mode)


class SearchConfigStore(ProviderConfigStore):
    """One active search adapter, stored with the model config's file safeguards."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir)
        self.settings_path = self.directory / "search-provider.json"
        self.backup_path = self.directory / "search-provider.backup.json"

    def load_search_settings(self) -> SearchSettings:
        for path in (self.settings_path, self.backup_path):
            try:
                value = self._secure_read_json(path)
                if value.get("schema_version") != 2:
                    raise ValueError("搜索配置版本无效")
                return SearchSettings(
                    provider=str(value.get("provider") or ""),
                    base_url=str(value.get("base_url") or ""),
                    api_key=str(value.get("api_key") or ""),
                    enabled=value.get("enabled") is True,
                ).validated()
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                LOGGER.error("搜索配置不可用：%s", path.name)
        return SearchSettings()

    def save_search_settings(self, settings: SearchSettings) -> None:
        checked = settings.validated()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._chmod(self.directory, 0o700)
        self._atomic_json(self.settings_path, {
            "schema_version": 2,
            "provider": checked.provider,
            "base_url": checked.base_url,
            "api_key": checked.api_key,
            "enabled": checked.enabled,
        }, 0o600)
