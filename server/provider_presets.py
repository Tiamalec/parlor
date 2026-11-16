from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    protocol: str
    base_url: str
    auth_type: str
    common_models: tuple[str, ...]
    base_url_editable: bool = True

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["common_models"] = list(self.common_models)
        return value


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "ollama": ProviderPreset("ollama", "Ollama", "ollama_native", "http://127.0.0.1:11434", "none", ("chatroom-qwen3.5-4b", "qwen3.5:4b", "llama3.2", "gemma3")),
    "openai": ProviderPreset("openai", "OpenAI", "openai_chat", "https://api.openai.com/v1", "bearer", ("gpt-5.4-mini", "gpt-5.4", "gpt-5.3-mini")),
    "deepseek": ProviderPreset("deepseek", "DeepSeek", "openai_chat", "https://api.deepseek.com", "bearer", ("deepseek-flash", "deepseek-v4-pro")),
    "qwen": ProviderPreset("qwen", "通义千问", "openai_chat", "https://dashscope.aliyuncs.com/compatible-mode/v1", "bearer", ("qwen-plus", "qwen-flash", "qwen-max")),
    "gemini": ProviderPreset("gemini", "Gemini", "gemini_native", "https://generativelanguage.googleapis.com/v1beta", "x-goog-api-key", ("gemini-3.5-flash", "gemini-3.5-pro")),
    "custom": ProviderPreset("custom", "自定义供应商", "openai_chat", "https://models.example/v1", "bearer", ()),
}


def preset_for_legacy(protocol: str, base_url: str) -> str:
    value = protocol.strip().lower()
    if value in {"ollama", "ollama_native"}:
        return "ollama"
    host = base_url.lower()
    if "api.openai.com" in host:
        return "openai"
    if "api.deepseek.com" in host:
        return "deepseek"
    if "dashscope" in host or ".maas.aliyuncs.com" in host:
        return "qwen"
    if "generativelanguage.googleapis.com" in host:
        return "gemini"
    return "custom"
