from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from server.model_provider import (
    GeminiAdapter,
    OllamaAdapter,
    OpenAICompatibleProvider,
    ProviderSettings,
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


if __name__ == "__main__":
    unittest.main()

