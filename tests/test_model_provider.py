from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from server.model_provider import (
    AgentUnavailable,
    GeminiAdapter,
    OllamaAdapter,
    OpenAICompatibleProvider,
    ProviderToolCall,
    ProviderSettings,
    ProviderTurn,
    probe_settings,
)


class _Response:
    def __init__(self, value: dict[str, object]) -> None:
        self.raw = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.raw


class ModelProviderTests(unittest.TestCase):
    def test_settings_validate_provider_url_and_ranges(self) -> None:
        settings = ProviderSettings(
            "ollama", "http://127.0.0.1:11434/v1/", "model", temperature=0.5
        ).validated()
        self.assertEqual(settings.protocol, "ollama_native")
        self.assertEqual(settings.base_url, "http://127.0.0.1:11434")
        with self.assertRaises(ValueError):
            ProviderSettings("unknown", "http://localhost/v1", "model").validated()
        with self.assertRaises(ValueError):
            ProviderSettings("ollama", "file:///tmp/model", "model").validated()
        with self.assertRaises(ValueError):
            ProviderSettings("openai_compatible", "http://models.example/v1", "model").validated()
        with self.assertRaises(ValueError):
            ProviderSettings("openai_compatible", "https://user:pass@models.example/v1", "model").validated()
        with self.assertRaises(ValueError):
            ProviderSettings("ollama", "http://models.example", "model").validated()
        with self.assertRaises(ValueError):
            ProviderSettings("ollama", "http://localhost/v1", "model", temperature=3).validated()

    def test_generate_uses_configured_inference_parameters(self) -> None:
        settings = ProviderSettings(
            "openai_compatible",
            "https://models.example/v1",
            "demo-model",
            temperature=0.7,
            max_output_tokens=321,
            timeout_seconds=2,
            reasoning_effort="low",
        ).validated()
        provider = OpenAICompatibleProvider(settings, "secret")
        captured: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: int) -> _Response:
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            captured["authorization"] = request.headers.get("Authorization")  # type: ignore[attr-defined]
            return _Response({"choices": [{"message": {"content": "ok"}}]})

        with patch("server.model_provider.URL_OPENER.open", fake_urlopen):
            result = asyncio.run(provider.generate("system", "user"))
        self.assertEqual(result, "ok")
        self.assertEqual(captured["timeout"], 2)
        self.assertEqual(captured["authorization"], "Bearer secret")
        payload = captured["payload"]
        self.assertEqual(payload["temperature"], 0.7)  # type: ignore[index]
        self.assertEqual(payload["max_tokens"], 321)  # type: ignore[index]
        self.assertEqual(payload["reasoning_effort"], "low")  # type: ignore[index]

    def test_adapters_list_models_and_map_native_payloads(self) -> None:
        ollama = OllamaAdapter(
            ProviderSettings("ollama_native", "http://localhost:11434", "qwen", auth_type="none").validated(),
            "",
        )
        gemini = GeminiAdapter(
            ProviderSettings(
                "gemini_native",
                "https://generativelanguage.googleapis.com/v1beta",
                "gemini-flash",
                auth_type="x-goog-api-key",
            ).validated(),
            "google-secret",
        )
        requests: list[object] = []

        def fake_urlopen(request: object, timeout: int) -> _Response:
            requests.append(request)
            url = request.full_url  # type: ignore[attr-defined]
            if url.endswith("/api/tags"):
                return _Response({"models": [{"name": "qwen:latest"}]})
            if url.endswith("/api/chat"):
                payload = json.loads(request.data.decode())  # type: ignore[attr-defined]
                self.assertEqual(payload["options"]["num_predict"], 512)
                self.assertFalse(payload["think"])
                return _Response({"message": {"content": "ollama ok"}})
            if url.endswith("/models"):
                self.assertEqual(request.headers.get("X-goog-api-key"), "google-secret")  # type: ignore[attr-defined]
                return _Response({"models": [{"name": "models/gemini-flash", "supportedGenerationMethods": ["generateContent"]}]})
            payload = json.loads(request.data.decode())  # type: ignore[attr-defined]
            self.assertIn("system_instruction", payload)
            return _Response({"candidates": [{"content": {"parts": [{"text": "gemini ok"}]}}]})

        with patch("server.model_provider.URL_OPENER.open", fake_urlopen):
            self.assertEqual(asyncio.run(ollama.list_models()), ["qwen:latest"])
            self.assertEqual(asyncio.run(ollama.generate("system", "user")), "ollama ok")
            self.assertEqual(asyncio.run(gemini.list_models()), ["gemini-flash"])
            self.assertEqual(asyncio.run(gemini.generate("system", "user")), "gemini ok")
        self.assertEqual(len(requests), 4)

    def test_connection_probe_disables_reasoning_and_keeps_enough_output_budget(self) -> None:
        settings = ProviderSettings(
            "ollama_native",
            "http://localhost:11434",
            "qwen3.5:4b",
            auth_type="none",
            max_output_tokens=512,
            timeout_seconds=45,
            reasoning_effort="high",
        )
        probe = probe_settings(settings)
        self.assertEqual(probe.max_output_tokens, 64)
        self.assertEqual(probe.timeout_seconds, 15)
        self.assertEqual(probe.reasoning_effort, "none")

    def test_openai_empty_or_truncated_result_retries_with_larger_budget(self) -> None:
        settings = ProviderSettings(
            "openai_chat",
            "https://models.example/v1",
            "deepseek-flash",
            max_output_tokens=512,
        ).validated()
        provider = OpenAICompatibleProvider(settings, "secret")
        budgets: list[int] = []

        def fake_urlopen(request: object, timeout: int) -> _Response:
            payload = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            budgets.append(payload["max_tokens"])
            if len(budgets) == 1:
                return _Response(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "",
                                    "reasoning_content": "先分析问题",
                                },
                                "finish_reason": "length",
                            }
                        ]
                    }
                )
            return _Response(
                {"choices": [{"message": {"content": "可以正常回复"}, "finish_reason": "stop"}]}
            )

        with patch("server.model_provider.URL_OPENER.open", fake_urlopen):
            result = asyncio.run(provider.generate("system", "user"))
        self.assertEqual(result, "可以正常回复")
        self.assertEqual(budgets, [512, 2048])

    def test_openai_content_blocks_are_flattened(self) -> None:
        settings = ProviderSettings(
            "openai_chat", "https://models.example/v1", "model"
        ).validated()
        provider = OpenAICompatibleProvider(settings, "secret")

        def fake_urlopen(_request: object, timeout: int) -> _Response:
            return _Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "第一段"},
                                    {"type": "text", "text": "第二段"},
                                ]
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            )

        with patch("server.model_provider.URL_OPENER.open", fake_urlopen):
            self.assertEqual(asyncio.run(provider.generate("system", "user")), "第一段\n第二段")

    def test_extra_parameters_cannot_override_protocol_fields(self) -> None:
        with self.assertRaises(ValueError):
            ProviderSettings(
                "openai_chat",
                "https://models.example/v1",
                "model",
                extra_parameters={"messages": []},
            ).validated()

    def test_openai_tool_turn_maps_native_payload_and_calls(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderSettings(
                "openai_chat",
                "https://models.example/v1",
                "demo-model",
                max_output_tokens=321,
                reasoning_effort="low",
            ).validated(),
            "secret",
        )
        captured: dict[str, object] = {}
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Read current weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ]

        def fake_urlopen(request: object, timeout: int) -> _Response:
            captured["url"] = request.full_url  # type: ignore[attr-defined]
            captured["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            return _Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "Checking.",
                                "tool_calls": [
                                    {
                                        "id": "call_openai",
                                        "type": "function",
                                        "function": {
                                            "name": "get_weather",
                                            "arguments": '{"city":"Paris"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            )

        with patch("server.model_provider.URL_OPENER.open", fake_urlopen):
            turn = asyncio.run(
                provider.generate_tool_turn(
                    "system", [{"role": "user", "content": "Weather?"}], tools
                )
            )

        self.assertEqual(turn, ProviderTurn("Checking.", [ProviderToolCall("call_openai", "get_weather", {"city": "Paris"})]))
        self.assertTrue(str(captured["url"]).endswith("/chat/completions"))
        payload = captured["payload"]
        self.assertEqual(payload["messages"], [  # type: ignore[index]
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Weather?"},
        ])
        self.assertEqual(payload["tools"][0]["function"]["name"], "get_weather")  # type: ignore[index]
        self.assertEqual(payload["max_tokens"], 321)  # type: ignore[index]
        self.assertEqual(payload["reasoning_effort"], "low")  # type: ignore[index]

    def test_ollama_tool_turn_maps_tools_and_stable_generated_ids(self) -> None:
        provider = OllamaAdapter(
            ProviderSettings(
                "ollama_native",
                "http://localhost:11434",
                "qwen",
                auth_type="none",
                max_output_tokens=222,
            ).validated(),
            "",
        )
        payloads: list[dict[str, object]] = []
        response = {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "get_weather",
                            "arguments": {"city": "Rome"},
                        }
                    }
                ],
            }
        }

        def fake_urlopen(request: object, timeout: int) -> _Response:
            payloads.append(json.loads(request.data.decode("utf-8")))  # type: ignore[attr-defined]
            return _Response(response)

        tools = [
            {
                "name": "get_weather",
                "description": "Read current weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            }
        ]
        with patch("server.model_provider.URL_OPENER.open", fake_urlopen):
            first = asyncio.run(
                provider.generate_tool_turn(
                    "system", [{"role": "user", "content": "Weather?"}], tools
                )
            )
            second = asyncio.run(
                provider.generate_tool_turn(
                    "system", [{"role": "user", "content": "Weather?"}], tools
                )
            )

        self.assertEqual(first.tool_calls[0].name, "get_weather")
        self.assertEqual(first.tool_calls[0].arguments, {"city": "Rome"})
        self.assertEqual(first.tool_calls[0].id, second.tool_calls[0].id)
        self.assertTrue(first.tool_calls[0].id.startswith("call_"))
        self.assertEqual(payloads[0]["tools"][0]["type"], "function")  # type: ignore[index]
        self.assertEqual(payloads[0]["options"]["num_predict"], 222)  # type: ignore[index]

    def test_gemini_tool_turn_maps_function_declarations_and_calls(self) -> None:
        provider = GeminiAdapter(
            ProviderSettings(
                "gemini_native",
                "https://generativelanguage.googleapis.com/v1beta",
                "gemini-flash",
                auth_type="x-goog-api-key",
                max_output_tokens=199,
            ).validated(),
            "google-secret",
        )
        captured: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: int) -> _Response:
            captured["url"] = request.full_url  # type: ignore[attr-defined]
            captured["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            return _Response(
                {
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {"text": "I can check."},
                                    {
                                        "functionCall": {
                                            "name": "get_weather",
                                            "args": {"city": "Oslo"},
                                        }
                                    },
                                ],
                            }
                        }
                    ]
                }
            )

        tools = [
            {
                "name": "get_weather",
                "description": "Read current weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            }
        ]
        with patch("server.model_provider.URL_OPENER.open", fake_urlopen):
            turn = asyncio.run(
                provider.generate_tool_turn(
                    "system", [{"role": "user", "content": "Weather?"}], tools
                )
            )

        self.assertEqual(turn.content, "I can check.")
        self.assertEqual(turn.tool_calls[0].name, "get_weather")
        self.assertEqual(turn.tool_calls[0].arguments, {"city": "Oslo"})
        self.assertTrue(turn.tool_calls[0].id.startswith("call_"))
        payload = captured["payload"]
        self.assertEqual(payload["tools"][0]["functionDeclarations"][0]["name"], "get_weather")  # type: ignore[index]
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 199)  # type: ignore[index]
        self.assertTrue(str(captured["url"]).endswith("/models/gemini-flash:generateContent"))

    def test_multi_turn_tool_result_requests_use_each_native_format(self) -> None:
        messages = [
            {"role": "user", "content": "Weather?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "name": "get_weather",
                        "arguments": {"city": "Paris"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_weather",
                "name": "get_weather",
                "content": '{"temperature":22}',
            },
        ]
        tools = [{"name": "get_weather", "parameters": {"type": "object"}}]
        captured: dict[str, dict[str, object]] = {}

        openai = OpenAICompatibleProvider(
            ProviderSettings("openai_chat", "https://models.example/v1", "openai").validated(),
            "secret",
        )
        ollama = OllamaAdapter(
            ProviderSettings("ollama_native", "http://localhost:11434", "qwen", auth_type="none").validated(),
            "",
        )
        gemini = GeminiAdapter(
            ProviderSettings(
                "gemini_native",
                "https://generativelanguage.googleapis.com/v1beta",
                "gemini",
                auth_type="none",
            ).validated(),
            "",
        )

        def openai_response(request: object, timeout: int) -> _Response:
            captured["openai"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            return _Response({"choices": [{"message": {"content": "Sunny"}}]})

        def ollama_response(request: object, timeout: int) -> _Response:
            captured["ollama"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            return _Response({"message": {"content": "Sunny"}})

        def gemini_response(request: object, timeout: int) -> _Response:
            captured["gemini"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            return _Response({"candidates": [{"content": {"parts": [{"text": "Sunny"}]}}]})

        with patch("server.model_provider.URL_OPENER.open", openai_response):
            self.assertEqual(
                asyncio.run(openai.generate_tool_turn("system", messages, tools)).content,
                "Sunny",
            )
        with patch("server.model_provider.URL_OPENER.open", ollama_response):
            self.assertEqual(
                asyncio.run(ollama.generate_tool_turn("system", messages, tools)).content,
                "Sunny",
            )
        with patch("server.model_provider.URL_OPENER.open", gemini_response):
            self.assertEqual(
                asyncio.run(gemini.generate_tool_turn("system", messages, tools)).content,
                "Sunny",
            )

        openai_messages = captured["openai"]["messages"]  # type: ignore[index]
        self.assertIsNone(openai_messages[2]["content"])
        self.assertEqual(openai_messages[2]["tool_calls"][0]["function"]["arguments"], '{"city":"Paris"}')
        self.assertEqual(openai_messages[3]["tool_call_id"], "call_weather")
        ollama_messages = captured["ollama"]["messages"]  # type: ignore[index]
        self.assertEqual(ollama_messages[2]["tool_calls"][0]["function"]["arguments"], {"city": "Paris"})
        self.assertEqual(ollama_messages[3]["tool_name"], "get_weather")
        gemini_contents = captured["gemini"]["contents"]  # type: ignore[index]
        self.assertEqual(gemini_contents[1]["role"], "model")
        self.assertEqual(gemini_contents[1]["parts"][0]["functionCall"]["name"], "get_weather")
        self.assertEqual(gemini_contents[2]["parts"][0]["functionResponse"]["response"], {"temperature": 22})

    def test_tool_turn_rejects_malformed_native_arguments(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderSettings("openai_chat", "https://models.example/v1", "model").validated(),
            "secret",
        )

        def fake_urlopen(request: object, timeout: int) -> _Response:
            return _Response(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_bad",
                                        "type": "function",
                                        "function": {
                                            "name": "get_weather",
                                            "arguments": "{not-json}",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            )

        with patch("server.model_provider.URL_OPENER.open", fake_urlopen):
            with self.assertRaises(AgentUnavailable) as raised:
                asyncio.run(
                    provider.generate_tool_turn(
                        "system",
                        [{"role": "user", "content": "Weather?"}],
                        [{"name": "get_weather"}],
                    )
                )
        self.assertEqual(raised.exception.category, "invalid_response")


if __name__ == "__main__":
    unittest.main()

