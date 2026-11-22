"""Command-line entry point for ``python -m client``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _config_path() -> Path:
    """Read .env from the working directory, or beside a packaged executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ".env"
    return Path.cwd() / ".env"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _load_dotenv(_config_path())
    parser = argparse.ArgumentParser(description="Parlor chatroom desktop client")
    parser.add_argument(
        "--host", default=os.environ.get("CLIENT_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("CLIENT_PORT", "8765"))
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="use the original Tkinter client",
    )
    args = parser.parse_args()
    if args.legacy:
        from .app import ChatApplication

        ChatApplication(args.host, args.port).run()
        return
    try:
        from .v2 import run
    except ImportError as exc:
        if exc.name == "PySide6" or str(exc).startswith("No module named 'PySide6"):
            parser.error("V2 客户端需要 PySide6。请先运行：python -m pip install -r requirements.txt；或使用 --legacy。")
        raise
    run(args.host, args.port)


if __name__ == "__main__":
    main()
