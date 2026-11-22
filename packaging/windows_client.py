"""PyInstaller entry point for the V2 Windows client."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.__main__ import main


if __name__ == "__main__":
    main()
