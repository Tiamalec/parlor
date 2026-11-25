"""Model/provider selection and health state."""

from __future__ import annotations

from .runtime import AgentRuntime
from ..model_provider import AgentUnavailable, ModelProvider, ProviderSettings, create_provider, probe_settings
from typing import Any
from ..agent_config import DEFAULT_AGENT_ID
from ..provider_presets import PROVIDER_PRESETS, preset_for_legacy
from ..search_provider import SearchSettings
import asyncio


class AgentProvidersMixin:
    """Model/provider selection and health state."""

    @property
    def model(self) -> str:
        return self.settings.model

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "online": self.enabled and self.online,
            "model": self.settings.model,
            "provider": self.provider_label,
            "protocol": self.settings.protocol,
            "active_runs": self.active_runs,
            "default_agent_id": DEFAULT_AGENT_ID,
        }

    def set_model_catalog(
        self,
        profiles: list[Any] | tuple[Any, ...],
        default_profile_id: str | None = None,
    ) -> None:
        """Expose the admin-configured model profiles to Agent selection.

        Only identifiers, labels and validated provider settings are retained;
        API keys stay inside the provider instances and are never returned by
        the client protocol.
        """
        catalog: dict[str, dict[str, Any]] = {}
        for profile in profiles:
            if isinstance(profile, dict):
                profile_id = str(profile.get("id") or "")
                settings = profile.get("settings")
                api_key = str(profile.get("api_key") or "")
                name = str(profile.get("name") or profile_id)
                preset_id = str(profile.get("preset_id") or "")
            else:
                profile_id = str(getattr(profile, "id", ""))
                settings = getattr(profile, "settings", None)
                api_key = str(getattr(profile, "api_key", "") or "")
                name = str(getattr(profile, "name", "") or profile_id)
                preset_id = str(getattr(profile, "preset_id", "") or "")
            if not profile_id or settings is None:
                continue
            checked = settings.validated()
            catalog[profile_id] = {
                "id": profile_id,
                "name": name,
                "preset_id": preset_id,
                "settings": checked,
                "api_key": api_key,
                "label": PROVIDER_PRESETS.get(preset_id, PROVIDER_PRESETS["custom"]).name
                if preset_id and preset_id != "custom"
                else name,
                "model": checked.model,
            }
        if catalog:
            self._model_catalog = catalog
            self._default_model_profile_id = str(default_profile_id or next(iter(catalog)))
            self._provider_cache.clear()

    def model_options(self) -> list[dict[str, Any]]:
        if not self._model_catalog:
            return [
                {
                    "id": self._default_model_profile_id,
                    "name": self.provider_label,
                    "model": self.settings.model,
                    "protocol": self.settings.protocol,
                    "is_default": True,
                }
            ]
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "model": item["model"],
                "protocol": item["settings"].protocol,
                "is_default": item["id"] == self._default_model_profile_id,
            }
            for item in self._model_catalog.values()
        ]

    def _provider_for_agent(self, agent: dict[str, Any]) -> tuple[ModelProvider, str, str, int]:
        profile_id = str(agent.get("model_profile_id") or "")
        selected = self._model_catalog.get(profile_id) if profile_id else None
        if not selected:
            return self.provider, self.provider_label, self.settings.model, self.settings.timeout_seconds
        provider = self._provider_cache.get(profile_id)
        if provider is None:
            provider = create_provider(selected["settings"], selected["api_key"])
            self._provider_cache[profile_id] = provider
        return (
            provider,
            str(selected["label"]),
            str(selected["model"]),
            int(selected["settings"].timeout_seconds),
        )

    def _runtime_for(self, agent: dict[str, Any]) -> AgentRuntime:
        provider, label, model, timeout = self._provider_for_agent(agent)
        identity = self._identity_answer(agent, label=label, model=model)
        return AgentRuntime(
            provider=provider,
            fallback_provider=self.fallback_provider,
            semaphore=self.semaphore,
            timeout_seconds=timeout,
            generation=self._generation,
            identity=identity,
            agent=agent,
            model_label=label,
            model_name=model,
        )

    def set_enabled(self, enabled: bool) -> None:
        if self.enabled != bool(enabled):
            self._generation += 1
        self.enabled = bool(enabled)
        if not self.enabled:
            self.online = False

    def apply_search_settings(self, settings: SearchSettings) -> None:
        self.search_settings = settings.validated()

    async def test_provider(self, settings: ProviderSettings, api_key: str) -> bool:
        online, _category = await self.probe_provider(settings, api_key)
        return online

    async def probe_provider(self, settings: ProviderSettings, api_key: str) -> tuple[bool, str | None]:
        checked = probe_settings(settings)
        provider = create_provider(checked, api_key)
        try:
            await provider.list_models()
            result = await asyncio.wait_for(
                provider.generate("你是连接测试助手。", "只回复 OK。"),
                timeout=checked.timeout_seconds + 2,
            )
            return bool(result.strip()), None if result.strip() else "invalid_response"
        except AgentUnavailable as exc:
            return False, exc.category
        except asyncio.TimeoutError:
            return False, "network"

    @staticmethod
    def _default_provider_label(settings: ProviderSettings) -> str:
        preset_id = preset_for_legacy(settings.protocol, settings.base_url)
        return PROVIDER_PRESETS.get(preset_id, PROVIDER_PRESETS["custom"]).name

    @staticmethod
    def _protocol_label(protocol: str) -> str:
        return {
            "openai_chat": "OpenAI Chat",
            "ollama_native": "Ollama Native",
            "gemini_native": "Gemini Native",
        }.get(protocol, protocol)

    def _identity_answer(
        self,
        agent: dict[str, Any] | None = None,
        *,
        label: str | None = None,
        model: str | None = None,
    ) -> str:
        name = str((agent or {}).get("name") or "AI Agent")
        profile_id = str((agent or {}).get("model_profile_id") or "")
        selected = self._model_catalog.get(profile_id) if profile_id else None
        protocol = selected["settings"].protocol if selected else self.settings.protocol
        provider_label = label or (str(selected["label"]) if selected else self.provider_label)
        model_name = model or (str(selected["model"]) if selected else self.settings.model)
        prefix = "我是聊天室 Agent 助手" if agent is None or agent.get("id") == DEFAULT_AGENT_ID else f"我是聊天室 Agent「{name}」"
        answer = (
            f"{prefix}，当前配置是 {provider_label} 的 "
            f"{model_name} 模型，使用 {self._protocol_label(protocol)} 协议。"
        )
        if self.fallback_provider:
            answer += f"默认模型不可用时，可能使用回退模型 {self.fallback_provider.settings.model}。"
        return answer

    def apply_provider(
        self, settings: ProviderSettings, api_key: str, provider_label: str | None = None
    ) -> None:
        checked = settings.validated()
        self.settings = checked
        self.api_key = api_key
        self.provider = create_provider(checked, api_key)
        self.provider_label = (provider_label or self._default_provider_label(checked)).strip()[:80]
        self.semaphore = asyncio.Semaphore(checked.max_concurrency)
        self._provider_cache.clear()
        self._generation += 1
        self.online = False

    async def refresh_health(self) -> bool:
        if not self.enabled:
            self.online = False
            return False
        generation = self._generation
        online = await self.provider.health()
        if not online and self.fallback_provider:
            online = await self.fallback_provider.health()
        if generation == self._generation:
            self.online = online
        return self.online

    def _update_online_for_runtime(self, online: bool) -> None:
        runtime = self._runtime_context.get()
        generation = runtime.generation if runtime else self._generation
        if generation == self._generation:
            self.online = online
