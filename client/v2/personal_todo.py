"""Personal todo page; mutations go through ChatService and AppStore."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QDateTime, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractButton,
    QCalendarWidget,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from client.models import AppState
from client.service import ChatService

from .icons import lucide_icon, lucide_icon_with_color
from .theme import active_theme_manager, current_tokens
from .widgets import configure_scroll_surface, configure_surface


PRIORITY_LABELS = {"low": "低优先级", "normal": "普通", "high": "高优先级"}
PRIORITY_SHORT = {"low": "低", "normal": "中", "high": "高"}
SOURCE_LABELS = {
    "manual": "手动创建",
    "agent_extracted": "Agent 提取",
    "chat_message": "聊天消息",
}
SOURCE_ICONS = {
    "manual": "user-round",
    "agent_extracted": "sparkles",
    "chat_message": "message-circle",
}


class TodoChoiceButton(QPushButton):
    """Lightweight menu button with a themed chevron instead of a platform combo box."""

    currentIndexChanged = Signal(int)

    def __init__(self, accessible_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TodoChoice")
        self.setAccessibleName(accessible_name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._items: list[tuple[str, Any]] = []
        self._index = -1
        self._menu = QMenu(self)
        self._menu.setObjectName("TodoChoiceMenu")
        self.clicked.connect(self._show_menu)
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(lambda _snapshot: self.update())

    def addItem(self, label: str, value: Any) -> None:
        index = len(self._items)
        self._items.append((label, value))
        action = self._menu.addAction(label)
        action.setCheckable(True)
        action.triggered.connect(
            lambda _checked=False, position=index: self.setCurrentIndex(position)
        )
        if index == 0:
            self.setCurrentIndex(0)

    def currentData(self) -> Any:
        return self._items[self._index][1] if self._index >= 0 else None

    def findData(self, value: Any) -> int:
        return next(
            (index for index, item in enumerate(self._items) if item[1] == value), -1
        )

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._items) or index == self._index:
            return
        self._index = index
        self.setText(self._items[index][0])
        for position, action in enumerate(self._menu.actions()):
            action.setChecked(position == index)
        self.currentIndexChanged.emit(index)

    def _show_menu(self) -> None:
        self._menu.setMinimumWidth(max(self.width(), 148))
        self._menu.popup(self.mapToGlobal(QPoint(0, self.height() + 5)))

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        pixmap = lucide_icon("chevron-down", 15).pixmap(QSize(15, 15))
        painter = QPainter(self)
        painter.drawPixmap(self.width() - 25, (self.height() - 15) // 2, pixmap)
        painter.end()


class TodoCheckBox(QAbstractButton):
    """Theme-aware completion control with no native indicator drawing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hovered = False
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(lambda _snapshot: self.update())

    def enterEvent(self, event: Any) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event: Any) -> None:
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = (
            tokens.primary
            if self.isChecked() or self._hovered
            else tokens.border_strong
        )
        if not self.isEnabled():
            border = tokens.text_disabled
        painter.setPen(QPen(QColor(border), 1.5))
        painter.setBrush(
            QColor(tokens.primary if self.isChecked() else tokens.surface)
        )
        painter.drawRoundedRect(3, 3, 17, 17, 4, 4)
        if self.isChecked():
            check_pen = QPen(QColor(tokens.surface), 2)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            painter.drawLine(7, 12, 10, 15)
            painter.drawLine(10, 15, 16, 9)
        if self.hasFocus():
            painter.setPen(QPen(QColor(tokens.primary), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(1, 1, 21, 21, 5, 5)
        painter.end()


def _due_label(timestamp: Any) -> str:
    if timestamp is None:
        return "未设置截止时间"
    due = datetime.fromtimestamp(float(timestamp))
    today = datetime.now().date()
    day = (
        "今天"
        if due.date() == today
        else "明天"
        if due.date() == today + timedelta(days=1)
        else due.strftime("%m月%d日")
    )
    return f"{day} {due:%H:%M}"


class PersonalTodoPage(QWidget):
    def __init__(self, service: ChatService) -> None:
        super().__init__()
        self.service = service
        self._state = AppState()
        self._selected_id: int | None = None
        self._selected_version: int | None = None
        self._dirty = False
        self._editing_new = False
        self._populating = False
        self._collapsed: set[str] = {"completed"}
        self._source_type = "manual"
        self._icon_labels: list[tuple[QLabel, str, int]] = []
        self._user_id: Any = None

        configure_surface(self, "canvas")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(0)
        self.shell = QFrame()
        self.shell.setObjectName("TodoWorkspace")
        self.page_layout = QVBoxLayout(self.shell)
        self.page_layout.setContentsMargins(26, 24, 26, 22)
        self.page_layout.setSpacing(16)
        outer.addWidget(self.shell, 1)

        header = QHBoxLayout()
        header.setSpacing(14)
        title = QLabel("我的待办")
        title.setObjectName("TodoPageTitle")
        header.addWidget(title)
        self.summary = QLabel()
        self.summary.setObjectName("TodoSummary")
        header.addWidget(self.summary)
        header.addStretch(1)
        self.page_layout.addLayout(header)

        self.quick_bar = QWidget()
        configure_surface(self.quick_bar, "surface")
        quick = QHBoxLayout(self.quick_bar)
        quick.setContentsMargins(0, 0, 0, 0)
        quick.setSpacing(12)
        self.quick_title = QLineEdit()
        self.quick_title.setObjectName("TodoQuickInput")
        self.quick_title.setPlaceholderText("添加待办，按回车创建")
        self.quick_title.setAccessibleName("待办名称")
        self.quick_title.setMaxLength(200)
        self.quick_title.setMinimumHeight(44)
        self.quick_action = self.quick_title.addAction(
            lucide_icon("plus", 20), QLineEdit.ActionPosition.LeadingPosition
        )
        self.quick_title.returnPressed.connect(self._create)
        quick.addWidget(self.quick_title, 1)
        self.add_button = QPushButton("新建待办")
        self.add_button.setObjectName("TodoAddButton")
        self.add_button.setAccessibleName("新建待办")
        self.add_button.setMinimumHeight(44)
        self.add_button.clicked.connect(self._new)
        quick.addWidget(self.add_button)
        self.page_layout.addWidget(self.quick_bar)

        self.filter_bar = QWidget()
        configure_surface(self.filter_bar, "surface")
        filters = QHBoxLayout(self.filter_bar)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        self.category = TodoChoiceButton("待办分类")
        for key, label in (
            ("all", "全部待办"),
            ("today", "今日待办"),
            ("upcoming", "即将到期"),
            ("completed", "已完成"),
        ):
            self.category.addItem(label, key)
        self.category.currentIndexChanged.connect(self._category_changed)
        filters.addWidget(self.category)
        self.priority_filter = TodoChoiceButton("待办优先级筛选")
        for key, label in (
            ("all", "全部优先级"),
            ("high", "高优先级"),
            ("normal", "普通"),
            ("low", "低优先级"),
        ):
            self.priority_filter.addItem(label, key)
        self.priority_filter.currentIndexChanged.connect(self._render)
        filters.addWidget(self.priority_filter)
        self.sort = TodoChoiceButton("待办排序")
        self.sort.addItem("按截止时间", "due")
        self.sort.addItem("按优先级", "priority")
        self.sort.currentIndexChanged.connect(self._render)
        filters.addWidget(self.sort)
        filters.addStretch(1)
        self.page_layout.addWidget(self.filter_bar)

        self.feedback = QLabel()
        self.feedback.setObjectName("TodoFeedback")
        self.feedback.setProperty("state", "loading")
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        self.page_layout.addWidget(self.feedback)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("TodoListScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_content = QWidget()
        configure_scroll_surface(self.scroll, self.list_content, "surface")
        self.list_layout = QVBoxLayout(self.list_content)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(0)
        self.scroll.setWidget(self.list_content)
        body.addWidget(self.scroll, 1)

        self.detail = QFrame()
        self.detail.setObjectName("TodoDetail")
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("TodoDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.detail_scroll.setMinimumWidth(400)
        self.detail_scroll.setMaximumWidth(460)
        configure_scroll_surface(self.detail_scroll, self.detail, "surface")
        form = QVBoxLayout(self.detail)
        form.setContentsMargins(4, 2, 6, 2)
        form.setSpacing(13)

        detail_toolbar = QHBoxLayout()
        detail_toolbar.setSpacing(4)
        detail_toolbar.addStretch(1)
        self.more_button = QToolButton()
        self.more_button.setObjectName("TodoIconButton")
        self.more_button.setAccessibleName("更多待办操作")
        self.more_button.setToolTip("更多操作")
        self.more_menu = QMenu(self)
        self.more_delete = self.more_menu.addAction("删除待办")
        self.more_delete.triggered.connect(self._delete)
        self.more_button.clicked.connect(
            lambda: self.more_menu.popup(
                self.more_button.mapToGlobal(QPoint(0, self.more_button.height() + 4))
            )
        )
        detail_toolbar.addWidget(self.more_button)
        self.close_button = QToolButton()
        self.close_button.setObjectName("TodoIconButton")
        self.close_button.setAccessibleName("关闭待办详情")
        self.close_button.setToolTip("关闭待办详情")
        self.close_button.clicked.connect(self._close_detail)
        detail_toolbar.addWidget(self.close_button)
        form.addLayout(detail_toolbar)

        self.edit_title = QLineEdit()
        self.edit_title.setObjectName("TodoDetailTitle")
        self.edit_title.setPlaceholderText("待办名称")
        self.edit_title.setAccessibleName("编辑待办名称")
        self.edit_title.setMaxLength(200)
        form.addWidget(self.edit_title)

        choices = QHBoxLayout()
        choices.setSpacing(10)
        self.edit_priority = TodoChoiceButton("优先级")
        for key, label in (("normal", "中"), ("high", "高"), ("low", "低")):
            self.edit_priority.addItem(label, key)
        self.edit_priority.setMinimumWidth(148)
        self.edit_priority.currentIndexChanged.connect(self._priority_changed)
        choices.addWidget(self.edit_priority)
        self.edit_status = TodoChoiceButton("完成状态")
        self.edit_status.setObjectName("TodoStatusChoice")
        self.edit_status.addItem("未完成", False)
        self.edit_status.addItem("已完成", True)
        self.edit_status.setMinimumWidth(160)
        self.edit_status.currentIndexChanged.connect(self._change_status)
        choices.addWidget(self.edit_status)
        choices.addStretch(1)
        form.addLayout(choices)

        form.addWidget(self._field_heading("file-text", "描述"))
        self.edit_description = QTextEdit()
        self.edit_description.setObjectName("TodoDescription")
        self.edit_description.setAccessibleName("编辑待办描述")
        self.edit_description.setPlaceholderText("添加任务描述")
        self.edit_description.setFixedHeight(112)
        form.addWidget(self.edit_description)

        form.addWidget(self._field_heading("calendar-days", "截止时间"))
        due_row = QHBoxLayout()
        due_row.setSpacing(8)
        self.edit_due = QLineEdit()
        self.edit_due.setObjectName("TodoDateInput")
        self.edit_due.setAccessibleName("截止时间")
        self.edit_due.setPlaceholderText("未设置截止时间")
        self.edit_due.setMaxLength(16)
        self.due_clear_action = self.edit_due.addAction(
            lucide_icon("x", 15), QLineEdit.ActionPosition.TrailingPosition
        )
        self.due_clear_action.setToolTip("清除截止时间")
        self.due_clear_action.triggered.connect(self.edit_due.clear)
        self.edit_due.textChanged.connect(
            lambda value: self.due_clear_action.setVisible(bool(value))
        )
        self.due_clear_action.setVisible(False)
        due_row.addWidget(self.edit_due, 1)
        self.calendar_button = QToolButton()
        self.calendar_button.setObjectName("TodoIconButton")
        self.calendar_button.setAccessibleName("选择截止日期")
        self.calendar_button.setToolTip("选择截止日期")
        self.calendar_button.clicked.connect(self._pick_due)
        due_row.addWidget(self.calendar_button)
        form.addLayout(due_row)

        form.addWidget(self._field_heading("sparkles", "来源"))
        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        self.source_icon = QLabel()
        self.source_icon.setObjectName("TodoFieldIcon")
        source_row.addWidget(self.source_icon)
        self.source = QLabel()
        self.source.setObjectName("TodoMeta")
        self.source.setWordWrap(True)
        source_row.addWidget(self.source, 1)
        form.addLayout(source_row)
        self.open_message = QPushButton("查看原始消息")
        self.open_message.setObjectName("TodoLink")
        self.open_message.setAccessibleName("查看待办关联的原始消息")
        self.open_message.clicked.connect(self._resolve_message)
        form.addWidget(self.open_message)
        self.created_at = QLabel()
        self.created_at.setObjectName("TodoCreatedAt")
        form.addWidget(self.created_at)
        form.addStretch(1)

        footer = QFrame()
        footer.setObjectName("TodoDetailFooter")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(0, 12, 0, 0)
        actions.setSpacing(8)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.clicked.connect(self._delete)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        self.save_button = QPushButton("保存修改")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        form.addWidget(footer)

        self.detail_scroll.setWidget(self.detail)
        self.detail_scroll.hide()
        body.addWidget(self.detail_scroll)
        self.page_layout.addLayout(body, 1)

        self.edit_title.textChanged.connect(self._mark_dirty)
        self.edit_description.textChanged.connect(self._mark_dirty)
        self.edit_due.textChanged.connect(self._mark_dirty)
        self.edit_priority.currentIndexChanged.connect(self._mark_dirty)

        self._refresh_icons()
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(self._theme_changed)
        self._render()

    def _field_heading(self, icon_name: str, text: str) -> QWidget:
        widget = QWidget()
        configure_surface(widget, "surface")
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        icon = QLabel()
        icon.setObjectName("TodoFieldIcon")
        icon.setFixedSize(18, 18)
        self._icon_labels.append((icon, icon_name, 16))
        row.addWidget(icon)
        label = QLabel(text)
        label.setObjectName("TodoFieldLabel")
        row.addWidget(label)
        row.addStretch(1)
        return widget

    def _theme_changed(self, *_args: object) -> None:
        self._refresh_icons()
        self._render()

    def _set_icon(self, label: QLabel, name: str, size: int, color: str) -> None:
        label.setPixmap(
            lucide_icon_with_color(name, color, size).pixmap(QSize(size, size))
        )

    def _refresh_icons(self) -> None:
        tokens = current_tokens()
        self.quick_action.setIcon(lucide_icon("plus", 20))
        for button, icon_name in (
            (self.close_button, "x"),
            (self.calendar_button, "calendar-days"),
            (self.more_button, "more-horizontal"),
        ):
            button.setIcon(lucide_icon(icon_name, 18))
            button.setIconSize(QSize(18, 18))
        self.open_message.setIcon(lucide_icon("external-link", 16))
        for label, icon_name, size in self._icon_labels:
            self._set_icon(label, icon_name, size, tokens.text_secondary)
        self._refresh_priority_icon()
        self._refresh_source_icon()

    def _refresh_priority_icon(self) -> None:
        tokens = current_tokens()
        priority = str(self.edit_priority.currentData() or "normal")
        color = {
            "high": tokens.danger,
            "normal": tokens.warning,
            "low": tokens.primary,
        }.get(priority, tokens.text_secondary)
        self.edit_priority.setIcon(lucide_icon_with_color("flag", color, 16))
        self.edit_priority.setIconSize(QSize(16, 16))

    def _refresh_source_icon(self) -> None:
        tokens = current_tokens()
        icon_name = SOURCE_ICONS.get(self._source_type, "arrow-right")
        color = (
            tokens.agent
            if self._source_type == "agent_extracted"
            else tokens.text_secondary
        )
        self._set_icon(self.source_icon, icon_name, 16, color)

    def update_state(self, topic: str, state: AppState) -> None:
        self._state = state
        user_id = state.current_user.get("id") if state.authenticated else None
        if self._user_id is not None and user_id != self._user_id:
            self._selected_id = None
            self._selected_version = None
            self._dirty = False
            self._editing_new = False
        self._user_id = user_id
        if topic in {"auth", "todos", "connection", "pending", "session"}:
            self._render()

    def _filtered(self) -> list[dict[str, Any]]:
        category = self.category.currentData()
        priority_filter = self.priority_filter.currentData()
        records = []
        for todo in self._state.todos:
            group = self._group_key(todo)
            if priority_filter != "all" and todo.get("priority") != priority_filter:
                continue
            if category == "completed" and group != "completed":
                continue
            if category == "today" and group != "today":
                continue
            if category == "upcoming" and group != "upcoming":
                continue
            records.append(todo)
        priority = {"high": 0, "normal": 1, "low": 2}
        if self.sort.currentData() == "priority":
            records.sort(
                key=lambda item: (
                    bool(item.get("completed")),
                    priority.get(item.get("priority"), 1),
                    item.get("due_at") is None,
                    item.get("due_at") or 0,
                    item.get("id", 0),
                )
            )
        else:
            records.sort(
                key=lambda item: (
                    bool(item.get("completed")),
                    item.get("due_at") is None,
                    item.get("due_at") or 0,
                    priority.get(item.get("priority"), 1),
                    item.get("id", 0),
                )
            )
        return records

    @staticmethod
    def _group_key(todo: dict[str, Any]) -> str:
        if todo.get("completed"):
            return "completed"
        due = todo.get("due_at")
        if due is None:
            return "undated"
        today = datetime.now().date()
        due_date = datetime.fromtimestamp(float(due)).date()
        if due_date < today:
            return "overdue"
        if due_date == today:
            return "today"
        if due_date <= today + timedelta(days=7):
            return "upcoming"
        return "later"

    def _sections(
        self, records: list[dict[str, Any]]
    ) -> list[tuple[str, str, list[dict[str, Any]]]]:
        labels = (
            ("overdue", "已逾期"),
            ("today", "今天"),
            ("upcoming", "即将到期"),
            ("later", "以后"),
            ("undated", "未设置日期"),
            ("completed", "已完成"),
        )
        grouped = {key: [] for key, _label in labels}
        for todo in records:
            grouped[self._group_key(todo)].append(todo)
        return [
            (key, label, grouped[key])
            for key, label in labels
            if grouped[key]
        ]

    def _render(self, *_args: object) -> None:
        enabled = self._state.connected and self._state.authenticated
        self.quick_title.setEnabled(enabled)
        self.add_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.more_delete.setEnabled(enabled)
        self.open_message.setEnabled(enabled)
        self.edit_status.setEnabled(enabled and not self._editing_new)
        self.summary.setText(
            f"今天 {sum(self._group_key(todo) == 'today' for todo in self._state.todos)}"
            f"  ·  全部 {len(self._state.todos)}"
            f"  ·  已完成 {sum(bool(todo.get('completed')) for todo in self._state.todos)}"
        )
        message = self._state.todo_error
        state_name = "error"
        if not message and not self._state.connected:
            message = "连接已断开，待办会在重连后同步"
            state_name = "offline"
        elif not message and not self._state.todos_loaded:
            message = "正在加载待办…"
            state_name = "loading"
        self.feedback.setText(message)
        self.feedback.setProperty("state", state_name)
        self._repolish_feedback()
        self.feedback.setVisible(bool(message))

        scroll_position = self.scroll.verticalScrollBar().value()
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        records = self._filtered()
        visible_ids = {int(todo.get("id", 0)) for todo in records}
        if self._selected_id is not None and not self._dirty:
            selected = next(
                (
                    item
                    for item in self._state.todos
                    if int(item.get("id", 0)) == self._selected_id
                ),
                None,
            )
            if selected is not None and selected.get("version") is not None:
                self._selected_version = int(selected["version"])
        if self._selected_id is not None and self._selected_id not in visible_ids:
            self._selected_id = None
            self._selected_version = None
            self._dirty = False
            self._editing_new = False
        if not records:
            empty_text = {
                "today": "今天还没有待办",
                "upcoming": "未来 7 天没有到期待办",
                "completed": "还没有已完成待办",
            }.get(self.category.currentData(), "还没有待办，输入名称即可添加")
            if self.priority_filter.currentData() != "all":
                empty_text = "当前筛选下没有待办"
            if not self._state.connected:
                empty_text = "连接恢复后将自动同步待办"
            elif not self._state.todos_loaded:
                empty_text = "正在同步待办"
            self._add_empty(empty_text)
        else:
            for key, label, items in self._sections(records):
                self._add_section(key, label, len(items))
                if key not in self._collapsed:
                    for todo in items:
                        self._add_row(todo, enabled)
        self.list_layout.addStretch(1)
        QTimer.singleShot(
            0,
            lambda position=scroll_position: self.scroll.verticalScrollBar().setValue(
                position
            ),
        )
        self._apply_layout()

    def _add_empty(self, message: str) -> None:
        empty = QWidget()
        configure_surface(empty, "surface")
        layout = QVBoxLayout(empty)
        layout.setContentsMargins(24, 64, 24, 24)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        icon = QLabel()
        icon.setObjectName("TodoEmptyIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_icon(icon, "list-todo", 26, current_tokens().text_tertiary)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        label = QLabel(message)
        label.setObjectName("TodoEmpty")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        self.list_layout.addWidget(empty)

    def _add_section(self, key: str, label: str, count: int) -> None:
        header = QFrame()
        header.setObjectName("TodoSection")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(2, 16, 2, 5)
        layout.setSpacing(8)
        toggle = QPushButton(label)
        toggle.setObjectName("TodoSectionToggle")
        toggle.setAccessibleName(
            ("展开" if key in self._collapsed else "收起") + label
        )
        toggle.setIcon(
            lucide_icon(
                "chevron-right" if key in self._collapsed else "chevron-down", 16
            )
        )
        toggle.setIconSize(QSize(16, 16))
        toggle.clicked.connect(
            lambda _checked=False, section=key: self._toggle_section(section)
        )
        layout.addWidget(toggle)
        badge = QLabel(str(count))
        badge.setObjectName("TodoSectionCount")
        layout.addWidget(badge)
        layout.addStretch(1)
        self.list_layout.addWidget(header)

    def _category_changed(self, _index: int) -> None:
        if self.category.currentData() == "completed":
            self._collapsed.discard("completed")
        self._render()

    def _toggle_section(self, key: str) -> None:
        if key in self._collapsed:
            self._collapsed.remove(key)
        else:
            self._collapsed.add(key)
        self._render()

    def _add_row(self, todo: dict[str, Any], enabled: bool) -> None:
        row = QFrame()
        row.setObjectName("TodoRow")
        row.setProperty(
            "selected", "true" if todo["id"] == self._selected_id else "false"
        )
        row.setProperty("completed", "true" if todo.get("completed") else "false")
        row.setMinimumHeight(88)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(12)
        check = TodoCheckBox()
        check.setAccessibleName(f"完成待办：{todo['title']}")
        check.setChecked(bool(todo.get("completed")))
        check.setEnabled(enabled)
        check.toggled.connect(
            lambda checked, todo_id=int(todo["id"]), version=int(todo.get("version") or 0): self.service.complete_todo(
                todo_id, checked, version or None
            )
        )
        layout.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(3)
        name = QPushButton(str(todo["title"]))
        name.setObjectName("TodoTitle")
        name.setAccessibleName(f"编辑待办：{todo['title']}")
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        name.clicked.connect(
            lambda _checked=False, todo_id=int(todo["id"]): self._select(todo_id)
        )
        if todo.get("completed"):
            font = name.font()
            font.setStrikeOut(True)
            name.setFont(font)
        column.addWidget(name)

        tokens = current_tokens()
        due_row = QHBoxLayout()
        due_row.setSpacing(6)
        calendar = QLabel()
        calendar.setObjectName("TodoRowIcon")
        self._set_icon(calendar, "calendar-days", 14, tokens.text_tertiary)
        due_row.addWidget(calendar)
        priority = str(todo.get("priority", "normal"))
        due_text = _due_label(todo.get("due_at"))
        metadata = QLabel(f"{due_text}  ·  {PRIORITY_LABELS.get(priority, '普通')}")
        metadata.setObjectName("TodoMeta")
        if not todo.get("completed") and todo.get("due_at") is not None:
            due_date = datetime.fromtimestamp(float(todo["due_at"])).date()
            if due_date < datetime.now().date():
                metadata.setProperty("state", "overdue")
        due_row.addWidget(metadata)
        due_row.addStretch(1)
        column.addLayout(due_row)

        source_row = QHBoxLayout()
        source_row.setSpacing(6)
        source_type = str(todo.get("source_type", "manual"))
        source_icon = QLabel()
        source_icon.setObjectName("TodoRowIcon")
        source_color = (
            tokens.agent
            if source_type == "agent_extracted"
            else tokens.text_tertiary
        )
        self._set_icon(
            source_icon,
            SOURCE_ICONS.get(source_type, "arrow-right"),
            14,
            source_color,
        )
        source_row.addWidget(source_icon)
        source = QLabel(SOURCE_LABELS.get(source_type, "其他来源"))
        source.setObjectName("TodoSource")
        source_row.addWidget(source)
        source_row.addStretch(1)
        column.addLayout(source_row)
        layout.addLayout(column, 1)

        if todo.get("completed"):
            badge = QLabel("已完成")
            badge.setObjectName("TodoDoneBadge")
            badge.setFixedHeight(25)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            priority_wrap = QWidget()
            configure_surface(priority_wrap, "surface")
            badge_row = QHBoxLayout(priority_wrap)
            badge_row.setContentsMargins(0, 0, 0, 0)
            badge_row.setSpacing(5)
            flag = QLabel()
            flag.setObjectName("TodoFlag")
            color = (
                tokens.danger
                if priority == "high"
                else tokens.warning
                if priority == "normal"
                else tokens.primary
            )
            self._set_icon(flag, "flag", 16, color)
            badge_row.addWidget(flag)
            badge = QLabel(PRIORITY_SHORT.get(priority, "中"))
            badge.setObjectName("TodoPriorityBadge")
            badge.setProperty("priority", priority)
            badge.setFixedHeight(25)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge_row.addWidget(badge)
            layout.addWidget(priority_wrap, 0, Qt.AlignmentFlag.AlignVCenter)
        self.list_layout.addWidget(row)

    def _apply_layout(self) -> None:
        detail_open = self._selected_id is not None or self._editing_new
        compact = self.width() < 1040
        self.page_layout.setContentsMargins(
            16 if compact else 26,
            18 if compact else 24,
            16 if compact else 26,
            18,
        )
        self.quick_bar.setVisible(not (compact and detail_open))
        self.filter_bar.setVisible(not (compact and detail_open))
        feedback_state = str(self.feedback.property("state"))
        self.feedback.setVisible(
            bool(self.feedback.text())
            and (
                not (compact and detail_open)
                or feedback_state in {"error", "offline"}
            )
        )
        self.scroll.setVisible(not compact or not detail_open)
        self.detail_scroll.setMaximumWidth(16_777_215 if compact else 460)
        self.detail_scroll.setMinimumWidth(0 if compact else 400)
        self.detail_scroll.setVisible(detail_open)

    def _show_feedback(self, message: str, state: str = "error") -> None:
        self.feedback.setText(message)
        self.feedback.setProperty("state", state)
        self._repolish_feedback()
        self.feedback.setVisible(True)

    def _repolish_feedback(self) -> None:
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)

    def _mark_dirty(self, *_args: object) -> None:
        if not self._populating:
            self._dirty = True

    def _create(self) -> None:
        title = self.quick_title.text().strip()
        if not title:
            self.quick_title.setFocus()
            return
        if self.service.create_todo(title):
            self.quick_title.clear()

    def _new(self) -> None:
        self._selected_id = None
        self._editing_new = True
        self._selected_version = None
        self._dirty = False
        self._populating = True
        self.edit_title.setText(self.quick_title.text().strip())
        self.edit_description.clear()
        self.edit_due.clear()
        self.edit_priority.setCurrentIndex(0)
        self.edit_status.setCurrentIndex(0)
        self._populating = False
        self._source_type = "manual"
        self.source.setText("手动创建")
        self._refresh_source_icon()
        self.created_at.clear()
        self.open_message.hide()
        self.delete_button.hide()
        self.more_button.hide()
        self.save_button.setText("创建待办")
        self._render()
        self.edit_title.setFocus()

    def _select(self, todo_id: int) -> None:
        todo = next(
            (
                item
                for item in self._state.todos
                if int(item.get("id", 0)) == todo_id
            ),
            None,
        )
        if not todo:
            return
        self._editing_new = False
        self._selected_id = todo_id
        self._selected_version = int(todo.get("version") or 0) or None
        self._dirty = False
        self._populating = True
        self.edit_title.setText(str(todo.get("title", "")))
        self.edit_description.setPlainText(str(todo.get("description", "")))
        due = todo.get("due_at")
        self.edit_due.setText(
            datetime.fromtimestamp(float(due)).strftime("%Y-%m-%d %H:%M")
            if due is not None
            else ""
        )
        priority_index = self.edit_priority.findData(
            todo.get("priority", "normal")
        )
        self.edit_priority.setCurrentIndex(max(0, priority_index))
        self.edit_status.setCurrentIndex(1 if todo.get("completed") else 0)
        self._populating = False
        self._source_type = str(todo.get("source_type", "manual"))
        source = SOURCE_LABELS.get(self._source_type, "其他来源")
        conversation_id = todo.get("source_conversation_id")
        if conversation_id and self._source_type != "manual":
            source += f" · 会话 {conversation_id}"
        self.source.setText(source)
        self._refresh_priority_icon()
        self._refresh_source_icon()
        self.open_message.setVisible(todo.get("source_message_id") is not None)
        created = todo.get("created_at")
        self.created_at.setText(
            "创建时间："
            + datetime.fromtimestamp(float(created)).strftime("%Y-%m-%d %H:%M")
            if created is not None
            else ""
        )
        self.delete_button.show()
        self.more_button.show()
        self.save_button.setText("保存修改")
        self._render()

    def select_todo(self, todo_id: int) -> bool:
        todo = next(
            (
                item
                for item in self._state.todos
                if int(item.get("id", 0)) == int(todo_id)
            ),
            None,
        )
        if todo is None:
            return False
        if todo_id not in {int(item.get("id", 0)) for item in self._filtered()}:
            self.priority_filter.setCurrentIndex(0)
            self.category.setCurrentIndex(0)
        self._select(int(todo_id))
        return True

    def _close_detail(self) -> None:
        self._selected_id = None
        self._selected_version = None
        self._dirty = False
        self._editing_new = False
        self._render()

    def _priority_changed(self, _index: int) -> None:
        self._refresh_priority_icon()

    def _pick_due(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("TodoCalendarDialog")
        dialog.setWindowTitle("选择截止时间")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        calendar = QCalendarWidget()
        calendar.setNavigationBarVisible(False)
        calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        calendar.setGridVisible(False)
        header_format = QTextCharFormat()
        header_format.setForeground(QColor(current_tokens().text_secondary))
        calendar.setHeaderTextFormat(header_format)
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            calendar.setWeekdayTextFormat(day, header_format)
        try:
            selected = datetime.strptime(
                self.edit_due.text().strip(), "%Y-%m-%d %H:%M"
            )
        except ValueError:
            selected = datetime.now() + timedelta(days=1)
        calendar.setSelectedDate(
            QDateTime.fromSecsSinceEpoch(int(selected.timestamp())).date()
        )
        month_row = QHBoxLayout()
        previous = QToolButton()
        previous.setObjectName("TodoIconButton")
        previous.setIcon(lucide_icon("chevron-left", 18))
        previous.setIconSize(QSize(18, 18))
        previous.setAccessibleName("上个月")
        previous.setToolTip("上个月")
        previous.clicked.connect(calendar.showPreviousMonth)
        month_row.addWidget(previous)
        month_title = QLabel()
        month_title.setObjectName("TodoCalendarMonth")
        month_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        month_row.addWidget(month_title, 1)
        next_month = QToolButton()
        next_month.setObjectName("TodoIconButton")
        next_month.setIcon(lucide_icon("chevron-right", 18))
        next_month.setIconSize(QSize(18, 18))
        next_month.setAccessibleName("下个月")
        next_month.setToolTip("下个月")
        next_month.clicked.connect(calendar.showNextMonth)
        month_row.addWidget(next_month)
        calendar.currentPageChanged.connect(
            lambda year, month: month_title.setText(f"{year}年{month}月")
        )
        month_title.setText(f"{calendar.yearShown()}年{calendar.monthShown()}月")
        layout.addLayout(month_row)
        layout.addWidget(calendar)
        time_edit = QLineEdit(selected.strftime("%H:%M"))
        time_edit.setPlaceholderText("HH:MM")
        time_edit.setMaxLength(5)
        time_edit.setAccessibleName("截止时间")
        layout.addWidget(time_edit)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(dialog.reject)
        actions.addWidget(cancel)
        confirm = QPushButton("确定")
        confirm.setObjectName("Primary")
        confirm.clicked.connect(dialog.accept)
        actions.addWidget(confirm)
        layout.addLayout(actions)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                selected_time = datetime.strptime(
                    time_edit.text().strip(), "%H:%M"
                )
            except ValueError:
                self._show_feedback("时间格式应为 HH:MM")
                return
            self.edit_due.setText(
                calendar.selectedDate().toString("yyyy-MM-dd")
                + " "
                + selected_time.strftime("%H:%M")
            )

    def _change_status(self, *_args: object) -> None:
        if not self._populating and self._selected_id is not None:
            request_id = self.service.complete_todo(
                self._selected_id,
                bool(self.edit_status.currentData()),
                self._selected_version,
            )
            if request_id:
                self._dirty = False

    def _parse_due(self) -> tuple[bool, float | None]:
        value = self.edit_due.text().strip()
        if not value:
            return True, None
        try:
            return True, datetime.strptime(value, "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            self._show_feedback("截止时间格式应为 YYYY-MM-DD HH:MM")
            self.edit_due.setFocus()
            return False, None

    def _save(self) -> None:
        if self._selected_id is None and not self._editing_new:
            return
        title = self.edit_title.text().strip()
        if not title:
            self.edit_title.setFocus()
            return
        description = self.edit_description.toPlainText().strip()
        if len(description) > 4000:
            self._show_feedback("任务描述不能超过 4000 字")
            return
        valid, due_at = self._parse_due()
        if not valid:
            return
        if self._editing_new:
            if self.service.create_todo(
                title, description, due_at, self.edit_priority.currentData()
            ):
                self.quick_title.clear()
                self._close_detail()
            return
        request_id = self.service.update_todo(
            self._selected_id,
            expected_version=self._selected_version,
            title=title,
            description=description,
            due_at=due_at,
            priority=self.edit_priority.currentData(),
        )
        if request_id:
            self._dirty = False

    def _delete(self) -> None:
        if self._selected_id is None:
            return
        if (
            QMessageBox.question(self, "删除待办", "确定删除这条待办吗？")
            == QMessageBox.StandardButton.Yes
        ):
            self.service.delete_todo(self._selected_id, self._selected_version)

    def _resolve_message(self) -> None:
        if self._selected_id is not None:
            self.service.resolve_todo_message(self._selected_id)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_layout()
