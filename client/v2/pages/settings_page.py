"""Settings page."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any
from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget
from client.models import AppState
from client.service import ChatService
from client.store import AppStore
from ..theme import ThemeMode, ThemeSnapshot, active_theme_manager
from ..components import PageHeader
from ..widgets import UserAvatar, configure_scroll_surface, configure_surface
from ..ui_constants import MAX_AVATAR_SIZE, MESSAGE_DENSITY_KEY
from .agent_manager import AgentManagerDialog, MemoryDialog


class SettingsPage(QWidget):
    logout_requested = Signal()
    density_changed = Signal(str)
    test_notification_requested = Signal()
    COMPACT_BREAKPOINT = 900

    def __init__(self, service: ChatService, store: AppStore, host: str, port: int) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        self._host = host
        self._port = port
        self._profile_action_pending = False
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(14)
        self.header = PageHeader(
            "设置",
            "管理账号、外观与当前可用的客户端选项。",
            eyebrow="偏好与账户",
        )
        self.compact_nav = QComboBox()
        self.compact_nav.setObjectName("SettingsCompactNav")
        self.compact_nav.hide()
        body = QHBoxLayout()
        body.setSpacing(20)
        sidebar = QFrame()
        sidebar.setObjectName("SettingsSidebar")
        sidebar.setFixedWidth(210)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 4, 0, 4)
        self.settings_nav = QListWidget()
        self.settings_nav.setObjectName("SettingsNav")
        self.settings_nav.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        sidebar_layout.addWidget(self.settings_nav)
        self.settings_sidebar = sidebar
        self.settings_stack = QStackedWidget()
        self.settings_stack.setObjectName("SettingsContent")
        self.settings_stack.setMaximumWidth(760)
        categories = ("账号资料", "外观主题", "聊天偏好", "消息通知", "AI 助手", "连接与诊断")
        for category in categories:
            item = QListWidgetItem(category)
            item.setSizeHint(QSize(0, 44))
            self.settings_nav.addItem(item)
            self.compact_nav.addItem(category)

        account = self._add_settings_page("账号资料", "头像和账号操作会同步到当前服务端账号。")
        self.user = QLabel("未登录")
        self.user.setObjectName("SectionTitle")
        profile_row = QHBoxLayout()
        initial_profile = self.service.profile(
            int(self.store.state.current_user.get("id", 0) or 0), self.store.state.current_user
        )
        self.avatar = UserAvatar(self.service, initial_profile, 58)
        profile_actions = QVBoxLayout()
        self.change_avatar = QPushButton("上传 / 修改头像")
        self.reset_avatar = QPushButton("恢复默认头像")
        self.reset_avatar.setObjectName("Ghost")
        profile_actions.addWidget(self.change_avatar)
        profile_actions.addWidget(self.reset_avatar)
        profile_row.addWidget(self.avatar)
        profile_row.addLayout(profile_actions)
        profile_row.addStretch()
        profile_section, profile_content = self._settings_section("个人资料")
        profile_content.addWidget(self.user)
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("登录时使用的账号")
        self.username_edit.setMaxLength(20)
        self.username_edit.setAccessibleName("账号")
        self.display_name_edit = QLineEdit()
        self.display_name_edit.setPlaceholderText("聊天时显示的昵称")
        self.display_name_edit.setMaxLength(30)
        self.display_name_edit.setAccessibleName("昵称")
        self.save_identity = QPushButton("保存")
        self.save_identity.clicked.connect(self._save_identity)
        profile_content.addWidget(QLabel("账号"))
        profile_content.addWidget(self.username_edit)
        profile_content.addWidget(QLabel("昵称"))
        profile_content.addWidget(self.display_name_edit)
        profile_content.addWidget(self.save_identity, alignment=Qt.AlignmentFlag.AlignLeft)
        profile_content.addLayout(profile_row)
        self.profile_feedback = QLabel("")
        self.profile_feedback.setObjectName("InlineNotice")
        self.profile_feedback.setProperty("noticeKind", "info")
        self.profile_feedback.hide()
        profile_content.addWidget(self.profile_feedback)
        account.addWidget(profile_section)
        account_action, account_action_content = self._settings_section("账户操作", "退出后会返回登录页面，不会删除服务端数据。")
        self.logout = QPushButton("退出登录")
        self.logout.setObjectName("DangerButton")
        self.logout.setMaximumWidth(160)
        account_action_content.addWidget(self.logout, alignment=Qt.AlignmentFlag.AlignLeft)
        account.addWidget(account_action)
        account.addStretch()

        appearance = self._add_settings_page("外观主题", "主题偏好保存在本机，并可随系统外观自动切换。")
        appearance_row = QHBoxLayout()
        appearance_label = QLabel("外观主题")
        self.theme_mode = QComboBox()
        self.theme_mode.addItem("跟随系统", ThemeMode.SYSTEM.value)
        self.theme_mode.addItem("浅色", ThemeMode.LIGHT.value)
        self.theme_mode.addItem("深色", ThemeMode.DARK.value)
        appearance_row.addWidget(appearance_label)
        appearance_row.addStretch()
        appearance_row.addWidget(self.theme_mode)
        appearance_section, appearance_content = self._settings_section("主题")
        appearance_content.addLayout(appearance_row)
        self.theme_feedback = QLabel("")
        self.theme_feedback.setObjectName("InlineNotice")
        self.theme_feedback.setProperty("noticeKind", "success")
        self.theme_feedback.hide()
        appearance_content.addWidget(self.theme_feedback)
        appearance.addWidget(appearance_section)
        appearance.addStretch()
        manager = active_theme_manager()
        if manager:
            index = self.theme_mode.findData(manager.mode.value)
            self.theme_mode.setCurrentIndex(max(0, index))
            manager.themeChanged.connect(self._theme_snapshot_changed)
        else:
            self.theme_mode.setEnabled(False)
        self.theme_mode.currentIndexChanged.connect(self._change_theme)

        chat = self._add_settings_page("聊天偏好", "当前聊天行为由客户端统一定义。")
        chat_section, chat_content = self._settings_section("输入与显示")
        send_row = QFrame()
        send_row.setObjectName("SettingsRow")
        send_layout = QHBoxLayout(send_row)
        send_layout.setContentsMargins(0, 8, 0, 8)
        send_text = QVBoxLayout()
        send_heading = QLabel("发送快捷键")
        send_heading.setObjectName("MemberName")
        self.send_shortcut_hint = QLabel()
        self.send_shortcut_hint.setObjectName("Muted")
        send_text.addWidget(send_heading)
        send_text.addWidget(self.send_shortcut_hint)
        self.send_shortcut = QComboBox()
        self.send_shortcut.setAccessibleName("发送快捷键")
        self.send_shortcut.addItem("Enter 发送", True)
        self.send_shortcut.addItem("Ctrl/Cmd+Enter 发送", False)
        enter_to_send = QSettings().value("chat/enter_to_send", True, type=bool)
        self.send_shortcut.setCurrentIndex(self.send_shortcut.findData(enter_to_send))
        self.send_shortcut_hint.setText("Shift+Enter 换行" if enter_to_send else "Enter 换行")
        self.send_shortcut.currentIndexChanged.connect(self._change_send_shortcut)
        send_layout.addLayout(send_text, 1)
        send_layout.addWidget(self.send_shortcut)
        chat_content.addWidget(send_row)
        density_row = QFrame()
        density_row.setObjectName("SettingsRow")
        density_layout = QHBoxLayout(density_row)
        density_layout.setContentsMargins(0, 8, 0, 8)
        density_text = QVBoxLayout()
        density_heading = QLabel("消息显示密度")
        density_heading.setObjectName("MemberName")
        density_helper = QLabel("紧凑模式会减少消息之间的垂直留白。")
        density_helper.setObjectName("Muted")
        density_helper.setWordWrap(True)
        density_text.addWidget(density_heading)
        density_text.addWidget(density_helper)
        self.message_density = QComboBox()
        self.message_density.addItem("标准", "default")
        self.message_density.addItem("紧凑", "compact")
        current_density = str(QSettings().value(MESSAGE_DENSITY_KEY, "default"))
        density_index = self.message_density.findData(current_density)
        self.message_density.setCurrentIndex(max(0, density_index))
        self.message_density.currentIndexChanged.connect(self._change_density)
        density_layout.addLayout(density_text, 1)
        density_layout.addWidget(self.message_density)
        chat_content.addWidget(density_row)
        chat.addWidget(chat_section)
        chat.addStretch()

        notifications = self._add_settings_page("消息通知", "新消息通知使用系统通知能力；偏好保存在本机。")
        notification_section, notification_content = self._settings_section("桌面通知")
        self.notify_enabled = QCheckBox("后台收到新消息时显示通知")
        self.notify_enabled.setChecked(QSettings().value("notifications/enabled", False, type=bool))
        self.notify_enabled.toggled.connect(lambda enabled: QSettings().setValue("notifications/enabled", enabled))
        self.do_not_disturb = QCheckBox("免打扰（暂停新消息通知）")
        self.do_not_disturb.setChecked(QSettings().value("notifications/dnd", False, type=bool))
        self.do_not_disturb.toggled.connect(lambda enabled: QSettings().setValue("notifications/dnd", enabled))
        self.notify_sound = QCheckBox("新消息声音提醒")
        self.notify_sound.setChecked(QSettings().value("notifications/sound", False, type=bool))
        self.notify_sound.toggled.connect(lambda enabled: QSettings().setValue("notifications/sound", enabled))
        notification_content.addWidget(self.notify_enabled)
        notification_content.addWidget(self.do_not_disturb)
        notification_content.addWidget(self.notify_sound)
        self.test_notification = QPushButton("发送测试通知")
        self.test_notification.clicked.connect(self._request_test_notification)
        notification_content.addWidget(
            self.test_notification, alignment=Qt.AlignmentFlag.AlignLeft
        )
        notification_hint = QLabel(
            "测试通知会立即发送；实际新消息通知仅在应用处于后台且未开启免打扰时显示。"
        )
        notification_hint.setObjectName("Muted")
        notification_hint.setWordWrap(True)
        notification_content.addWidget(notification_hint)
        self.notification_feedback = QLabel("")
        self.notification_feedback.setObjectName("InlineNotice")
        self.notification_feedback.setWordWrap(True)
        self.notification_feedback.hide()
        notification_content.addWidget(self.notification_feedback)
        notifications.addWidget(notification_section)
        notifications.addStretch()

        ai_page = self._add_settings_page(
            "AI 助手",
            "这里管理 Agent 的全局身份；群内启用、工具边界、定时任务和共享记忆请打开群聊的“群设置”。",
        )
        ai_section, ai_content = self._settings_section("Agent 角色库")
        self.agent_setting_status = QLabel("状态未知")
        self.agent_setting_status.setObjectName("AgentBadge")
        self.agent_setting_model = QLabel("模型信息尚未加载")
        self.agent_setting_model.setObjectName("Muted")
        ai_content.addWidget(self.agent_setting_status, alignment=Qt.AlignmentFlag.AlignLeft)
        ai_content.addWidget(self.agent_setting_model)
        ai_actions = QHBoxLayout()
        self.manage_agents = QPushButton("管理 Agent 角色库")
        self.manage_memory = QPushButton("当前会话记忆")
        self.manage_memory.setObjectName("Ghost")
        ai_actions.addWidget(self.manage_agents)
        ai_actions.addWidget(self.manage_memory)
        ai_actions.addStretch()
        ai_content.addLayout(ai_actions)
        self.agent_capability = QLabel("多 Agent 与记忆服务尚未加载")
        self.agent_capability.setObjectName("Muted")
        self.agent_capability.setWordWrap(True)
        ai_content.addWidget(self.agent_capability)
        ai_page.addWidget(ai_section)
        ai_page.addStretch()

        connection_page = self._add_settings_page("连接与诊断", "连接参数来自启动配置，当前页面仅提供安全的只读状态。")
        connection_section, connection_content = self._settings_section("连接")
        self.connection = QLabel(self.store.state.connection_text)
        self.connection.setObjectName("SectionTitle")
        self.endpoint = QLabel(f"服务器 {host}:{port}")
        self.endpoint.setObjectName("Muted")
        connection_content.addWidget(self.connection)
        connection_content.addWidget(self.endpoint)
        connection_page.addWidget(connection_section)
        diagnostics_section, diagnostics_content = self._settings_section("诊断与隐私")
        privacy = QLabel("诊断信息不会包含密码、密钥、图片数据或完整消息正文。")
        privacy.setObjectName("Muted")
        privacy.setWordWrap(True)
        diagnostics_content.addWidget(privacy)
        export_row = QFrame()
        export_row.setObjectName("SettingsRow")
        export_layout = QHBoxLayout(export_row)
        export_layout.setContentsMargins(0, 8, 0, 8)
        export_text = QVBoxLayout()
        export_heading = QLabel("导出诊断报告")
        export_heading.setObjectName("MemberName")
        export_helper = QLabel("仅包含连接状态、计数与本机偏好；不包含密码、密钥、图片或消息正文。")
        export_helper.setObjectName("Muted")
        export_helper.setWordWrap(True)
        export_text.addWidget(export_heading)
        export_text.addWidget(export_helper)
        self.export_diagnostics = QPushButton("导出诊断报告")
        self.export_diagnostics.clicked.connect(self._export_diagnostics)
        export_layout.addLayout(export_text, 1)
        export_layout.addWidget(self.export_diagnostics)
        diagnostics_content.addWidget(export_row)
        self.diagnostics_feedback = QLabel("")
        self.diagnostics_feedback.setObjectName("InlineNotice")
        self.diagnostics_feedback.setWordWrap(True)
        self.diagnostics_feedback.hide()
        diagnostics_content.addWidget(self.diagnostics_feedback)
        connection_page.addWidget(diagnostics_section)
        connection_page.addStretch()

        body.addWidget(sidebar)
        body.addWidget(self.settings_stack, 1)
        self.settings_gutter = QWidget()
        configure_surface(self.settings_gutter, "canvas")
        body.addWidget(self.settings_gutter)
        root.addWidget(self.header)
        root.addWidget(self.compact_nav)
        root.addLayout(body, 1)
        self.settings_nav.setCurrentRow(0)
        self.settings_nav.currentRowChanged.connect(self._select_section)
        self.compact_nav.currentIndexChanged.connect(self._select_section)
        self.logout.clicked.connect(self.logout_requested)
        self.change_avatar.clicked.connect(self._change_avatar)
        self.reset_avatar.clicked.connect(self._reset_avatar)
        self.manage_agents.clicked.connect(self._open_agent_manager)
        self.manage_memory.clicked.connect(self._open_memory_manager)

    def _add_settings_page(self, title: str, description: str) -> QVBoxLayout:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        helper = QLabel(description)
        helper.setObjectName("Muted")
        helper.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(helper)
        configure_scroll_surface(scroll, content, "canvas")
        scroll.setWidget(content)
        self.settings_stack.addWidget(scroll)
        return layout

    @staticmethod
    def _settings_section(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
        section = QFrame()
        section.setObjectName("SettingsSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 4, 0, 16)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        if description:
            helper = QLabel(description)
            helper.setObjectName("Muted")
            helper.setWordWrap(True)
            layout.addWidget(helper)
        return section, layout

    def _change_send_shortcut(self, _index: int) -> None:
        enter_to_send = bool(self.send_shortcut.currentData())
        QSettings().setValue("chat/enter_to_send", enter_to_send)
        self.send_shortcut_hint.setText(
            "Shift+Enter 换行" if enter_to_send else "Enter 换行"
        )

    def _select_section(self, index: int) -> None:
        if index < 0 or index >= self.settings_stack.count():
            return
        self.settings_stack.setCurrentIndex(index)
        if self.settings_nav.currentRow() != index:
            self.settings_nav.blockSignals(True)
            self.settings_nav.setCurrentRow(index)
            self.settings_nav.blockSignals(False)
        if self.compact_nav.currentIndex() != index:
            self.compact_nav.blockSignals(True)
            self.compact_nav.setCurrentIndex(index)
            self.compact_nav.blockSignals(False)

    def _change_theme(self, _index: int) -> None:
        manager = active_theme_manager()
        value = self.theme_mode.currentData()
        if manager and value:
            manager.set_mode(str(value))
            self.theme_feedback.setText("主题偏好已保存")
            self.theme_feedback.show()

    def _change_density(self, _index: int) -> None:
        value = str(self.message_density.currentData() or "default")
        QSettings().setValue(MESSAGE_DENSITY_KEY, value)
        self.density_changed.emit(value)

    @staticmethod
    def _set_feedback(label: QLabel, text: str, kind: str) -> None:
        label.setText(text)
        label.setProperty("noticeKind", kind)
        label.style().unpolish(label)
        label.style().polish(label)
        label.setVisible(bool(text))

    def _export_diagnostics(self) -> None:
        default_name = f"chatroom-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path, _selected = QFileDialog.getSaveFileName(
            self, "导出诊断报告", default_name, "JSON 文件 (*.json)"
        )
        if not path:
            return
        state = self.store.state
        manager = active_theme_manager()
        settings = QSettings()
        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "platform": sys.platform,
            "connection": {
                "server": f"{self._host}:{self._port}",
                "connected": bool(state.connected),
                "status": state.connection_text,
            },
            "account": {
                "id": state.current_user.get("id"),
                "role": str(state.current_user.get("role") or ""),
            },
            "agent": {
                "online": bool(state.agent_online),
                "model": state.agent_model,
            },
            "counts": {
                "conversations": len(state.conversations),
                "cached_messages": sum(len(records) for records in state.messages.values()),
                "activities": len(state.activities),
            },
            "preferences": {
                "theme": manager.mode.value if manager is not None else "system",
                "message_density": str(settings.value(MESSAGE_DENSITY_KEY, "default")),
                "enter_to_send": bool(settings.value("chat/enter_to_send", True, type=bool)),
                "notifications_enabled": bool(
                    settings.value("notifications/enabled", False, type=bool)
                ),
                "notifications_dnd": bool(
                    settings.value("notifications/dnd", False, type=bool)
                ),
                "notifications_sound": bool(
                    settings.value("notifications/sound", False, type=bool)
                ),
            },
            "last_error_present": bool(state.last_error),
        }
        try:
            Path(path).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self._set_feedback(self.diagnostics_feedback, f"导出失败：{exc}", "error")
            return
        self._set_feedback(
            self.diagnostics_feedback, f"诊断报告已保存到 {path}", "success"
        )

    def _request_test_notification(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._set_feedback(
                self.notification_feedback,
                "当前系统托盘不可用，无法发送桌面通知。",
                "error",
            )
            return
        self._set_feedback(
            self.notification_feedback,
            "已发送测试通知；若未看到，请检查系统通知权限。",
            "success",
        )
        self.test_notification_requested.emit()

    def _sync_theme_mode(self, mode: ThemeMode) -> None:
        index = self.theme_mode.findData(mode.value)
        if index >= 0 and index != self.theme_mode.currentIndex():
            self.theme_mode.blockSignals(True)
            self.theme_mode.setCurrentIndex(index)
            self.theme_mode.blockSignals(False)

    def _theme_snapshot_changed(self, snapshot: ThemeSnapshot) -> None:
        self._sync_theme_mode(snapshot.mode)

    def update_state(self, topic: str, state: AppState) -> None:
        if topic in {"auth", "connection", "session", "profile", "avatar", "agent", "agents", "memories", "tools", "error", "ack"}:
            role = "管理员" if state.current_user.get("role") == "admin" else "普通用户"
            self.user.setText(f"{state.current_user.get('display_name') or state.current_user.get('username', '未登录')} · {role}")
            if topic in {"auth", "profile", "session"}:
                if not self.username_edit.hasFocus():
                    self.username_edit.setText(str(state.current_user.get("username", "")))
                if not self.display_name_edit.hasFocus():
                    self.display_name_edit.setText(str(state.current_user.get("display_name") or state.current_user.get("username", "")))
            self.connection.setText(state.connection_text)
            user_id = int(state.current_user.get("id", 0) or 0)
            profile = self.service.profile(user_id, state.current_user)
            self.avatar.refresh(profile)
            self.agent_setting_status.setText("Agent 在线" if state.agent_online else "Agent 离线")
            self.agent_setting_status.setProperty("online", state.agent_online)
            self.agent_setting_status.style().unpolish(self.agent_setting_status)
            self.agent_setting_status.style().polish(self.agent_setting_status)
            self.agent_setting_model.setText(f"当前模型：{state.agent_model}" if state.agent_model else "模型信息尚未加载")
            enabled_agents = sum(1 for item in state.agents if item.get("enabled", True))
            memory_scopes = sum(1 for value in state.memory_settings.values() if value)
            self.agent_capability.setText(
                f"已启用 {enabled_agents} 个 Agent，{len(state.tool_definitions)} 个工具定义；"
                f"当前客户端已加载 {memory_scopes} 个已开启的记忆范围。"
            )
        if topic == "avatar":
            self._profile_action_pending = False
            self.profile_feedback.setText("头像已更新")
            self.profile_feedback.setProperty("noticeKind", "success")
            self.profile_feedback.style().unpolish(self.profile_feedback)
            self.profile_feedback.style().polish(self.profile_feedback)
            self.profile_feedback.show()
        elif topic == "ack" and self._profile_action_pending:
            self.profile_feedback.setText("资料已提交")
            self.profile_feedback.setProperty("noticeKind", "success")
            self.profile_feedback.style().unpolish(self.profile_feedback)
            self.profile_feedback.style().polish(self.profile_feedback)
            self.profile_feedback.show()
            self._profile_action_pending = False
        elif topic == "error" and self._profile_action_pending and state.last_error:
            self.profile_feedback.setText(f"操作失败：{state.last_error}")
            self.profile_feedback.setProperty("noticeKind", "error")
            self.profile_feedback.style().unpolish(self.profile_feedback)
            self.profile_feedback.style().polish(self.profile_feedback)
            self.profile_feedback.show()
            self._profile_action_pending = False

    def _open_agent_manager(self) -> None:
        dialog = AgentManagerDialog(self.service, self.store, self)
        dialog.exec()

    def _open_memory_manager(self) -> None:
        if not self.store.state.current_conversation:
            QMessageBox.information(self, "尚未选择会话", "请先在聊天页选择一个群聊或私聊，再管理该会话的记忆。")
            return
        dialog = MemoryDialog(self.service, self.store, self)
        dialog.exec()

    def _change_avatar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择头像", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp)")
        if not path:
            return
        size = os.path.getsize(path)
        if size > MAX_AVATAR_SIZE:
            QMessageBox.warning(self, "头像过大", "头像不能超过 2 MiB")
            return
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        raw = Path(path).read_bytes()
        self._save_avatar(mime, base64.b64encode(raw).decode("ascii"))

    def _save_identity(self) -> None:
        username = self.username_edit.text().strip()
        display_name = self.display_name_edit.text().strip()
        if not 3 <= len(username) <= 20 or not 1 <= len(display_name) <= 30:
            self.profile_feedback.setText("账号需为 3–20 字符，昵称需为 1–30 字符")
            self.profile_feedback.show()
            return
        self._profile_action_pending = True
        self.profile_feedback.setText("正在保存账号和昵称…")
        self.profile_feedback.show()
        if not self.service.set_identity(username, display_name):
            self._profile_action_pending = False
            self.profile_feedback.setText(self.store.state.last_error)

    def _reset_avatar(self) -> None:
        self._save_avatar(None, "")

    def _save_avatar(self, mime: str | None, encoded: str) -> None:
        self._profile_action_pending = True
        self.profile_feedback.setText("正在保存头像…")
        self.profile_feedback.setProperty("noticeKind", "info")
        self.profile_feedback.show()
        if not self.service.set_avatar(mime, encoded):
            self._profile_action_pending = False
            if self.store.state.last_error:
                self.profile_feedback.setText(f"操作失败：{self.store.state.last_error}")
                self.profile_feedback.setProperty("noticeKind", "error")

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        compact = self.width() < self.COMPACT_BREAKPOINT
        self.settings_sidebar.setVisible(not compact)
        self.compact_nav.setVisible(compact)
        self.settings_gutter.setVisible(not compact)
        self.settings_stack.setMaximumWidth(16_777_215 if compact else 760)
