from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QSettings, Qt
    from PySide6.QtGui import QColor, QImage, QPalette
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class V2ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from client.v2.theme import reset_theme_manager

        reset_theme_manager()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._palette = QPalette(self.app.palette())
        self._stylesheet = self.app.styleSheet()
        self._settings_dir = tempfile.TemporaryDirectory()
        self.settings = QSettings(
            os.path.join(self._settings_dir.name, "theme.ini"),
            QSettings.Format.IniFormat,
        )
        self.system_scheme = Qt.ColorScheme.Dark

    def tearDown(self) -> None:
        from client.v2.theme import reset_theme_manager

        self.app.processEvents()
        for widget in self.app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        reset_theme_manager()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.setPalette(self._palette)
        self.app.setStyleSheet(self._stylesheet)
        self.app.processEvents()
        self._settings_dir.cleanup()

    def _install(self):
        from client.v2.theme import install_theme_manager

        return install_theme_manager(
            self.app,
            self.settings,
            system_scheme_getter=lambda: self.system_scheme,
        )

    def test_tokens_are_complete_and_primary_text_meets_aa(self) -> None:
        from client.v2.theme import DARK_TOKENS, LIGHT_TOKENS, contrast_ratio

        self.assertEqual(set(LIGHT_TOKENS.as_dict()), set(DARK_TOKENS.as_dict()))
        self.assertTrue(all(value for value in LIGHT_TOKENS.as_dict().values()))
        self.assertTrue(all(value for value in DARK_TOKENS.as_dict().values()))
        for tokens in (LIGHT_TOKENS, DARK_TOKENS):
            self.assertGreaterEqual(contrast_ratio(tokens.text_primary, tokens.canvas), 4.5)
            self.assertGreaterEqual(contrast_ratio(tokens.text_primary, tokens.surface), 4.5)
            self.assertGreaterEqual(contrast_ratio(tokens.text_primary, tokens.message_incoming), 4.5)
            self.assertGreaterEqual(contrast_ratio(tokens.text_secondary, tokens.canvas), 4.5)
            self.assertGreaterEqual(contrast_ratio(tokens.text_tertiary, tokens.surface), 4.5)
            self.assertGreaterEqual(contrast_ratio(tokens.mention_user_fg, tokens.mention_user_bg), 4.5)
            self.assertGreaterEqual(contrast_ratio(tokens.mention_agent_fg, tokens.mention_agent_bg), 4.5)

    def test_modes_persist_and_system_refreshes_without_restart(self) -> None:
        from client.v2.theme import ThemeMode, ThemeVariant, reset_theme_manager

        manager = self._install()
        self.assertEqual(manager.mode, ThemeMode.SYSTEM)
        self.assertEqual(manager.snapshot.variant, ThemeVariant.DARK)
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Window).name().upper(),
            manager.tokens.canvas,
        )

        manager.set_mode(ThemeMode.LIGHT)
        self.assertEqual(self.settings.value(manager.SETTINGS_KEY), ThemeMode.LIGHT.value)
        self.assertEqual(manager.snapshot.variant, ThemeVariant.LIGHT)
        reset_theme_manager()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        manager = self._install()
        self.assertEqual(manager.mode, ThemeMode.LIGHT)
        self.assertEqual(manager.snapshot.variant, ThemeVariant.LIGHT)

        manager.set_mode(ThemeMode.SYSTEM)
        revision = manager.snapshot.revision
        self.system_scheme = Qt.ColorScheme.Light
        manager._schedule_system_refresh()
        self.app.processEvents()
        self.assertEqual(manager.snapshot.variant, ThemeVariant.LIGHT)
        self.assertGreater(manager.snapshot.revision, revision)
        self.system_scheme = Qt.ColorScheme.Dark
        manager._schedule_system_refresh()
        self.app.processEvents()
        self.assertEqual(manager.snapshot.variant, ThemeVariant.DARK)

    def test_dark_surfaces_dialogs_and_menus_paint_semantic_backgrounds(self) -> None:
        from PySide6.QtWidgets import QDialog, QMenu, QScrollArea, QWidget

        from client.v2.theme import DARK_TOKENS, ThemeMode
        from client.v2.widgets import configure_scroll_surface

        manager = self._install()
        manager.set_mode(ThemeMode.DARK)

        def center_color(widget) -> str:
            widget.resize(120, 80)
            widget.ensurePolished()
            image = QImage(widget.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            widget.render(image)
            return image.pixelColor(image.width() // 2, image.height() // 2).name().upper()

        area = QScrollArea()
        content = QWidget()
        content.resize(100, 60)
        configure_scroll_surface(area, content, "canvas")
        area.setWidget(content)
        dialog = QDialog()
        menu = QMenu()
        menu.addAction("主题操作")

        self.assertEqual(center_color(content), DARK_TOKENS.canvas)
        self.assertEqual(center_color(dialog), DARK_TOKENS.surface_elevated)
        self.assertEqual(center_color(menu), DARK_TOKENS.surface_elevated)

    def test_invalid_preference_falls_back_to_system(self) -> None:
        from client.v2.theme import ThemeMode

        self.settings.setValue("ui/theme_mode", "not-a-theme")
        manager = self._install()
        self.assertEqual(manager.mode, ThemeMode.SYSTEM)

    def test_rich_text_mentions_avatar_and_switch_refresh_in_place(self) -> None:
        from client.v2.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeMode
        from client.v2.widgets import Avatar, MentionTextEdit, MessageBubble, ToggleSwitch

        manager = self._install()
        manager.set_mode(ThemeMode.LIGHT)
        message = {
            "id": 7,
            "message_type": "agent",
            "content_format": "markdown",
            "content": "## 结果\n\n```text\nstatus=ok\n```",
        }
        bubble = MessageBubble(message, mine=False)
        editor = MentionTextEdit()
        editor.insertPlainText("@bo")
        self.app.processEvents()
        editor.insert_mention({"member_id": "user:2", "username": "bob", "kind": "user"})
        editor.insertPlainText(" 请确认")
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        original_text = editor.toPlainText()
        original_mentions = editor.mentions()
        original_cursor = editor.textCursor().position()
        avatar = Avatar("Alice", 38)
        switch = ToggleSwitch()
        switch.setChecked(True)

        def rendered(widget) -> QImage:
            image = QImage(widget.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            widget.render(image)
            return image

        light_avatar = avatar.pixmap().toImage().pixelColor(2, 19)
        light_switch = rendered(switch).pixelColor(5, 12)
        self.assertIn(LIGHT_TOKENS.code_bg.lower(), bubble.content_label.text().lower())

        manager.set_mode(ThemeMode.DARK)
        self.app.processEvents()
        self.assertIs(bubble.message, message)
        self.assertIn(DARK_TOKENS.code_bg.lower(), bubble.content_label.text().lower())
        self.assertEqual(editor.toPlainText(), original_text)
        self.assertEqual(editor.mentions(), original_mentions)
        self.assertEqual(editor.textCursor().position(), original_cursor)
        self.assertEqual(
            editor.document().begin().begin().fragment().charFormat().foreground().color(),
            QColor(DARK_TOKENS.mention_user_fg),
        )
        self.assertNotEqual(avatar.pixmap().toImage().pixelColor(2, 19), light_avatar)
        self.assertNotEqual(rendered(switch).pixelColor(5, 12), light_switch)

    def test_settings_switch_preserves_chat_and_store_state(self) -> None:
        from client.models import ConversationKey
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import ChatPane, SettingsPage
        from client.v2.theme import ThemeMode, ThemeVariant

        manager = self._install()
        store = AppStore()
        service = ChatService(NetworkClient("127.0.0.1", 9), store)
        chat = ChatPane(service, store)
        settings_page = SettingsPage(service, store, "127.0.0.1", 9)
        key = ConversationKey("group", "7")
        store.state.messages[key] = [
            {
                "id": index,
                "message_type": "text",
                "content": f"消息 {index} " * 8,
                "sender_id": 2,
                "sender_name": "bob",
                "created_at": index,
            }
            for index in range(1, 35)
        ]
        chat.set_conversation(key, "项目组")
        chat.compose.setPlainText("尚未发送的内容")
        chat.resize(760, 520)
        chat.show()
        self.app.processEvents()
        scroll_bar = chat.scroll.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum() // 2)
        scroll_position = scroll_bar.value()
        message_widget_ids = {key: id(value) for key, value in chat._message_rows.items()}
        state_identity = id(store.state)
        network_identity = id(service.network)

        settings_page.theme_mode.setCurrentIndex(settings_page.theme_mode.findData(ThemeMode.DARK.value))
        self.app.processEvents()
        self.assertEqual(manager.snapshot.variant, ThemeVariant.DARK)
        self.assertEqual(id(store.state), state_identity)
        self.assertEqual(id(service.network), network_identity)
        self.assertEqual(chat.key, key)
        self.assertEqual(chat.compose.toPlainText(), "尚未发送的内容")
        self.assertEqual(scroll_bar.value(), scroll_position)
        self.assertEqual({key: id(value) for key, value in chat._message_rows.items()}, message_widget_ids)

    def test_navigation_selection_and_icons_survive_theme_switch(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.application import Workspace
        from client.v2.theme import ThemeMode

        manager = self._install()
        store = AppStore()
        workspace = Workspace(
            ChatService(NetworkClient("127.0.0.1", 9), store),
            store,
            "127.0.0.1",
            9,
        )
        workspace.nav_buttons[2].click()
        light_icon_key = workspace.nav_buttons[2].icon().cacheKey()

        manager.set_mode(ThemeMode.DARK)
        self.app.processEvents()

        self.assertEqual(workspace.pages.currentIndex(), 2)
        self.assertTrue(workspace.nav_buttons[2].isChecked())
        self.assertNotEqual(workspace.nav_buttons[2].icon().cacheKey(), light_icon_key)
