"""Main window and application entry point."""

from __future__ import annotations

import queue
import sys
from typing import Any
from PySide6.QtCore import QEvent, QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget
from client.models import AppState, ConversationKey
from client.network import NetworkClient
from client.service import ChatService
from client.store import AppStore
from ..icons import brand_app_icon, brand_logo_icon
from ..theme import install_theme_manager
from ..widgets import message_card_kind
from ..pages.auth_page import AuthPage
from .workspace import Workspace


class MainWindow(QMainWindow):
    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.setWindowTitle("Parlor 智能聊天室")
        self.setWindowIcon(brand_app_icon(128))
        self.resize(1280, 820)
        self.setMinimumSize(720, 520)
        self.network = NetworkClient(host, port)
        self.store = AppStore()
        self.service = ChatService(self.network, self.store)
        self.credentials: tuple[str, str] | None = None
        self.was_authenticated = False
        self._read_cursors: dict[ConversationKey, int] = {}
        self._read_requests: dict[str, tuple[ConversationKey, int]] = {}
        self._tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(brand_logo_icon(24), self)
            self._tray.setToolTip("Parlor 智能聊天室")
            self._tray.show()
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.auth = AuthPage(host, port)
        self.workspace = Workspace(self.service, self.store, host, port)
        self.stack.addWidget(self.auth)
        self.stack.addWidget(self.workspace)
        root_layout.addWidget(self.stack)
        self.setCentralWidget(root)
        self.auth.submitted.connect(self._authenticate)
        self.workspace.logout_requested.connect(self._logout)
        self.workspace.notification_test_requested.connect(self._show_test_notification)
        self.workspace.chat.latest_viewed.connect(lambda: self._mark_conversation_read(self.store.state))
        self.workspace.pages.currentChanged.connect(lambda _index: QTimer.singleShot(0, lambda: self._mark_conversation_read(self.store.state)))
        self.store.subscribe(self._on_state)
        self.poll = QTimer(self)
        self.poll.setInterval(50)
        self.poll.timeout.connect(self._poll_events)
        self.poll.start()
        self._conversation_refresh_timer = QTimer(self)
        self._conversation_refresh_timer.setSingleShot(True)
        self._conversation_refresh_timer.setInterval(120)
        self._conversation_refresh_timer.timeout.connect(self._request_conversation_list)
        self.network.start()

    def _authenticate(self, mode: str, username: str, password: str) -> None:
        self.credentials = (username, password)
        if mode == "register":
            self.service.register(username, password)
        else:
            self.service.login(username, password)

    def _logout(self) -> None:
        self.was_authenticated = False
        self.credentials = None
        self.workspace.flush_drafts()
        self._conversation_refresh_timer.stop()
        self._read_cursors.clear()
        self._read_requests.clear()
        self.service.logout()
        self.store.clear_session()
        self.auth.password.clear()
        self.stack.setCurrentWidget(self.auth)

    def _poll_events(self) -> None:
        processed = 0
        while processed < 100:
            try:
                event = self.network.events.get_nowait()
            except queue.Empty:
                break
            request_id = str(event.get("request_id") or "")
            if event.get("type") == "error" and request_id in self._read_requests:
                key, previous = self._read_requests.pop(request_id)
                self._read_cursors[key] = previous
            elif event.get("type") == "ack":
                self._read_requests.pop(request_id, None)
            self.store.handle_event(event)
            if event.get("type") == "message.event":
                self._notify_new_message(event.get("payload"))
            processed += 1

    def _notify_new_message(self, payload: Any) -> None:
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
            return
        message = payload["message"]
        if str(message.get("sender_id") or "") == str(self.store.state.current_user.get("id") or ""):
            return
        if not self.store.state.authenticated or self.isActiveWindow():
            return
        settings = QSettings()
        if settings.value("notifications/dnd", False, type=bool):
            return
        card_kind = message_card_kind(message)
        kind = str(message.get("message_type") or "text")
        if card_kind == "summary":
            preview = "[群聊总结]"
        elif card_kind == "todo":
            preview = "[待办事项]"
        else:
            preview = "[表情包]" if kind == "sticker" else "[图片]" if kind == "image" else "[Agent 回复]" if kind == "agent" else str(message.get("content") or "")
        if self._tray and settings.value("notifications/enabled", False, type=bool):
            self._tray.showMessage(str(message.get("sender_name") or "新消息"), preview[:120], QSystemTrayIcon.MessageIcon.Information, 4000)
        if settings.value("notifications/sound", False, type=bool):
            QApplication.beep()

    def _show_test_notification(self) -> None:
        if self._tray is None:
            return
        self._tray.showMessage(
            "测试通知",
            "如果你看到这条消息，桌面通知已经可用。",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )
        if QSettings().value("notifications/sound", False, type=bool):
            QApplication.beep()

    def _schedule_conversation_refresh(self) -> None:
        if not self.store.state.authenticated:
            return
        if not self._conversation_refresh_timer.isActive():
            self._conversation_refresh_timer.start()

    def _request_conversation_list(self) -> None:
        if self.store.state.authenticated:
            self.service.request("conversation.list")

    def _mark_conversation_read(self, state: AppState) -> None:
        key = state.current_conversation
        if (key is None or not state.authenticated or self.stack.currentWidget() is not self.workspace
                or self.workspace.pages.currentIndex() != 0 or not self.workspace.chat.isVisible()
                or not self.isActiveWindow() or not self.workspace.chat._is_at_bottom()):
            return
        ids = [
            int(item.get("id", item.get("message_id")))
            for item in state.messages.get(key, [])
            if item.get("id", item.get("message_id")) is not None
        ]
        if not ids:
            return
        latest = max(ids)
        if latest <= self._read_cursors.get(key, 0):
            return
        payload = self.service.scope(key)
        payload["message_id"] = latest
        previous = self._read_cursors.get(key, 0)
        request_id = self.service.request("history.read", payload)
        if request_id:
            self._read_cursors[key] = latest
            self._read_requests[request_id] = (key, previous)

    def _on_state(self, topic: str, state: AppState) -> None:
        if topic == "profile" and self.credentials and state.current_user.get("username"):
            self.credentials = (str(state.current_user["username"]), self.credentials[1])
        self.auth.update_state(topic, state)
        self.workspace.update_state(topic, state)
        if topic == "auth" and state.authenticated:
            self.was_authenticated = True
            self.stack.setCurrentWidget(self.workspace)
            self.service.bootstrap_workspace()
            self.service.reconcile_private_sends()
            QTimer.singleShot(0, lambda: self._mark_conversation_read(self.store.state))
            if state.events_has_more:
                self.service.sync_events()
        elif topic == "events" and state.events_has_more:
            self.service.sync_events()
        elif topic == "todos" and state.todo_needs_refresh and state.authenticated:
            state.todo_needs_refresh = False
            self.service.list_todos()
        elif topic == "connection" and state.connected and self.was_authenticated and self.credentials and not state.authenticated:
            self.service.login(*self.credentials)
        elif topic == "connection" and not state.connected:
            self._read_cursors.clear()
            self._read_requests.clear()
        elif topic == "error" and state.last_error:
            self.statusBar().showMessage(state.last_error, 5000)
        if topic == "agent_result" and state.authenticated:
            self.service.request("agent.activity.list", {"limit": 100})
            self._schedule_conversation_refresh()
        if topic == "profile" and state.authenticated:
            self._schedule_conversation_refresh()
        if topic in {"messages", "selection"} and state.authenticated:
            self._schedule_conversation_refresh()
        if topic == "read":
            self._schedule_conversation_refresh()
        if topic == "private_read":
            self._schedule_conversation_refresh()

    def changeEvent(self, event: Any) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and hasattr(self, "workspace"):
            if self.isActiveWindow():
                QTimer.singleShot(0, lambda: self._mark_conversation_read(self.store.state))
            else:
                self.workspace.chat._stop_private_typing()

    def closeEvent(self, event: Any) -> None:
        self._conversation_refresh_timer.stop()
        self.workspace.flush_drafts()
        self.network.close()
        super().closeEvent(event)


def run(host: str, port: int) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("Chatroom")
    app.setApplicationName("Parlor 智能聊天室")
    app.setWindowIcon(brand_app_icon(256))
    install_theme_manager(app)
    window = MainWindow(host, port)
    window.show()
    raise SystemExit(app.exec())
