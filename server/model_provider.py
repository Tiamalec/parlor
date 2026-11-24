from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RETRY_OUTPUT_TOKENS = 8192
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


def text_from_content(value: Any) -> str:
    """Flatten OpenAI-style content blocks without losing usable text."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                cleaned = item.strip()
            elif isinstance(item, dict):
                cleaned = next(
                    (
                        str(item[key]).strip()
                        for key in ("text", "content", "value")
                        if isinstance(item.get(key), str) and str(item[key]).strip()
                    ),
                    "",
                )
            else:
                cleaned = ""
            if cleaned:
                parts.append(cleaned)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if isinstance(value.get(key), str) and str(value[key]).strip():
                return str(value[key]).strip()
    return ""


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
    async def generate_tool_turn(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> "ProviderTurn": ...


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderTurn:
    content: str
    tool_calls: list[ProviderToolCall] = field(default_factory=list)


def _invalid_response(message: str = "模型返回格式无效") -> AgentUnavailable:
    return AgentUnavailable(message, "invalid_response")


def _validate_json_value(value: Any) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON 参数包含非有限数值")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON 参数对象的键必须是字符串")
            _validate_json_value(item)
        return
    raise ValueError("JSON 参数包含不支持的值")


def _reject_json_constant(constant: str) -> None:
    raise ValueError(f"无效 JSON 常量：{constant}")


def _decode_tool_arguments(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if isinstance(value, str):
        try:
            value = json.loads(
                value,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("工具参数不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    _validate_json_value(value)
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _stable_tool_call_id(provider: str, position: int, name: str, arguments: dict[str, Any]) -> str:
    identity = json.dumps(
        [provider, position, name, arguments],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"call_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _validated_tool_name(value: Any, *, response: bool) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        if response:
            raise _invalid_response()
        raise ValueError("工具名称不能为空且不能超过 200 个字符")
    return value.strip()


def _validated_call_id(
    value: Any,
    *,
    provider: str,
    position: int,
    name: str,
    arguments: dict[str, Any],
    response: bool,
) -> str:
    if value is None or value == "":
        return _stable_tool_call_id(provider, position, name, arguments)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
        if response:
            raise _invalid_response()
        raise ValueError("工具调用 ID 格式无效")
    return value.strip()


def _parse_response_tool_call(
    *,
    call_id: Any,
    name: Any,
    arguments: Any,
    provider: str,
    position: int,
) -> ProviderToolCall:
    checked_name = _validated_tool_name(name, response=True)
    try:
        checked_arguments = _decode_tool_arguments(arguments)
    except (TypeError, ValueError) as exc:
        raise _invalid_response("模型返回的工具参数无效") from exc
    return ProviderToolCall(
        id=_validated_call_id(
            call_id,
            provider=provider,
            position=position,
            name=checked_name,
            arguments=checked_arguments,
            response=True,
        ),
        name=checked_name,
        arguments=checked_arguments,
    )


def _normalize_function_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        raise ValueError("工具定义必须是列表")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(tools):
        if not isinstance(item, dict):
            raise ValueError(f"工具定义 #{index + 1} 无效")
        tool_type = item.get("type", "function")
        if tool_type != "function":
            raise ValueError("目前仅支持 function 工具")
        function = item.get("function", item)
        if not isinstance(function, dict):
            raise ValueError(f"工具定义 #{index + 1} 无效")
        name = _validated_tool_name(function.get("name"), response=False)
        description = function.get("description", "")
        if not isinstance(description, str):
            raise ValueError("工具描述必须是字符串")
        parameters = function.get("parameters")
        if parameters is None:
            parameters = {"type": "object", "properties": {}}
        try:
            _validate_json_value(parameters)
            parameters = json.loads(
                json.dumps(parameters, ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("工具参数 Schema 必须是有效 JSON") from exc
        if not isinstance(parameters, dict):
            raise ValueError("工具参数 Schema 必须是 JSON 对象")
        declaration: dict[str, Any] = {"name": name, "parameters": parameters}
        if description:
            declaration["description"] = description
        normalized.append(declaration)
    return normalized


def _normalize_tool_messages(messages: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        raise ValueError("消息必须是列表")
    normalized: list[dict[str, Any]] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError("消息格式无效")
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            raise ValueError("消息角色必须是 user、assistant 或 tool")
        content = message.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ValueError("消息内容必须是字符串")
        if role == "user":
            if not content.strip():
                raise ValueError("用户消息不能为空")
            normalized.append({"role": "user", "content": content})
            continue
        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            name = _validated_tool_name(message.get("name"), response=False)
            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                raise ValueError("工具结果必须包含 tool_call_id")
            normalized.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id.strip(),
                    "name": name,
                    "content": content,
                }
            )
            continue
        raw_calls = message.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise ValueError("assistant tool_calls 必须是列表")
        calls: list[ProviderToolCall] = []
        for call_index, raw_call in enumerate(raw_calls):
            if isinstance(raw_call, ProviderToolCall):
                name = _validated_tool_name(raw_call.name, response=False)
                try:
                    arguments = _decode_tool_arguments(raw_call.arguments)
                except (TypeError, ValueError) as exc:
                    raise ValueError("assistant 工具参数无效") from exc
                call_id = raw_call.id
            elif isinstance(raw_call, dict):
                call_type = raw_call.get("type", "function")
                if call_type != "function":
                    raise ValueError("目前仅支持 function 工具调用")
                function = raw_call.get("function", raw_call)
                if not isinstance(function, dict):
                    raise ValueError("assistant 工具调用格式无效")
                name = _validated_tool_name(function.get("name"), response=False)
                try:
                    arguments = _decode_tool_arguments(
                        function.get("arguments", raw_call.get("arguments"))
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError("assistant 工具参数无效") from exc
                call_id = raw_call.get("id")
            else:
                raise ValueError("assistant 工具调用格式无效")
            calls.append(
                ProviderToolCall(
                    id=_validated_call_id(
                        call_id,
                        provider=provider,
                        position=message_index * 1000 + call_index,
                        name=name,
                        arguments=arguments,
                        response=False,
                    ),
                    name=name,
                    arguments=arguments,
                )
            )
        if not content.strip() and not calls:
            raise ValueError("assistant 消息必须包含内容或工具调用")
        normalized.append({"role": "assistant", "content": content, "tool_calls": calls})
    return normalized


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _checked_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentUnavailable("模型请求配置无效", "invalid_request") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise AgentUnavailable("模型请求内容过大", "invalid_request")
    return payload


def _parse_openai_tool_turn(result: dict[str, Any], provider: str) -> ProviderTurn:
    try:
        choice = result["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise _invalid_response() from exc
    if not isinstance(message, dict):
        raise _invalid_response()
    if message.get("role", "assistant") != "assistant":
        raise _invalid_response()
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise _invalid_response()
    calls: list[ProviderToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            raise _invalid_response()
        if raw_call.get("type", "function") != "function":
            raise _invalid_response()
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise _invalid_response()
        calls.append(
            _parse_response_tool_call(
                call_id=raw_call.get("id"),
                name=function.get("name"),
                arguments=function.get("arguments"),
                provider=provider,
                position=index,
            )
        )
    content = text_from_content(message.get("content"))
    if not content and not calls:
        raise AgentUnavailable("模型返回了空结果", "invalid_response")
    return ProviderTurn(content=content, tool_calls=calls)


def _parse_ollama_tool_turn(result: dict[str, Any], provider: str) -> ProviderTurn:
    message = result.get("message")
    if not isinstance(message, dict):
        raise _invalid_response()
    if message.get("role", "assistant") != "assistant":
        raise _invalid_response()
    raw_content = message.get("content", "")
    if raw_content is None:
        raw_content = ""
    if not isinstance(raw_content, str):
        raise _invalid_response()
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise _invalid_response()
    calls: list[ProviderToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            raise _invalid_response()
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise _invalid_response()
        calls.append(
            _parse_response_tool_call(
                call_id=raw_call.get("id"),
                name=function.get("name"),
                arguments=function.get("arguments"),
                provider=provider,
                position=index,
            )
        )
    content = raw_content.strip()
    if not content and not calls:
        raise AgentUnavailable("模型返回了空结果", "invalid_response")
    return ProviderTurn(content=content, tool_calls=calls)


def _parse_gemini_tool_turn(result: dict[str, Any], provider: str) -> ProviderTurn:
    try:
        content = result["candidates"][0]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise _invalid_response() from exc
    if not isinstance(content, dict) or content.get("role", "model") != "model":
        raise _invalid_response()
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise _invalid_response()
    text_parts: list[str] = []
    calls: list[ProviderToolCall] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            raise _invalid_response()
        if "text" in part:
            if not isinstance(part["text"], str):
                raise _invalid_response()
            if part["text"].strip():
                text_parts.append(part["text"].strip())
        raw_call = part.get("functionCall")
        if raw_call is None:
            continue
        if not isinstance(raw_call, dict):
            raise _invalid_response()
        calls.append(
            _parse_response_tool_call(
                call_id=raw_call.get("id"),
                name=raw_call.get("name"),
                arguments=raw_call.get("args"),
                provider=provider,
                position=index,
            )
        )
    content = "\n".join(text_parts).strip()
    if not content and not calls:
        raise AgentUnavailable("模型返回了空结果", "invalid_response")
    return ProviderTurn(content=content, tool_calls=calls)


def _gemini_function_response(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"result": content}
    _validate_json_value(value)
    if isinstance(value, dict):
        return value
    return {"result": value}


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
        budget = self.settings.max_output_tokens
        budgets = [budget]
        expanded = min(MAX_RETRY_OUTPUT_TOKENS, max(2048, budget * 4))
        if expanded > budget:
            budgets.append(expanded)
        if expanded < MAX_RETRY_OUTPUT_TOKENS:
            budgets.append(MAX_RETRY_OUTPUT_TOKENS)
        best = ""
        for attempt, attempt_budget in enumerate(budgets):
            attempt_payload = dict(payload)
            attempt_payload["max_tokens"] = attempt_budget
            result = await asyncio.to_thread(
                self._request_json, "POST", "/chat/completions", attempt_payload
            )
            try:
                choices = result["choices"]
                choice = choices[0]
                message = choice["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise AgentUnavailable("模型返回格式无效", "invalid_response") from exc
            content = text_from_content(message.get("content"))
            finish_reason = str(choice.get("finish_reason") or "")
            if len(content) > len(best):
                best = content
            if content and finish_reason != "length":
                return content
            last_attempt = attempt == len(budgets) - 1
            if last_attempt:
                break
        if best:
            # A truncated answer is still more useful than dropping the reply
            # entirely; structured tasks will surface their parse state to the
            # client and can be regenerated.
            return best
        raise AgentUnavailable("模型返回了空结果", "invalid_response")

    async def generate_tool_turn(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        declarations = _normalize_function_tools(tools)
        normalized_messages = _normalize_tool_messages(messages, self.settings.protocol)
        payload: dict[str, Any] = dict(self.settings.extra_parameters)
        openai_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        for message in normalized_messages:
            if message["role"] == "user":
                openai_messages.append(
                    {"role": "user", "content": message["content"]}
                )
            elif message["role"] == "assistant":
                calls = message["tool_calls"]
                openai_calls = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": _compact_json(call.arguments),
                        },
                    }
                    for call in calls
                ]
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": message["content"] or None,
                }
                if openai_calls:
                    assistant_message["tool_calls"] = openai_calls
                openai_messages.append(assistant_message)
            else:
                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                )
        payload.update(
            {
                "model": self.settings.model,
                "messages": openai_messages,
                "temperature": self.settings.temperature,
                "max_tokens": self.settings.max_output_tokens,
                "stream": False,
            }
        )
        if declarations:
            payload["tools"] = [
                {"type": "function", "function": declaration}
                for declaration in declarations
            ]
        if self.settings.reasoning_effort and self.settings.reasoning_effort != "none":
            payload["reasoning_effort"] = self.settings.reasoning_effort
        result = await asyncio.to_thread(
            self._request_json, "POST", "/chat/completions", _checked_payload(payload)
        )
        return _parse_openai_tool_turn(result, self.settings.protocol)


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

    async def generate_tool_turn(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        declarations = _normalize_function_tools(tools)
        normalized_messages = _normalize_tool_messages(messages, self.settings.protocol)
        options = dict(self.settings.extra_parameters)
        options.update(
            {
                "temperature": self.settings.temperature,
                "num_predict": self.settings.max_output_tokens,
            }
        )
        ollama_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        for message in normalized_messages:
            if message["role"] == "user":
                ollama_messages.append(
                    {"role": "user", "content": message["content"]}
                )
            elif message["role"] == "assistant":
                calls = message["tool_calls"]
                ollama_calls = [
                    {
                        "id": call.id,
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                    }
                    for call in calls
                ]
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": message["content"],
                }
                if ollama_calls:
                    assistant_message["tool_calls"] = ollama_calls
                ollama_messages.append(assistant_message)
            else:
                ollama_messages.append(
                    {
                        "role": "tool",
                        "tool_name": message["name"],
                        "content": message["content"],
                    }
                )
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": ollama_messages,
            "options": options,
            "stream": False,
            "think": self.settings.reasoning_effort != "none",
        }
        if declarations:
            payload["tools"] = [
                {"type": "function", "function": declaration}
                for declaration in declarations
            ]
        result = await asyncio.to_thread(
            self._request_json, "POST", "/api/chat", _checked_payload(payload)
        )
        return _parse_ollama_tool_turn(result, self.settings.protocol)


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

    async def generate_tool_turn(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderTurn:
        declarations = _normalize_function_tools(tools)
        normalized_messages = _normalize_tool_messages(messages, self.settings.protocol)
        contents: list[dict[str, Any]] = []
        for message in normalized_messages:
            if message["role"] == "user":
                contents.append(
                    {"role": "user", "parts": [{"text": message["content"]}]}
                )
            elif message["role"] == "assistant":
                parts: list[dict[str, Any]] = []
                if message["content"]:
                    parts.append({"text": message["content"]})
                parts.extend(
                    {
                        "functionCall": {
                            "id": call.id,
                            "name": call.name,
                            "args": call.arguments,
                        }
                    }
                    for call in message["tool_calls"]
                )
                contents.append({"role": "model", "parts": parts})
            else:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "id": message["tool_call_id"],
                                    "name": message["name"],
                                    "response": _gemini_function_response(
                                        message["content"]
                                    ),
                                }
                            }
                        ],
                    }
                )
        generation_config = dict(self.settings.extra_parameters)
        generation_config.update(
            {
                "temperature": self.settings.temperature,
                "maxOutputTokens": self.settings.max_output_tokens,
            }
        )
        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": generation_config,
        }
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
        model = self.settings.model.removeprefix("models/")
        result = await asyncio.to_thread(
            self._request_json,
            "POST",
            f"/models/{quote(model, safe='-._')}:generateContent",
            _checked_payload(payload),
        )
        return _parse_gemini_tool_turn(result, self.settings.protocol)


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
