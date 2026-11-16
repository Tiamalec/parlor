from __future__ import annotations

import asyncio
import ipaddress
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SUPPORTED_PROTOCOLS = {"openai_chat", "ollama_native", "gemini_native"}
SUPPORTED_AUTH_TYPES = {"none", "bearer", "x-api-key", "x-goog-api-key"}
RESERVED_EXTRA_KEYS = {"model", "messages", "stream", "contents", "system_instruction", "generationConfig", "options", "authorization", "api_key"}


class AgentUnavailable(RuntimeError):
    def __init__(self, message: str, category: str = "unavailable") -> None:
        super().__init__(message)
        self.category = category


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


URL_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def normalize_protocol(value: str) -> str:
    aliases = {"ollama": "ollama_native", "openai_compatible": "openai_chat"}
    return aliases.get(value.strip().lower(), value.strip().lower())


def validate_api_key(value: str) -> str:
    key = str(value)
    if len(key) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise ValueError("API Key 格式无效")
    return key


@dataclass(frozen=True)
class ProviderSettings:
    protocol: str
    base_url: str
    model: str
    auth_type: str = "bearer"
    temperature: float = 0.2
    max_output_tokens: int = 512
    timeout_seconds: int = 45
    reasoning_effort: str = "none"
    max_concurrency: int = 1
    extra_parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.protocol

    def validated(self) -> "ProviderSettings":
        protocol = normalize_protocol(self.protocol)
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError("不支持的模型 API 协议")
        auth_type = self.auth_type.strip().lower()
        if auth_type not in SUPPORTED_AUTH_TYPES:
            raise ValueError("不支持的模型认证方式")
        base_url = self.base_url.strip().rstrip("/")
        if protocol == "ollama_native" and base_url.endswith("/v1"):
            base_url = base_url[:-3]
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("模型 API 地址必须是有效的 http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("模型 API 地址不能包含凭据、查询参数或片段")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("非本机模型 API 必须使用 HTTPS")
        try:
            literal_address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal_address = None
        if literal_address and (
            literal_address.is_link_local
            or literal_address.is_multicast
            or literal_address.is_unspecified
            or literal_address.is_reserved
        ):
            raise ValueError("模型 API 地址不能使用链路本地或保留地址")
        model = self.model.strip()
        if not model or len(model) > 200:
            raise ValueError("模型名称不能为空且不能超过 200 个字符")
        temperature = float(self.temperature)
        if not 0 <= temperature <= 2:
            raise ValueError("temperature 必须在 0 到 2 之间")
        if not isinstance(self.extra_parameters, dict):
            raise ValueError("高级请求参数必须是 JSON 对象")
        try:
            encoded = json.dumps(self.extra_parameters, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("高级请求参数包含无效值") from exc
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("高级请求参数不能超过 8KB")
        reserved = RESERVED_EXTRA_KEYS.intersection(str(key) for key in self.extra_parameters)
        if reserved:
            raise ValueError(f"高级请求参数不能覆盖保留字段：{', '.join(sorted(reserved))}")
        return ProviderSettings(
            protocol=protocol,
            base_url=base_url,
            model=model,
            auth_type=auth_type,
            temperature=temperature,
            max_output_tokens=max(1, min(int(self.max_output_tokens), 32_768)),
            timeout_seconds=max(1, min(int(self.timeout_seconds), 300)),
            reasoning_effort=str(self.reasoning_effort or "none")[:40],
            max_concurrency=max(1, min(int(self.max_concurrency), 16)),
            extra_parameters=dict(self.extra_parameters),
        )

    def public_dict(self) -> dict[str, Any]:
        return asdict(self.validated())


class ModelProvider(Protocol):
    settings: ProviderSettings
    async def health(self) -> bool: ...
    async def list_models(self) -> list[str]: ...
    async def generate(self, system_prompt: str, user_prompt: str) -> str: ...


@dataclass
class _JsonHttpProvider:
    settings: ProviderSettings
    api_key: str

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key and self.settings.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.api_key and self.settings.auth_type == "x-api-key":
            headers["X-API-Key"] = self.api_key
        elif self.api_key and self.settings.auth_type == "x-goog-api-key":
            headers["X-Goog-Api-Key"] = self.api_key
        return headers

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            request = urllib.request.Request(f"{self.settings.base_url}{path}", data=body, method=method, headers=self._headers())
            with URL_OPENER.open(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise AgentUnavailable("模型响应过大", "invalid_response")
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise AgentUnavailable("模型返回格式无效", "invalid_response")
                return value
        except AgentUnavailable:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                category = "authentication"
            elif exc.code == 404:
                category = "not_found"
            elif exc.code == 429:
                category = "rate_limited"
            else:
                category = "http_error"
            raise AgentUnavailable(f"模型服务返回 HTTP {exc.code}", category) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AgentUnavailable("模型服务地址不可达", "network") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AgentUnavailable("模型返回格式无效", "invalid_response") from exc
        except ValueError as exc:
            raise AgentUnavailable("模型请求配置无效", "invalid_request") from exc

    async def health(self) -> bool:
        try:
            await self.list_models()
            return True
        except AgentUnavailable:
            return False


@dataclass
class OpenAIChatAdapter(_JsonHttpProvider):
    async def list_models(self) -> list[str]:
        value = await asyncio.to_thread(self._request_json, "GET", "/models")
        rows = value.get("data")
        if not isinstance(rows, list):
            raise AgentUnavailable("模型列表格式无效", "invalid_response")
        return sorted({str(row.get("id", "")).strip() for row in rows if isinstance(row, dict) and row.get("id")})

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload: dict[str, Any] = dict(self.settings.extra_parameters)
        payload.update({"model": self.settings.model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], "temperature": self.settings.temperature, "max_tokens": self.settings.max_output_tokens, "stream": False})
        if self.settings.reasoning_effort and self.settings.reasoning_effort != "none":
            payload["reasoning_effort"] = self.settings.reasoning_effort
        result = await asyncio.to_thread(self._request_json, "POST", "/chat/completions", payload)
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentUnavailable("模型返回格式无效", "invalid_response") from exc
        if not isinstance(content, str) or not content.strip():
            raise AgentUnavailable("模型返回了空结果", "invalid_response")
        return content.strip()


@dataclass
class OllamaAdapter(_JsonHttpProvider):
    async def list_models(self) -> list[str]:
        value = await asyncio.to_thread(self._request_json, "GET", "/api/tags")
        rows = value.get("models")
        if not isinstance(rows, list):
            raise AgentUnavailable("模型列表格式无效", "invalid_response")
        return sorted({str(row.get("name") or row.get("model") or "").strip() for row in rows if isinstance(row, dict)} - {""})

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        options = dict(self.settings.extra_parameters)
        options.update({"temperature": self.settings.temperature, "num_predict": self.settings.max_output_tokens})
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": options,
            "stream": False,
            "think": self.settings.reasoning_effort != "none",
        }
        result = await asyncio.to_thread(self._request_json, "POST", "/api/chat", payload)
        try:
            content = result["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise AgentUnavailable("模型返回格式无效", "invalid_response") from exc
        if not isinstance(content, str) or not content.strip():
            raise AgentUnavailable("模型返回了空结果", "invalid_response")
        return content.strip()


@dataclass
class GeminiAdapter(_JsonHttpProvider):
    async def list_models(self) -> list[str]:
        value = await asyncio.to_thread(self._request_json, "GET", "/models")
        rows = value.get("models")
        if not isinstance(rows, list):
            raise AgentUnavailable("模型列表格式无效", "invalid_response")
        models: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            methods = row.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            name = str(row.get("name", "")).removeprefix("models/").strip()
            if name:
                models.add(name)
        return sorted(models)

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        generation_config = dict(self.settings.extra_parameters)
        generation_config.update({"temperature": self.settings.temperature, "maxOutputTokens": self.settings.max_output_tokens})
        payload = {"system_instruction": {"parts": [{"text": system_prompt}]}, "contents": [{"role": "user", "parts": [{"text": user_prompt}]}], "generationConfig": generation_config}
        model = self.settings.model.removeprefix("models/")
        result = await asyncio.to_thread(self._request_json, "POST", f"/models/{quote(model, safe='-._')}:generateContent", payload)
        try:
            parts = result["candidates"][0]["content"]["parts"]
            content = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentUnavailable("模型返回格式无效", "invalid_response") from exc
        if not content.strip():
            raise AgentUnavailable("模型返回了空结果", "invalid_response")
        return content.strip()


OpenAICompatibleProvider = OpenAIChatAdapter


def create_provider(settings: ProviderSettings, api_key: str) -> ModelProvider:
    checked = settings.validated()
    adapters: dict[str, type[_JsonHttpProvider]] = {"openai_chat": OpenAIChatAdapter, "ollama_native": OllamaAdapter, "gemini_native": GeminiAdapter}
    return adapters[checked.protocol](checked, validate_api_key(api_key))


def probe_settings(settings: ProviderSettings) -> ProviderSettings:
    checked = settings.validated()
    return replace(
        checked,
        max_output_tokens=64,
        timeout_seconds=min(checked.timeout_seconds, 15),
        reasoning_effort="none",
    )
