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

    def test_sticker_library_exposes_shared_and_role_card_scope(self) -> None:
        root = Path(__file__).parents[1] / "server" / "admin" / "static"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        for identifier in (
            'name="shared"',
            'id="sticker-agent-options"',
            'id="sticker-filter"',
        ):
            self.assertIn(identifier, html)
        self.assertIn("/api/v1/admin/agents", script)
        self.assertIn("agent_ids", script)
        self.assertIn("sticker.agent_ids", script)

    def test_admin_can_create_preset_role_cards_that_feed_sticker_scope(self) -> None:
        root = Path(__file__).parents[1] / "server" / "admin" / "static"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        for identifier in (
            'data-page="roles"',
            'id="role-card-form"',
            'id="role-card-list"',
            'name="personality"',
            'name="behavior_rules"',
        ):
            self.assertIn(identifier, html)
        self.assertIn("/api/v1/admin/role-cards", script)
        self.assertIn("loadRoleCards", script)


if __name__ == "__main__":
    unittest.main()
