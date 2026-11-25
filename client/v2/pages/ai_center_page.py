"""AI task center and task cards."""

from __future__ import annotations

import time
from typing import Any
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget
from client.models import AppState, ConversationKey, ConversationSummary
from client.service import ChatService
from client.store import AppStore
from ..widgets import EmptyState, configure_scroll_surface, configure_surface
from ..components import FilterBar, InlineNotice, PageHeader, SectionHeader, StatusBadge
from ..ui_helpers import clear_layout


TASK_TYPE_LABELS = {
    "summary": "总结会话",
    "todo": "提取待办",
    "question": "回答问题",
    "search": "搜索并整理会话",
    "identity": "Agent 信息",
    "scheduled_summary": "定时总结",
}


TASK_CENTER_TYPES = ("summary", "todo", "search", "scheduled_summary")


TASK_STATUS_LABELS = {"running": "进行中", "succeeded": "已完成", "failed": "失败"}


def task_error_text(message: str) -> str:
    detail = str(message or "").lower()
    if "database" in detail or "unable to open" in detail or "数据库" in detail:
        return "服务暂时无法访问数据，请稍后重试"
    if "timeout" in detail or "timed out" in detail:
        return "任务执行超时，请稍后重试"
    if any(term in detail for term in ("model", "模型", "ollama", "connection", "unreachable")):
        return "AI 服务暂时不可用，请稍后重试"
    return "任务执行失败，请稍后重试"


def activity_time(value: object) -> str:
    try:
        stamp = float(value or 0)
    except (TypeError, ValueError):
        stamp = 0
    return time.strftime("%m-%d %H:%M", time.localtime(stamp)) if stamp else "时间未知"


class AgentTaskCard(QFrame):
    result_requested = Signal(object, int)
    details_requested = Signal(object, str)

    def __init__(self, activity: dict[str, Any], title: str) -> None:
        super().__init__()
        self.setObjectName("TaskRow")
        key = ConversationKey(str(activity.get("conversation_type", "")), str(activity.get("conversation_id", "")))
        self.setMinimumHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        heading = QLabel(TASK_TYPE_LABELS.get(str(activity.get("task_type", "")), "AI 任务"))
        heading.setObjectName("TaskTitle")
        requester = str(activity.get("requested_by_name") or "系统")
        meta = QLabel(f"{title} · {requester} · {activity_time(activity.get('started_at'))}")
        meta.setObjectName("Muted")
        text.addWidget(heading)
        text.addWidget(meta)
        error = task_error_text(str(activity.get("error") or ""))
        if activity.get("status") == "failed" and error:
            failure = QLabel(error)
            failure.setObjectName("Danger")
            failure.setWordWrap(True)
            text.addWidget(failure)
        layout.addLayout(text, 1)
        status_key = str(activity.get("status", ""))
        status = StatusBadge(TASK_STATUS_LABELS.get(status_key, "状态未知"))
        status.setObjectName("TaskStatus")
        status.setProperty("taskStatus", status_key)
        status.set_kind({"succeeded": "success", "failed": "danger", "running": "warning"}.get(status_key, "neutral"))
        layout.addWidget(status)
        details = QPushButton("详情")
        details.setObjectName("Ghost")
        details.setAccessibleName(f"查看{heading.text()}详情")
        details.clicked.connect(lambda: self.details_requested.emit(dict(activity), title))
        layout.addWidget(details)
        result_id = int(activity.get("result_message_id", 0) or 0)
        if activity.get("status") == "succeeded" and result_id:
            result = QPushButton("查看结果")
            result.setObjectName("Ghost")
            result.clicked.connect(lambda: self.result_requested.emit(key, result_id))
            layout.addWidget(result)


class AgentTaskDetailsDialog(QDialog):
    result_requested = Signal(object, int)

    def __init__(self, activity: dict[str, Any], title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("任务详情")
        self.setMinimumWidth(440)
        key = ConversationKey(str(activity.get("conversation_type", "")), str(activity.get("conversation_id", "")))
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        heading = QLabel(TASK_TYPE_LABELS.get(str(activity.get("task_type", "")), "AI 任务"))
        heading.setObjectName("SectionTitle")
        root.addWidget(heading)
        fields = (
            ("状态", TASK_STATUS_LABELS.get(str(activity.get("status", "")), "状态未知")),
            ("来源会话", title),
            ("发起者", str(activity.get("requested_by_name") or "系统")),
            ("开始时间", activity_time(activity.get("started_at"))),
            ("完成时间", activity_time(activity.get("finished_at")) if activity.get("finished_at") else "—"),
        )
        for label, value in fields:
            row = QHBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("Muted")
            content = QLabel(value)
            content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(caption)
            row.addStretch()
            row.addWidget(content)
            root.addLayout(row)
        error = str(activity.get("error") or "").strip()
        if error:
            failure = QLabel(task_error_text(error))
            failure.setObjectName("InlineNotice")
            failure.setProperty("noticeKind", "error")
            failure.setWordWrap(True)
            failure.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(failure)
        buttons = QHBoxLayout()
        buttons.addStretch()
        result_id = int(activity.get("result_message_id", 0) or 0)
        if activity.get("status") == "succeeded" and result_id:
            result = QPushButton("查看结果")
            result.setObjectName("Primary")
            result.clicked.connect(lambda: (self.result_requested.emit(key, result_id), self.accept()))
            buttons.addWidget(result)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        root.addLayout(buttons)


class NewAgentTaskDialog(QDialog):
    def __init__(self, conversations: list[ConversationSummary], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建 AI 任务")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("会话"))
        self.scope = QComboBox()
        for conversation in conversations:
            self.scope.addItem(conversation.title, conversation.key)
        root.addWidget(self.scope)
        root.addWidget(QLabel("任务类型"))
        self.task_type = QComboBox()
        self.task_type.addItem("总结会话", "summary")
        self.task_type.addItem("提取待办", "todo")
        root.addWidget(self.task_type)
        root.addWidget(QLabel("时间范围（可选）"))
        self.time_range = QLineEdit()
        self.time_range.setPlaceholderText("例如：今天、昨天下午、最近 3 天、11月1日到11月5日")
        self.time_range.setClearButtonEnabled(True)
        root.addWidget(self.time_range)
        hint = QLabel(
            "不填时间范围时处理最近 50 条消息；任务结果会显示在对应会话中，"
            "普通问答请直接在聊天里发起。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def request(self) -> tuple[ConversationKey | None, str]:
        key = self.scope.currentData()
        task_type = str(self.task_type.currentData())
        time_range = self.time_range.text().strip()
        if time_range:
            prompts = {
                "summary": f"总结{time_range}的消息",
                "todo": f"提取{time_range}的待办",
            }
        else:
            prompts = {
                "summary": "总结最近 50 条消息",
                "todo": "提取最近 50 条消息中的待办",
            }
        return (key if isinstance(key, ConversationKey) else None, prompts.get(task_type, ""))


class AICenterPage(QWidget):
    result_requested = Signal(object, int)

    def __init__(self, service: ChatService, store: AppStore) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self.service = service
        self.store = store
        self._render_signature: tuple[Any, ...] | None = None
        self._task_action_pending = False
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(16)
        shell_row = QHBoxLayout()
        shell_row.addStretch()
        shell = QWidget()
        configure_surface(shell, "canvas")
        shell.setMaximumWidth(1100)
        content = QVBoxLayout(shell)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)
        self.header = PageHeader(
            "我的 AI 任务",
            "只显示你发起的总结、待办与整理任务；普通问答保留在会话中。",
            eyebrow="AI 工作台",
        )
        self.new_task = QPushButton("新建 AI 任务")
        self.new_task.setObjectName("Primary")
        self.new_task.setEnabled(False)
        self.header.add_action(self.new_task)
        self.subtitle = self.header.subtitle
        self.status = StatusBadge("Agent 状态未知", "agent")
        self.notice = InlineNotice("")
        self.notice.hide()
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        status_row.addWidget(self.status)
        status_row.addStretch(1)
        filters = FilterBar()
        self.status_filter = QComboBox()
        for label, value in (("全部状态", ""), ("进行中", "running"), ("已完成", "succeeded"), ("失败", "failed")):
            self.status_filter.addItem(label, value)
        self.type_filter = QComboBox()
        self.type_filter.addItem("全部类型", "")
        for value in TASK_CENTER_TYPES:
            self.type_filter.addItem(TASK_TYPE_LABELS[value], value)
        self.scope_filter = QComboBox()
        self.scope_filter.addItem("全部会话", None)
        self.filter_summary = QLabel("0 个任务")
        self.filter_summary.setObjectName("Muted")
        filters.add_widget(self.status_filter)
        filters.add_widget(self.type_filter)
        filters.add_widget(self.scope_filter, 1)
        filters.add_widget(self.filter_summary)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("TaskScroll")
        self.scroll.setWidgetResizable(True)
        self.task_content = QWidget()
        self.task_rows = QVBoxLayout(self.task_content)
        self.task_rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.task_rows.setSpacing(0)
        configure_scroll_surface(self.scroll, self.task_content, "canvas")
        self.scroll.setWidget(self.task_content)
        content.addWidget(self.header)
        content.addLayout(status_row)
        content.addWidget(self.notice)
        content.addWidget(SectionHeader("任务记录", "按状态、类型或来源会话筛选"))
        content.addWidget(filters)
        content.addWidget(self.scroll, 1)
        shell_row.addWidget(shell, 1)
        shell_row.addStretch()
        root.addLayout(shell_row, 1)
        self.new_task.clicked.connect(self._new_task)
        self.status_filter.currentIndexChanged.connect(self._render)
        self.type_filter.currentIndexChanged.connect(self._render)
        self.scope_filter.currentIndexChanged.connect(self._render)

    def update_state(self, topic: str, state: AppState) -> None:
        if topic not in {
            "auth",
            "connection",
            "session",
            "activities",
            "agent",
            "conversations",
            "pending",
            "error",
            "ack",
        }:
            return
        self.status.setText(f"Agent {'在线' if state.agent_online else '离线'}" + (f" · {state.agent_model}" if state.agent_model else ""))
        self.status.set_kind("agent" if state.agent_online else "neutral")
        self.new_task.setEnabled(state.agent_online and bool(state.conversations))
        self.new_task.setToolTip("" if self.new_task.isEnabled() else "需要 Agent 在线且至少存在一个会话")
        if topic in {"pending", "error", "activities", "agent", "auth", "ack"}:
            pending = any(action in {"agent.activity.list", "agent.invoke"} for action in state.pending.values())
            if pending:
                self.notice.setText("正在更新任务状态…")
                self.notice.setProperty("noticeKind", "info")
            elif topic == "error" and self._task_action_pending and state.last_error:
                self.notice.setText(f"任务操作失败：{state.last_error}")
                self.notice.setProperty("noticeKind", "error")
                self._task_action_pending = False
            elif topic == "ack" and self._task_action_pending:
                self.notice.setText("任务已提交，结果会显示在对应会话中。")
                self.notice.setProperty("noticeKind", "success")
                self._task_action_pending = False
            else:
                self.notice.clear()
            self.notice.setVisible(bool(self.notice.text()))
            self.notice.style().unpolish(self.notice)
            self.notice.style().polish(self.notice)
        if topic not in {"auth", "activities", "agent", "conversations", "pending", "error", "ack"}:
            return
        if topic in {"auth", "conversations"}:
            selected = self.scope_filter.currentData()
            self.scope_filter.clear()
            self.scope_filter.addItem("全部会话", None)
            for conversation in state.conversations:
                self.scope_filter.addItem(conversation.title, conversation.key)
            if selected:
                index = self.scope_filter.findData(selected)
                if index >= 0:
                    self.scope_filter.setCurrentIndex(index)
        self._render()

    def _conversation_title(self, key: ConversationKey) -> str:
        summary = next((item for item in self.store.state.conversations if item.key == key), None)
        return summary.title if summary else "历史会话"

    def _render(self) -> None:
        status = str(self.status_filter.currentData() or "")
        task_type = str(self.type_filter.currentData() or "")
        scope = self.scope_filter.currentData()
        current_user_id = self.store.state.current_user.get("id")
        activities = [
            item for item in self.store.state.activities
            if str(item.get("task_type") or "") in TASK_CENTER_TYPES
            and (
                current_user_id is None
                or not item.get("requested_by")
                or str(item.get("requested_by")) == str(current_user_id)
            )
            and (not status or item.get("status") == status)
            and (not task_type or item.get("task_type") == task_type)
            and (
                not isinstance(scope, ConversationKey)
                or (str(item.get("conversation_type")) == scope.kind and str(item.get("conversation_id")) == scope.identifier)
            )
        ]
        signature = (
            current_user_id,
            status,
            task_type,
            scope,
            tuple(
                (
                    item.get("id"),
                    item.get("status"),
                    item.get("error"),
                    item.get("result_message_id"),
                    item.get("finished_at"),
                    self._conversation_title(
                        ConversationKey(
                            str(item.get("conversation_type", "")),
                            str(item.get("conversation_id", "")),
                        )
                    ),
                )
                for item in activities
            ),
        )
        if signature == self._render_signature and self.task_rows.count():
            return
        self._render_signature = signature
        clear_layout(self.task_rows)
        self.filter_summary.setText(f"{len(activities)} 个任务")
        if not activities:
            filtered = bool(status or task_type or isinstance(scope, ConversationKey))
            self.task_rows.addWidget(
                EmptyState(
                    "没有匹配的任务" if filtered else "还没有个人 AI 任务",
                    "调整筛选条件"
                    if filtered
                    else "普通问答会保留在会话中；使用“新建 AI 任务”发起总结或待办后，会显示在这里",
                    "◇",
                )
            )
            return
        for activity in activities:
            key = ConversationKey(str(activity.get("conversation_type", "")), str(activity.get("conversation_id", "")))
            card = AgentTaskCard(activity, self._conversation_title(key))
            card.result_requested.connect(self.result_requested)
            card.details_requested.connect(self._show_details)
            self.task_rows.addWidget(card)

    def _show_details(self, activity: object, title: str) -> None:
        if not isinstance(activity, dict):
            return
        dialog = AgentTaskDetailsDialog(activity, title, self)
        dialog.result_requested.connect(self.result_requested)
        dialog.exec()

    def _new_task(self) -> None:
        dialog = NewAgentTaskDialog(self.store.state.conversations, self)
        if not dialog.exec():
            return
        key, prompt = dialog.request()
        if not key or not prompt:
            QMessageBox.warning(self, "任务信息不完整", "请选择要处理的会话。")
            return
        self._task_action_pending = True
        self.notice.setText("正在提交任务…")
        self.notice.setProperty("noticeKind", "info")
        self.notice.show()
        if not self.service.invoke_agent(key, prompt):
            self._task_action_pending = False
            if self.store.state.last_error:
                self.notice.setText(f"任务操作失败：{self.store.state.last_error}")
                self.notice.setProperty("noticeKind", "error")
