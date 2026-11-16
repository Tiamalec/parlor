from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from server.admin.auth import AdminAuthError, AdminSessionStore
from server.admin.config_store import ProviderConfigStore
from server.admin.service import AdminRequestError, AdminService, LogBufferHandler
from server.agent import AgentService
from server.config import Config
from server.database import Database
from server.model_provider import ProviderSettings


class FakeChatServer:
    def __init__(self) -> None:
        self.published = 0

    def admin_status_snapshot(self) -> dict[str, Any]:
        return {
            "server_version": "test",
            "tcp_listening": True,
            "tcp_addresses": ["('127.0.0.1', 1234)"],
            "uptime_seconds": 10,
            "connection_count": 2,
            "authenticated_connections": 2,
            "online_users": 1,
        }

    def online_user_ids(self) -> set[int]:
        return {1}

    async def publish_agent_status(self) -> None:
        self.published += 1


class AdminServiceTests(unittest.TestCase):
    def test_v1_provider_snapshot_migrates_to_v2_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ProviderConfigStore(root)
            store.directory.mkdir(parents=True)
            store.settings_path.write_text(
                json.dumps(
                    {
                        "kind": "openai_compatible",
                        "base_url": "https://models.example/v1",
                        "model": "legacy-model",
                        "api_key": "legacy-secret",
                        "agent_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            store.settings_path.chmod(0o600)
            fallback = ProviderSettings("ollama", "http://localhost:11434/v1", "fallback", auth_type="none").validated()
            state = store.load_state(fallback, "", True)
            self.assertEqual(state.active().settings.protocol, "openai_chat")
            self.assertEqual(state.active().api_key, "legacy-secret")
            self.assertFalse(state.agent_enabled)
            migrated = json.loads(store.settings_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], 2)

    def test_corrupt_persisted_provider_falls_back_to_bootstrap_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ProviderConfigStore(root)
            store.directory.mkdir(parents=True)
            store.settings_path.write_text(
                '{"kind":"ollama","base_url":"http://127.0.0.1:11434/v1",'
                '"model":"broken","timeout_seconds":Infinity,"api_key":"bad"}',
                encoding="utf-8",
            )
            store.settings_path.chmod(0o600)
            defaults = Config.from_env()
            fallback_settings = AgentService(
                defaults,
                Database(root / "fallback.sqlite3", root / "uploads"),
                AsyncMock(),
            ).settings
            settings, key, enabled = store.load(fallback_settings, "fallback-key", True)
            self.assertEqual(settings, fallback_settings)
            self.assertEqual(key, "fallback-key")
            self.assertFalse(enabled)

    def test_corrupt_nondefault_profile_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ProviderConfigStore(root)
            fallback = ProviderSettings("ollama", "http://localhost:11434", "fallback", auth_type="none").validated()
            state = store.load_state(fallback, "", True)
            store.save_state(state)
            raw = json.loads(store.settings_path.read_text(encoding="utf-8"))
            raw["profiles"].append(
                {
                    "id": "broken-draft",
                    "name": "损坏草稿",
                    "protocol": "unknown",
                    "base_url": "not-a-url",
                    "model": "broken",
                }
            )
            store.settings_path.write_text(json.dumps(raw), encoding="utf-8")
            store.settings_path.chmod(0o600)
            loaded = store.load_state(fallback, "", True)
            self.assertEqual(loaded.default_profile_id, state.default_profile_id)
            self.assertEqual(len(loaded.profiles), 1)
            self.assertTrue(loaded.agent_enabled)

    def test_corrupt_default_profile_recovers_last_valid_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ProviderConfigStore(root)
            fallback = ProviderSettings("ollama", "http://localhost:11434", "fallback", auth_type="none").validated()
            original = store.load_state(fallback, "", True)
            store.save_state(original)
            store.save_state(original)  # creates a validated v2 backup
            raw = json.loads(store.settings_path.read_text(encoding="utf-8"))
            raw["profiles"][0]["protocol"] = "broken"
            store.settings_path.write_text(json.dumps(raw), encoding="utf-8")
            store.settings_path.chmod(0o600)
            recovered = store.load_state(fallback, "", False)
            self.assertEqual(recovered.active().settings.model, "fallback")
            self.assertTrue(recovered.agent_enabled)

    def test_admin_auth_provider_persistence_and_safe_views(self) -> None:
        asyncio.run(self._scenario())

    async def _scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            admin = database.register_user("admin01", "password-admin", role="admin")
            database.register_user("normal", "password-user")
            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_api_key="initial-secret",
            )

            async def emit(*_args: object) -> None:
                return None

            agent = AgentService(config, database, emit)
            chat = FakeChatServer()
            store = ProviderConfigStore(root)
            service = AdminService(
                database, agent, chat, store, AdminSessionStore(600), 100
            )
            try:
                with self.assertRaises(AdminAuthError):
                    await service.login("normal", "password-user", "127.0.0.1")
                token, session = await service.login(
                    "admin01", "password-admin", "127.0.0.1"
                )
                self.assertTrue(token)
                self.assertEqual(session.user_id, admin["id"])

                agent.test_provider = AsyncMock(return_value=True)  # type: ignore[method-assign]
                view = await service.test_and_apply_provider(
                    session,
                    {
                        "kind": "openai_compatible",
                        "base_url": "https://models.example/v1",
                        "model": "chat-model",
                        "api_key": "new-secret-value",
                        "temperature": 0.4,
                        "max_output_tokens": 400,
                        "timeout_seconds": 3,
                        "reasoning_effort": "none",
                        "max_concurrency": 2,
                    },
                )
                self.assertNotIn("api_key", view)
                self.assertTrue(view["api_key_set"])
                snapshot_raw = json.loads(store.settings_path.read_text(encoding="utf-8"))
                self.assertEqual(snapshot_raw["schema_version"], 2)
                self.assertEqual(snapshot_raw["profiles"][0]["api_key"], "new-secret-value")
                self.assertEqual(store.settings_path.stat().st_mode & 0o777, 0o600)
                loaded_settings, loaded_key, loaded_enabled = store.load(
                    config_store_default := agent.settings, "fallback", agent.enabled
                )
                self.assertEqual(loaded_settings, config_store_default)
                self.assertEqual(loaded_key, "new-secret-value")
                self.assertTrue(loaded_enabled)
                previous = agent.settings
                agent.test_provider = AsyncMock(return_value=False)  # type: ignore[method-assign]
                with self.assertRaises(AdminRequestError):
                    await service.test_and_apply_provider(
                        session,
                        {
                            "kind": "openai_compatible",
                            "base_url": "https://unavailable.example/v1",
                            "model": "bad-model",
                        },
                    )
                self.assertEqual(agent.settings, previous)
                self.assertNotIn(
                    "secret-value",
                    LogBufferHandler.sanitize('{"api_key":"secret-value"}'),
                )

                status = await service.set_agent_enabled(session, False)
                self.assertFalse(status["enabled"])
                self.assertEqual(chat.published, 2)
                dashboard = await service.dashboard()
                self.assertEqual(dashboard["online_users"], 1)
                users = await service.users()
                self.assertTrue(next(item for item in users if item["id"] == admin["id"])["online"])
                audit = await service.audit()
                self.assertTrue(any(item["action"] == "model-provider.apply" for item in audit))
                self.assertNotIn("new-secret-value", json.dumps(audit))
            finally:
                service.close()

    def test_multiple_profiles_test_activate_and_delete_rules(self) -> None:
        asyncio.run(self._profiles_scenario())

    async def _profiles_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            database.register_user("admin01", "password-admin", role="admin")
            config = replace(Config.from_env(), data_dir=root, database_path=root / "chat.sqlite3", uploads_dir=root / "uploads")

            async def emit(*_args: object) -> None:
                return None

            agent = AgentService(config, database, emit)
            chat = FakeChatServer()
            service = AdminService(database, agent, chat, ProviderConfigStore(root), AdminSessionStore(600), 100)
            try:
                _token, session = await service.login("admin01", "password-admin", "127.0.0.1")
                original = service.model_profiles()["profiles"][0]
                draft = await service.save_model_profile(
                    session,
                    {
                        "name": "Ollama 新草稿",
                        "preset_id": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model": "different-local-model",
                    },
                    original["id"],
                )
                self.assertNotEqual(draft["id"], original["id"])
                self.assertEqual(service.model_profiles()["default_profile_id"], original["id"])
                self.assertNotEqual(agent.settings.model, "different-local-model")
                created = await service.save_model_profile(
                    session,
                    {
                        "name": "DeepSeek 主配置",
                        "preset_id": "deepseek",
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-flash",
                        "api_key": "profile-secret",
                    },
                )
                self.assertNotIn("api_key", created)
                self.assertTrue(created["api_key_set"])
                agent.probe_provider = AsyncMock(return_value=(True, None))  # type: ignore[method-assign]
                tested = await service.test_model_profile(session, created["id"])
                self.assertTrue(tested["available"])
                previous_default = service.model_profiles()["default_profile_id"]
                original_apply = agent.apply_provider

                def fail_new_provider(
                    settings: ProviderSettings, api_key: str, provider_label: str | None = None
                ) -> None:
                    if settings.model == "deepseek-flash":
                        raise RuntimeError("injected apply failure")
                    original_apply(settings, api_key, provider_label)

                agent.apply_provider = fail_new_provider  # type: ignore[method-assign]
                with self.assertRaises(AdminRequestError):
                    await service.activate_model_profile(session, created["id"])
                self.assertEqual(service.model_profiles()["default_profile_id"], previous_default)
                agent.apply_provider = original_apply  # type: ignore[method-assign]
                active = await service.activate_model_profile(session, created["id"])
                self.assertTrue(active["is_default"])
                self.assertEqual(agent.settings.model, "deepseek-flash")
                with self.assertRaises(AdminRequestError):
                    await service.delete_model_profile(session, created["id"])
                self.assertNotIn("profile-secret", json.dumps(service.model_profiles()))
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()

