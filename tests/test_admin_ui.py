from __future__ import annotations

import unittest
from pathlib import Path


class AdminUiTests(unittest.TestCase):
    def test_model_manager_contains_profile_workflow_and_responsive_layout(self) -> None:
        root = Path(__file__).parents[1] / "server" / "admin" / "static"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")
        for identifier in (
            'id="profile-list"',
            'id="discover-models"',
            'id="test-profile"',
            'id="activate-profile"',
            'name="extra_parameters"',
        ):
            self.assertIn(identifier, html)
        self.assertIn("/api/v1/admin/model-profiles", script)
        self.assertIn("clear_api_key", script)
        self.assertNotIn("api_key_mask", script)
        self.assertIn("@media(max-width:1100px)", styles)


if __name__ == "__main__":
    unittest.main()
