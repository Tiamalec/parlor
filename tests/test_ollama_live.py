from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from server.agent import AgentService
from server.config import Config
from server.database import Database


@unittest.skipUnless(os.getenv("RUN_OLLAMA_LIVE") == "1", "set RUN_OLLAMA_LIVE=1 to test the installed local model")
class OllamaLiveTests(unittest.TestCase):
    def test_real_local_model_completes_agent_tool_chain(self) -> None:
        asyncio.run(self._scenario())

    async def _scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Database(root / "chat.sqlite3", root / "uploads")
            database.initialize()
            owner = database.register_user("owner", "password-owner")
            group = database.create_group("本地模型联调群", owner["id"])
            database.save_message("group", str(group["id"]), owner["id"], "owner", "text", "我们决定使用 asyncio TCP 服务端。")
            config = replace(
                Config.from_env(),
                data_dir=root,
                database_path=root / "chat.sqlite3",
                uploads_dir=root / "uploads",
                agent_timeout_seconds=45,
                agent_cloud_fallback=False,
            )
            events: list[tuple[str, dict[str, Any]]] = []

            async def emit(_scope_type: str, _scope_id: str, event_type: str, payload: dict[str, Any]) -> None:
                events.append((event_type, payload))

            service = AgentService(config, database, emit)
            self.assertTrue(await service.refresh_health())
            await service.invoke(owner["id"], "group", str(group["id"]), "总结最近20条消息")
            history = database.recent_history(owner["id"], "group", str(group["id"]), 10)
            self.assertEqual(history[-1]["sender_kind"], "agent", events)
            self.assertTrue(history[-1]["content"].strip())
            self.assertTrue(any(kind == "agent.result" and payload.get("status") == "succeeded" for kind, payload in events))


if __name__ == "__main__":
    unittest.main()

