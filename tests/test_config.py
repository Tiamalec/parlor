from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from server.config import load_dotenv


class ConfigTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX permission check")
    def test_dotenv_rejects_group_or_world_readable_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            key = f"CHATROOM_TEST_{uuid.uuid4().hex}"
            path.write_text(f"{key}=value\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                load_dotenv(path)
            path.chmod(0o600)
            try:
                load_dotenv(path)
                self.assertEqual(os.environ.get(key), "value")
            finally:
                os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main()

