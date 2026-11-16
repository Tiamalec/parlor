from __future__ import annotations

import asyncio
import logging

from .app import ChatServer
from .admin.auth import AdminSessionStore
from .admin.config_store import ProviderConfigStore
from .admin.http import AdminHttpServer
from .admin.service import AdminService
from .config import Config


LOGGER = logging.getLogger("chatroom.main")


async def _run_admin(admin: AdminHttpServer) -> None:
    try:
        await admin.serve_forever()
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.exception("管理面板意外停止；TCP 聊天服务将继续运行")
    finally:
        await admin.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    server = ChatServer(config)
    admin: AdminHttpServer | None = None
    if config.admin_enabled:
        try:
            admin_service = AdminService(
                server.database,
                server.agent,
                server,
                ProviderConfigStore(config.data_dir),
                AdminSessionStore(config.admin_session_ttl_seconds),
                config.admin_log_capacity,
            )
            admin = AdminHttpServer(config, admin_service)
        except Exception:
            LOGGER.exception("管理面板初始化失败；TCP 聊天服务将继续运行")
    admin_task: asyncio.Task[None] | None = None
    try:
        await server.start()
        if admin:
            try:
                await admin.start()
                admin_task = asyncio.create_task(_run_admin(admin), name="admin-console")
            except Exception:
                LOGGER.exception("管理面板启动失败；TCP 聊天服务将继续运行")
                await admin.close()
                admin = None
        await server.serve_forever()
    finally:
        if admin_task:
            admin_task.cancel()
            await asyncio.gather(admin_task, return_exceptions=True)
        elif admin:
            await admin.close()
        await server.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

