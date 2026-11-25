"""Agent management dialogs."""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget
from client.models import AppState, ConversationKey
from client.service import ChatService
from client.store import AppStore
from ..widgets import UserAvatar, avatar_pixmap, configure_scroll_surface
from ..ui_constants import MAX_AVATAR_SIZE


class AgentManagerDialog(QDialog):
    def __init__(
        self,
        service: ChatService,
        store: AppStore,
        parent: QWidget | None = None,
        *,
        group_id: str = "",
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.store = store
        self.current_id = ""
        self._global_tools_read_only = False
        self.context_group_id = str(group_id or "")
        self.setWindowTitle("Agent 管理")
        self.resize(820, 620)
        self._unsubscribe = store.subscribe(self._store_changed)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        left = QVBoxLayout()
        heading = QLabel("Agent")
        heading.setObjectName("SectionTitle")
        self.agent_list = QListWidget()
        self.agent_list.setMinimumWidth(220)
        self.agent_list.setIconSize(QSize(28, 28))
        self.new_button = QPushButton("新建 Agent")
        self.new_button.setObjectName("Primary")
        left.addWidget(heading)
        left.addWidget(self.agent_list, 1)
        left.addWidget(self.new_button)
        root.addLayout(left)

        editor = QScrollArea()
        editor.setWidgetResizable(True)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(10, 4, 10, 10)
        title_row = QHBoxLayout()
        self.avatar_preview = UserAvatar(
            self.service,
            self.service.profile(None, {"id": "", "name": "新 Agent", "kind": "agent"}),
            48,
        )
        self.editor_title = QLabel("新建 Agent")
        self.editor_title.setObjectName("PageTitle")
        title_row.addWidget(self.avatar_preview)
        title_row.addWidget(self.editor_title, 1)
        form.addLayout(title_row)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：项目分析师")
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("一句话介绍这个 Agent")
        self.model_combo = QComboBox()
        self.enabled_check = QCheckBox("启用")
        self.memory_check = QCheckBox("允许长期记忆（仍需在会话内明确开启）")
        self.auto_sticker_check = QCheckBox("允许自动发送表情包")
        form.addWidget(QLabel("名称"))
        form.addWidget(self.name_edit)
        form.addWidget(QLabel("简介"))
        form.addWidget(self.description_edit)
        form.addWidget(QLabel("模型配置"))
        form.addWidget(self.model_combo)
        form.addWidget(self.enabled_check)
        form.addWidget(self.memory_check)
        form.addWidget(self.auto_sticker_check)

        card_title = QLabel("角色卡")
        card_title.setObjectName("SectionTitle")
        form.addWidget(card_title)
        self.card_editors: dict[str, QTextEdit] = {}
        for field, label, placeholder in (
            ("personality", "性格", "例如：冷静、直接、先澄清事实"),
            ("background", "背景", "例如：长期参与交付项目"),
            ("language_style", "语言风格", "例如：先结论后依据，使用短句"),
            ("behavior_rules", "行为规则", "描述表达与工作偏好；不能改变服务端工具权限"),
        ):
            edit = QTextEdit()
            edit.setPlaceholderText(placeholder)
            edit.setMaximumHeight(78)
            self.card_editors[field] = edit
            form.addWidget(QLabel(label))
            form.addWidget(edit)

        self.tool_title = QLabel("Agent 全局工具权限")
        self.tool_title.setObjectName("SectionTitle")
        form.addWidget(self.tool_title)
        self.tool_list = QListWidget()
        self.tool_list.setMaximumHeight(160)
        form.addWidget(self.tool_list)
        self.save_tools = QPushButton("保存工具权限")
        form.addWidget(self.save_tools, alignment=Qt.AlignmentFlag.AlignLeft)
        self.global_tool_help = QLabel("仅 Agent 所有者或管理员可修改全局权限；群主可在下方管理本群限制。")
        self.global_tool_help.setObjectName("Muted")
        self.global_tool_help.setWordWrap(True)
        form.addWidget(self.global_tool_help)

        group_title = QLabel("群组关联")
        group_title.setObjectName("SectionTitle")
        form.addWidget(group_title)
        group_row = QHBoxLayout()
        self.group_combo = QComboBox()
        self.add_group = QPushButton("加入群组")
        self.remove_group = QPushButton("从群组移除")
        self.remove_group.setObjectName("Ghost")
        group_row.addWidget(self.group_combo, 1)
        group_row.addWidget(self.add_group)
        group_row.addWidget(self.remove_group)
        form.addLayout(group_row)
        group_sticker_row = QHBoxLayout()
        self.group_sticker_check = QCheckBox("在选中群组自动发送表情包")
        self.group_sticker_button = QPushButton("保存群组设置")
        self.group_sticker_button.setObjectName("Ghost")
        group_sticker_row.addWidget(self.group_sticker_check)
        group_sticker_row.addWidget(self.group_sticker_button)
        form.addLayout(group_sticker_row)
        self.group_tool_title = QLabel("群内工具权限")
        self.group_tool_title.setObjectName("SectionTitle")
        form.addWidget(self.group_tool_title)
        self.group_tool_list = QListWidget()
        self.group_tool_list.setMaximumHeight(160)
        form.addWidget(self.group_tool_list)
        self.save_group_tools = QPushButton("保存群内工具权限")
        form.addWidget(self.save_group_tools, alignment=Qt.AlignmentFlag.AlignLeft)
        group_help = QLabel("群主可保存本群工具开关；全局未授权的工具仍需 Agent 所有者或管理员先开启。")
        group_help.setObjectName("Muted")
        group_help.setWordWrap(True)
        form.addWidget(group_help)

        self.notice = QLabel("")
        self.notice.setObjectName("InlineNotice")
        self.notice.setProperty("noticeKind", "info")
        self.notice.hide()
        form.addWidget(self.notice)
        actions = QHBoxLayout()
        self.avatar_button = QPushButton("上传头像")
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("Primary")
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerButton")
        actions.addWidget(self.avatar_button)
        actions.addStretch()
        actions.addWidget(self.delete_button)
        actions.addWidget(self.save_button)
        form.addLayout(actions)
        form.addStretch()
        configure_scroll_surface(editor, content, "surface")
        editor.setWidget(content)
        self.editor_scroll = editor
        root.addWidget(editor, 1)

        self.agent_list.currentItemChanged.connect(self._selection_changed)
        self.new_button.clicked.connect(self._start_new)
        self.save_button.clicked.connect(self._save)
        self.delete_button.clicked.connect(self._delete)
        self.avatar_button.clicked.connect(self._upload_avatar)
        self.save_tools.clicked.connect(self._save_tools)
        self.add_group.clicked.connect(lambda: self._change_group(True))
        self.remove_group.clicked.connect(lambda: self._change_group(False))
        self.group_combo.currentIndexChanged.connect(self._refresh_group_sticker)
        self.group_sticker_button.clicked.connect(self._save_group_sticker)
        self.save_group_tools.clicked.connect(self._save_group_tools)
        self.service.request("agent.list")
        self.service.request("tool.list")
        self._start_new()
        self._render_agents()

    def closeEvent(self, event: Any) -> None:
        self._unsubscribe()
        super().closeEvent(event)

    def _store_changed(self, topic: str, _state: AppState) -> None:
        if topic in {"agents", "auth", "avatar"}:
            self._render_agents()
            self._refresh_group_sticker()
            if self.current_id:
                self._request_tools(self.current_id)
                agent = next((item for item in self.store.state.agents if str(item.get("id")) == self.current_id), None)
                if agent:
                    self.avatar_preview.refresh(self.service.profile(None, agent))
        if topic == "ack" and self.store.state.last_error == "":
            self._notice("配置已提交", "success")

    def _notice(self, message: str, kind: str = "info") -> None:
        self.notice.setText(message)
        self.notice.setProperty("noticeKind", kind)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        self.notice.setVisible(bool(message))

    def _render_agents(self) -> None:
        selected = self.current_id
        self.agent_list.blockSignals(True)
        self.agent_list.clear()
        for agent in self.store.state.agents:
            label = str(agent.get("name") or "未命名 Agent")
            suffix = "" if agent.get("enabled", True) else " · 已停用"
            if agent.get("builtin"):
                suffix += " · 预设"
            item = QListWidgetItem(f"{label}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, str(agent.get("id", "")))
            item.setIcon(self._agent_icon(agent))
            self.agent_list.addItem(item)
            if str(agent.get("id", "")) == selected:
                self.agent_list.setCurrentItem(item)
        self.agent_list.blockSignals(False)

    def _agent_icon(self, agent: dict[str, Any]) -> QIcon:
        profile = self.service.profile(None, agent)
        data = self.service.avatar_data(profile)
        return QIcon(avatar_pixmap(profile.display_name, 28, data, agent=True))

    def _selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        agent_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        agent = next((item for item in self.store.state.agents if str(item.get("id")) == agent_id), None)
        if agent:
            self._load_agent(agent)

    def _start_new(self) -> None:
        self.current_id = ""
        self.agent_list.setCurrentRow(-1)
        self.editor_title.setText("新建 Agent")
        self.avatar_preview.refresh(self.service.profile(None, {"id": "", "name": "新 Agent", "kind": "agent"}))
        self.name_edit.clear()
        self.description_edit.clear()
        for editor in self.card_editors.values():
            editor.clear()
        self.enabled_check.setChecked(True)
        self.memory_check.setChecked(True)
        self.auto_sticker_check.setChecked(True)
        self.model_combo.clear()
        self.model_combo.addItem("使用默认模型", None)
        for model in self.store.state.agent_models:
            label = f"{model.get('name')} · {model.get('model')}"
            if model.get("is_default"):
                label += "（默认）"
            self.model_combo.addItem(label, model.get("id"))
        self.tool_list.clear()
        for tool in self.store.state.tool_definitions:
            permission = "写入" if tool.get("permission") == "write" else "查询"
            mode = "需确认" if tool.get("mode") == "confirm" else "自动"
            item = QListWidgetItem(f"{tool.get('label')}  ·  {permission} · {mode}")
            item.setData(Qt.ItemDataRole.UserRole, str(tool.get("name")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.tool_list.addItem(item)
        self.delete_button.setEnabled(False)
        self.avatar_button.setEnabled(False)
        self.save_tools.setEnabled(False)
        self.tool_list.setEnabled(False)
        self.save_button.setEnabled(True)
        self._fill_groups()
        self._refresh_group_tools()

    def _load_agent(self, agent: dict[str, Any]) -> None:
        self.current_id = str(agent.get("id") or "")
        self.editor_title.setText(f"编辑 {agent.get('name') or 'Agent'}")
        self.avatar_preview.refresh(self.service.profile(None, agent))
        self.name_edit.setText(str(agent.get("name") or ""))
        self.description_edit.setText(str(agent.get("description") or ""))
        card = agent.get("card") if isinstance(agent.get("card"), dict) else {}
        for field, editor in self.card_editors.items():
            editor.setPlainText(str(card.get(field) or ""))
        self.enabled_check.setChecked(bool(agent.get("enabled", True)))
        self.memory_check.setChecked(bool(agent.get("memory_enabled", True)))
        self.auto_sticker_check.setChecked(bool(agent.get("auto_sticker", True)))
        self.model_combo.clear()
        self.model_combo.addItem("使用默认模型", None)
        for model in self.store.state.agent_models:
            label = f"{model.get('name')} · {model.get('model')}"
            if model.get("is_default"):
                label += "（默认）"
            self.model_combo.addItem(label, model.get("id"))
        index = self.model_combo.findData(agent.get("model_profile_id"))
        self.model_combo.setCurrentIndex(max(0, index))
        self.delete_button.setEnabled(self.current_id != "agent" and bool(agent.get("can_manage")))
        self.avatar_button.setEnabled(bool(agent.get("can_manage")))
        can_manage = bool(agent.get("can_manage"))
        self._global_tools_read_only = not can_manage
        self.save_tools.setEnabled(can_manage)
        self.tool_list.setEnabled(True)
        self.tool_list.setVisible(can_manage or not self.context_group_id)
        self.save_button.setEnabled(can_manage)
        self._set_global_tool_mode(can_manage)
        self._fill_groups()
        self._request_tools(self.current_id)
        if not can_manage and self._can_manage_selected_group():
            QTimer.singleShot(0, self._focus_group_tools)

    def _set_global_tool_mode(self, can_manage: bool) -> None:
        self.tool_title.setText("Agent 全局工具权限" if can_manage else "Agent 全局工具权限（只读）")
        self.save_tools.setText("保存工具权限" if can_manage else "全局权限由管理员管理")
        if can_manage:
            self.global_tool_help.setText(
                "保存后影响该 Agent 在所有群组和私聊中的工具授权；群主仍可在下方收紧本群权限。"
            )
        else:
            self.global_tool_help.setText(
                "当前账号不能修改全局权限。群主请使用下方「群内工具权限」保存本群开关；"
                "若工具标有「Agent 未授权」，需要 Agent 所有者或管理员先开启全局权限。"
            )

    def _can_manage_selected_group(self) -> bool:
        group_id = str(self.group_combo.currentData() or "")
        group = next((item for item in self.store.state.groups if str(item.get("id")) == group_id), {})
        user = self.store.state.current_user
        return bool(group_id and (
            user.get("role") == "admin"
            or (user.get("id") is not None and str(group.get("owner_id")) == str(user["id"]))
        ))

    def _focus_group_tools(self) -> None:
        if self._can_manage_selected_group():
            self.editor_scroll.ensureWidgetVisible(self.save_group_tools, 0, 24)

    def _request_tools(self, agent_id: str) -> None:
        tools = self.store.state.agent_tools.get(agent_id)
        if tools is None:
            self.service.request("agent.tool.list", {"agent_id": agent_id})
            return
        self.tool_list.clear()
        for tool in tools:
            permission = "写入" if tool.get("permission") == "write" else "查询"
            mode = "需确认" if tool.get("mode") == "confirm" else "自动"
            item = QListWidgetItem(
                f"{tool.get('label')}  ·  {permission} · {mode}"
            )
            item.setData(Qt.ItemDataRole.UserRole, str(tool.get("name")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if tool.get("enabled", True) else Qt.CheckState.Unchecked
            )
            if self._global_tools_read_only:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.tool_list.addItem(item)

    def _fill_groups(self) -> None:
        current = self.group_combo.currentData()
        self.group_combo.clear()
        for group in self.store.state.groups:
            if group.get("joined") or group.get("name") == "大厅":
                self.group_combo.addItem(str(group.get("name")), str(group.get("id")))
        index = self.group_combo.findData(current)
        if index >= 0:
            self.group_combo.setCurrentIndex(index)
        elif self.context_group_id:
            index = self.group_combo.findData(self.context_group_id)
            if index >= 0:
                self.group_combo.setCurrentIndex(index)
                self.add_group.setText("加入当前群")
        self._refresh_group_sticker()

    def _refresh_group_sticker(self) -> None:
        group_id = str(self.group_combo.currentData() or "")
        selected = next((item for item in self.store.state.group_agents.get(group_id, []) if str(item.get("id")) == self.current_id), None)
        group = next((item for item in self.store.state.groups if str(item.get("id")) == group_id), {})
        current_user_id = self.store.state.current_user.get("id")
        can_manage = self.store.state.current_user.get("role") == "admin" or (current_user_id is not None and str(group.get("owner_id")) == str(current_user_id))
        self.group_sticker_check.setEnabled(bool(selected and can_manage))
        self.group_sticker_button.setEnabled(bool(selected and can_manage))
        self.group_sticker_check.setChecked(bool(selected and selected.get("group_auto_sticker", True)))
        if group_id and group_id not in self.store.state.group_agents:
            self.service.request("agent.group.list", {"group_id": group_id})
        self._refresh_group_tools()

    def _refresh_group_tools(self) -> None:
        group_id = str(self.group_combo.currentData() or "")
        group = next((item for item in self.store.state.groups if str(item.get("id")) == group_id), {})
        user = self.store.state.current_user
        can_manage = bool(group_id and self.current_id and (
            user.get("role") == "admin"
            or (user.get("id") is not None and str(group.get("owner_id")) == str(user["id"]))
        ))
        self.group_tool_list.clear()
        self.group_tool_list.setEnabled(can_manage)
        self.save_group_tools.setEnabled(False)
        if not can_manage:
            return
        tools = self.store.state.group_agent_tools.get((group_id, self.current_id))
        if tools is None:
            self.service.request("agent.group.tools.list", {"group_id": group_id, "agent_id": self.current_id})
            return
        for tool in tools:
            permission = "写入" if tool.get("permission") == "write" else "查询"
            mode = "需确认" if tool.get("mode") == "confirm" else "自动"
            unavailable = not tool.get("enabled", False)
            label = f"{tool.get('label')}  ·  {permission} · {mode}"
            if unavailable:
                label += " · Agent 未授权"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(tool.get("name")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if tool.get("group_enabled", True) and not unavailable
                else Qt.CheckState.Unchecked
            )
            if unavailable:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setToolTip("需要 Agent 所有者或管理员先开启全局权限")
            self.group_tool_list.addItem(item)
        self.save_group_tools.setEnabled(True)

    def _save_group_sticker(self) -> None:
        group_id = str(self.group_combo.currentData() or "")
        if group_id and self.current_id:
            self.service.set_group_auto_sticker(group_id, self.current_id, self.group_sticker_check.isChecked())
            self._notice("正在更新群组自动表情包设置…", "info")

    def _payload(self) -> dict[str, Any]:
        card = {field: editor.toPlainText().strip() for field, editor in self.card_editors.items()}
        return {
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.text().strip(),
            "card": card,
            "model_profile_id": self.model_combo.currentData(),
            "enabled": self.enabled_check.isChecked(),
            "memory_enabled": self.memory_check.isChecked(),
            "auto_sticker": self.auto_sticker_check.isChecked(),
        }


    def _save(self) -> None:
        payload = self._payload()
        if not payload["name"]:
            self._notice("请填写 Agent 名称", "error")
            return
        if self.current_id:
            request_id = self.service.update_agent(self.current_id, **payload)
        else:
            if self.context_group_id:
                payload["group_id"] = self.context_group_id
            request_id = self.service.create_agent(**payload)
        if not request_id:
            self._notice(self.store.state.last_error or "提交失败", "error")
            return
        self._notice("正在保存…", "info")

    def _delete(self) -> None:
        if not self.current_id:
            return
        if QMessageBox.question(self, "删除 Agent", "删除后该 Agent 的群组关联和记忆也会移除，继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.service.delete_agent(self.current_id)
        self._start_new()

    def _upload_avatar(self) -> None:
        if not self.current_id:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 Agent 头像", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp)")
        if not path:
            return
        if os.path.getsize(path) > MAX_AVATAR_SIZE:
            self._notice("头像不能超过 2 MiB", "error")
            return
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        self.service.set_agent_avatar(self.current_id, mime, encoded)
        self._notice("正在上传头像…", "info")

    def _save_tools(self) -> None:
        if not self.current_id:
            return
        names = [
            str(self.tool_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.tool_list.count())
            if self.tool_list.item(index).checkState() == Qt.CheckState.Checked
        ]
        self.service.set_agent_tools(self.current_id, names)
        self._notice("正在更新工具权限…", "info")

    def _save_group_tools(self) -> None:
        group_id = str(self.group_combo.currentData() or "")
        if not group_id or not self.current_id or not self.save_group_tools.isEnabled():
            return
        names = [
            str(self.group_tool_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.group_tool_list.count())
            if self.group_tool_list.item(index).checkState() == Qt.CheckState.Checked
        ]
        self.service.set_group_agent_tools(group_id, self.current_id, names)
        self._notice("正在更新群内工具权限…", "info")

    def _change_group(self, enabled: bool) -> None:
        if not self.current_id or not self.group_combo.currentData():
            return
        self.service.set_group_agent(
            str(self.group_combo.currentData()), self.current_id, enabled
        )
        self._notice("正在更新群组关联…", "info")


class MemoryDialog(QDialog):
    def __init__(
        self,
        service: ChatService,
        store: AppStore,
        parent: QWidget | None = None,
        *,
        preferred_agent_id: str = "",
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.store = store
        self.preferred_agent_id = str(preferred_agent_id or "")
        self.setWindowTitle("Agent 记忆")
        self.resize(620, 520)
        self._unsubscribe = store.subscribe(self._store_changed)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        title = QLabel("当前会话的 Agent 记忆")
        title.setObjectName("PageTitle")
        self.scope_label = QLabel("")
        self.scope_label.setObjectName("Muted")
        root.addWidget(title)
        root.addWidget(self.scope_label)
        agent_row = QHBoxLayout()
        self.agent_combo = QComboBox()
        agent_row.addWidget(QLabel("Agent"))
        agent_row.addWidget(self.agent_combo, 1)
        root.addLayout(agent_row)
        self.enabled_check = QCheckBox("允许该 Agent 在本会话使用长期记忆")
        root.addWidget(self.enabled_check)
        self.memory_list = QListWidget()
        root.addWidget(self.memory_list, 1)
        self.notice = QLabel("")
        self.notice.setObjectName("InlineNotice")
        self.notice.setProperty("noticeKind", "info")
        self.notice.hide()
        root.addWidget(self.notice)
        actions = QHBoxLayout()
        self.add_button = QPushButton("新增记忆")
        self.edit_button = QPushButton("编辑")
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerButton")
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("Ghost")
        actions.addWidget(self.add_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        actions.addWidget(self.refresh_button)
        root.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        root.addWidget(close)
        self.agent_combo.currentIndexChanged.connect(self._reload)
        self.enabled_check.clicked.connect(self._toggle_memory)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.refresh_button.clicked.connect(self._reload)
        self._refresh_agents()

    def closeEvent(self, event: Any) -> None:
        self._unsubscribe()
        super().closeEvent(event)

    def _store_changed(self, topic: str, _state: AppState) -> None:
        if topic == "agents":
            self._refresh_agents()
        if topic == "memories":
            self._render_memories()

    def _key(self) -> ConversationKey | None:
        key = self.store.state.current_conversation
        return key if key and key.kind in {"group", "private"} else None

    def _agent_id(self) -> str:
        return str(self.agent_combo.currentData() or "agent")

    def _refresh_agents(self) -> None:
        current = self.agent_combo.currentData()
        self.agent_combo.blockSignals(True)
        self.agent_combo.clear()
        key = self._key()
        available: set[str] | None = None
        if key and key.kind == "group":
            members = self.store.state.members_by_group.get(key.identifier, [])
            if members:
                available = {
                    str(member.get("id"))
                    for member in members
                    if member.get("kind") == "agent"
                }
        for agent in self.store.state.agents:
            if agent.get("enabled", True) and (available is None or str(agent.get("id")) in available):
                self.agent_combo.addItem(str(agent.get("name") or "Agent"), str(agent.get("id")))
        index = self.agent_combo.findData(current)
        if index < 0 and self.preferred_agent_id:
            index = self.agent_combo.findData(self.preferred_agent_id)
        if index >= 0:
            self.agent_combo.setCurrentIndex(index)
        self.agent_combo.blockSignals(False)
        self._reload()

    def _reload(self) -> None:
        key = self._key()
        if not key:
            self.scope_label.setText("请先在聊天页选择一个会话。")
            self.memory_list.clear()
            self.enabled_check.setEnabled(False)
            return
        self.scope_label.setText(
            f"{'群聊共享记忆' if key.kind == 'group' else '个人私聊记忆'} · {key.identifier}"
        )
        self.enabled_check.setEnabled(True)
        self.service.load_memories(key, self._agent_id())
        self._render_memories()

    def _render_memories(self) -> None:
        key = self._key()
        if not key:
            return
        storage_key = (self._agent_id(), key.kind, key.identifier)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(bool(self.store.state.memory_settings.get(storage_key, False)))
        self.enabled_check.blockSignals(False)
        self.memory_list.clear()
        for memory in self.store.state.memories.get(storage_key, []):
            preview = str(memory.get("content") or "").replace("\n", " ")[:100]
            kind = str(memory.get("kind") or "note")
            item = QListWidgetItem(f"[{kind}] {preview}")
            item.setData(Qt.ItemDataRole.UserRole, int(memory.get("id", 0) or 0))
            self.memory_list.addItem(item)

    def _toggle_memory(self) -> None:
        key = self._key()
        if not key:
            return
        self.service.set_memory_enabled(key, self._agent_id(), self.enabled_check.isChecked())
        self._notice("记忆开关已提交")

    def _add(self) -> None:
        key = self._key()
        if not key:
            return
        value, ok = QInputDialog.getMultiLineText(self, "新增记忆", "内容")
        if ok and value.strip():
            self.service.add_memory(key, self._agent_id(), value.strip())
            self._notice("正在保存记忆…")

    def _selected_memory_id(self) -> int:
        item = self.memory_list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole) or 0) if item else 0

    def _edit(self) -> None:
        memory_id = self._selected_memory_id()
        if not memory_id:
            return
        key = self._key()
        if not key:
            return
        current = next(
            (item for item in self.store.state.memories.get((self._agent_id(), key.kind, key.identifier), []) if int(item.get("id", 0)) == memory_id),
            {},
        )
        value, ok = QInputDialog.getMultiLineText(
            self, "编辑记忆", "内容", str(current.get("content") or "")
        )
        if ok and value.strip():
            self.service.update_memory(memory_id, value.strip(), str(current.get("kind") or "note"))
            self._notice("正在更新记忆…")

    def _delete(self) -> None:
        memory_id = self._selected_memory_id()
        if not memory_id:
            return
        if QMessageBox.question(self, "删除记忆", "删除后无法恢复，继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.service.delete_memory(memory_id)
        self._notice("正在删除记忆…")

    def _notice(self, message: str, kind: str = "info") -> None:
        self.notice.setText(message)
        self.notice.setProperty("noticeKind", kind)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        self.notice.setVisible(bool(message))
