"""Compact AI summary and todo cards for the V2 chat timeline."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .icons import lucide_icon_with_color
from .theme import ThemeSnapshot, active_theme_manager, current_tokens


DEFAULT_VISIBLE_POINTS = 3
MAX_CARD_WIDTH = 620


def _safe_text(value: Any, limit: int = 2000) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def _source_ids(value: Any) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    result: list[int] = []
    for raw in values:
        if isinstance(raw, bool):
            continue
        try:
            clean = int(raw)
        except (TypeError, ValueError):
            continue
        if clean > 0 and clean not in result:
            result.append(clean)
    return result


def _point_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            text = _safe_text(item)
            sources: list[int] = []
        elif isinstance(item, dict):
            text = _safe_text(item.get("text"))
            sources = _source_ids(item.get("sources"))
        else:
            continue
        if text:
            result.append({"text": text, "sources": sources})
            if len(result) >= 20:
                break
    return result


def _message_range(card: dict[str, Any]) -> tuple[int | None, int | None]:
    raw = card.get("range")
    if not isinstance(raw, dict):
        return None, None
    try:
        start = int(raw.get("start_id"))
        end = int(raw.get("end_id"))
    except (TypeError, ValueError):
        return None, None
    return (start, end) if start > 0 and end >= start else (None, None)


def _time_text(card: dict[str, Any]) -> str:
    try:
        stamp = float(card.get("generated_at") or 0)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    now = time.localtime()
    when = time.localtime(stamp)
    if (now.tm_year, now.tm_yday) == (when.tm_year, when.tm_yday):
        return time.strftime("%H:%M", when)
    return time.strftime("%m-%d %H:%M", when)


def _meta_text(card: dict[str, Any]) -> str:
    start, end = _message_range(card)
    parts: list[str] = []
    if start is not None and end is not None:
        parts.append(f"消息 #{start}–#{end}" if start != end else f"消息 #{start}")
    generated = _time_text(card)
    if generated:
        parts.append(generated)
    return " · ".join(parts)


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


class _WrapLabel(QLabel):
    """QLabel whose height follows wrapped text at the current card width."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        if width <= 0 or not self.text():
            return 0
        rect = self.fontMetrics().boundingRect(
            QRect(0, 0, width, 100_000),
            int(Qt.TextFlag.TextWordWrap),
            self.text(),
        )
        return max(rect.height(), self.fontMetrics().height())

    def sizeHint(self):  # type: ignore[override]
        size = super().sizeHint()
        width = self.width() or size.width()
        size.setHeight(self.heightForWidth(width))
        return size

    def minimumSizeHint(self):  # type: ignore[override]
        size = super().minimumSizeHint()
        width = self.width() or size.width()
        size.setHeight(self.heightForWidth(width))
        return size

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        required = self.heightForWidth(self.contentsRect().width())
        if required and required != self.minimumHeight():
            self.setMinimumHeight(required)


class _AgentCardBase(QFrame):
    sources_requested = Signal(list)

    def __init__(self, card: dict[str, Any], icon_name: str, title: str) -> None:
        super().__init__()
        self.card = card
        self._icon_name = icon_name
        self._title = title
        self._expanded = False
        self.setObjectName("AgentCard")
        self.setProperty("cardKind", str(card.get("kind") or ""))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(360)
        self.setMaximumWidth(MAX_CARD_WIDTH)
        self.setAccessibleName(title)

        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(15, 14, 15, 12)
        self.root_layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(7)
        self._icon = QLabel()
        self._icon.setObjectName("CardIcon")
        self._icon.setFixedSize(16, 16)
        header.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("CardTitle")
        self._title_label.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self._title_label)
        self._badge = QLabel("AI 生成")
        self._badge.setObjectName("CardBadge")
        self._badge.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self._badge)
        header.addStretch()
        self.root_layout.addLayout(header)

        meta = _meta_text(card)
        self._meta_label = QLabel(meta)
        self._meta_label.setObjectName("CardMeta")
        self._meta_label.setTextFormat(Qt.TextFormat.PlainText)
        self._meta_label.setVisible(bool(meta))
        self.root_layout.addWidget(self._meta_label)

        self._divider = QFrame()
        self._divider.setObjectName("CardDivider")
        self._divider.setFrameShape(QFrame.Shape.HLine)
        self.root_layout.addWidget(self._divider)

        self.body = QWidget()
        self.body.setObjectName("AgentCardBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        self.root_layout.addWidget(self.body)

        self.footer = QWidget()
        self.footer.setObjectName("AgentCardFooter")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(4)
        self._expand_button = self._action_button("展开全文", "展开卡片全文")
        self._expand_button.clicked.connect(self._toggle_expanded)
        self._source_button = self._action_button("查看来源", "定位总结或待办的来源消息")
        self._source_button.clicked.connect(self._request_sources)
        self._copy_button = self._action_button("复制", "复制卡片内容")
        self._copy_button.clicked.connect(self._copy_to_clipboard)
        footer_layout.addWidget(self._expand_button)
        footer_layout.addWidget(self._source_button)
        footer_layout.addStretch()
        footer_layout.addWidget(self._copy_button)
        self.root_layout.addWidget(self.footer)

        self._refresh_icon()
        manager = active_theme_manager()
        if manager:
            manager.themeChanged.connect(self._theme_changed)

    @staticmethod
    def _action_button(text: str, accessible_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("CardAction")
        button.setAccessibleName(accessible_name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return button

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._render_body()

    def _toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def _request_sources(self) -> None:
        sources = _source_ids(self.card.get("sources"))
        if sources:
            self.sources_requested.emit(sources)

    def _set_actions(self, *, can_expand: bool, can_copy: bool = True) -> None:
        self._expand_button.setVisible(can_expand)
        self._expand_button.setText("收起" if self._expanded else "展开全文")
        show_sources = bool(_source_ids(self.card.get("sources")))
        self._source_button.setVisible(show_sources)
        self._copy_button.setVisible(can_copy)
        self.footer.setVisible(can_expand or show_sources or can_copy)

    def _refresh_icon(self) -> None:
        icon: QIcon = lucide_icon_with_color(
            self._icon_name, current_tokens().agent, 16
        )
        self._icon.setPixmap(icon.pixmap(16, 16))

    def _theme_changed(self, _snapshot: ThemeSnapshot) -> None:
        self._refresh_icon()

    def _copy_to_clipboard(self) -> None:
        text = self._copy_text()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._copy_button.setText("已复制")
        QTimer.singleShot(1200, self._reset_copy_button)

    def _reset_copy_button(self) -> None:
        try:
            self._copy_button.setText("复制")
        except RuntimeError:
            return

    def _copy_text(self) -> str:
        return ""

    def _render_body(self) -> None:
        raise NotImplementedError


class SummaryCard(_AgentCardBase):
    """A neutral, progressively disclosed group-chat summary."""

    def __init__(self, card: dict[str, Any]) -> None:
        title = _shorten(_safe_text(card.get("title"), 80), 40) or "群聊总结"
        super().__init__(card, "file-text", title)
        self._badge.setText("AI 生成")
        self._overview = _safe_text(card.get("overview"), 1200)
        self._conclusions = _point_items(card.get("conclusions"))
        self._pending = _point_items(card.get("pending"))
        self._parse_error = bool(card.get("parse_error"))
        self._render_body()

    def _visible_counts(self) -> tuple[int, int]:
        total_conclusions = len(self._conclusions)
        total_pending = len(self._pending)
        if self._expanded:
            return total_conclusions, total_pending
        conclusion_count = min(total_conclusions, DEFAULT_VISIBLE_POINTS)
        pending_count = min(total_pending, DEFAULT_VISIBLE_POINTS - conclusion_count)
        return conclusion_count, pending_count

    def _render_body(self) -> None:
        _clear_layout(self.body_layout)
        if self._parse_error:
            self._add_empty("结构化内容解析失败，请重新生成。")
            self._set_actions(can_expand=False, can_copy=False)
            return
        overview = self._overview
        if overview:
            shown_overview = overview if self._expanded else _shorten(overview, 220)
            label = _WrapLabel(shown_overview)
            label.setObjectName("CardBody")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.body_layout.addWidget(label)

        conclusion_count, pending_count = self._visible_counts()
        shown_conclusions = self._conclusions[:conclusion_count]
        shown_pending = self._pending[:pending_count]
        if shown_conclusions:
            self._add_section("关键结论")
            for item in shown_conclusions:
                text = item["text"] if self._expanded else _shorten(item["text"], 160)
                self._add_point(text, pending=False)
        elif self._conclusions or self._pending:
            self._add_empty("当前消息信息有限，未形成明确结论。")

        if shown_pending:
            self._add_section("待确认事项")
            for item in shown_pending:
                text = item["text"] if self._expanded else _shorten(item["text"], 160)
                self._add_point(text, pending=True)
        hidden_conclusions = len(self._conclusions) - conclusion_count
        hidden_pending = len(self._pending) - pending_count
        extras: list[str] = []
        if hidden_conclusions:
            extras.append(f"{hidden_conclusions} 条结论")
        if hidden_pending:
            extras.append(f"{hidden_pending} 项待确认")
        if extras:
            self._add_meta("另有 " + "、".join(extras))
        elif not self._conclusions and not self._pending and not overview:
            self._add_empty("当前消息信息有限，未形成明确结论。")

        hidden = hidden_conclusions + hidden_pending
        can_expand = self._expanded or hidden > 0 or len(self._overview) > 220
        self._set_actions(can_expand=can_expand)

    def _add_section(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("CardSectionTitle")
        label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_layout.addWidget(label)

    def _add_empty(self, text: str) -> None:
        label = _WrapLabel(text)
        label.setObjectName("CardEmpty")
        self.body_layout.addWidget(label)

    def _add_meta(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("CardMeta")
        label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_layout.addWidget(label)

    def _add_point(self, text: str, *, pending: bool) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        marker = QLabel("•")
        marker.setObjectName("CardBulletMark")
        marker.setFixedWidth(8)
        marker.setAlignment(Qt.AlignmentFlag.AlignTop)
        body = _WrapLabel(text)
        body.setObjectName("CardPendingPoint" if pending else "SummaryPoint")
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row_layout.addWidget(marker, alignment=Qt.AlignmentFlag.AlignTop)
        row_layout.addWidget(body, 1)
        self.body_layout.addWidget(row)

    def _copy_text(self) -> str:
        if self._parse_error:
            return ""
        lines = [self._title]
        if self._overview:
            lines.append(self._overview)
        if self._conclusions:
            lines.append("关键结论：")
            lines.extend(f"- {item['text']}" for item in self._conclusions)
        if self._pending:
            lines.append("待确认事项：")
            lines.extend(f"- {item['text']}" for item in self._pending)
        return "\n".join(lines)


class TodoCard(_AgentCardBase):
    """Read-only extracted tasks with explicit owner/due metadata only."""

    def __init__(self, card: dict[str, Any]) -> None:
        super().__init__(card, "list-todo", "待办事项")
        self._badge.setText("AI 提取")
        self._tasks = self._task_items(card.get("tasks"))
        self._unresolved = [
            _safe_text(item) for item in card.get("unresolved", []) if _safe_text(item)
        ]
        self._parse_error = bool(card.get("parse_error"))
        meta = _meta_text(card)
        count_text = f"{len(self._tasks)} 项任务"
        self._meta_label.setText(f"{count_text} · {meta}" if meta else count_text)
        self._meta_label.setVisible(True)
        self._render_body()

    @staticmethod
    def _task_items(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        result: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            title = _safe_text(item.get("title"), 400)
            if not title:
                continue
            result.append(
                {
                    "title": title,
                    "owner": _safe_text(item.get("owner"), 80),
                    "due": _safe_text(item.get("due"), 80),
                    "group": "suggested" if item.get("group") == "suggested" else "confirmed",
                }
            )
            if len(result) >= 40:
                break
        return result

    def _visible_task_count(self) -> int:
        return len(self._tasks) if self._expanded else min(len(self._tasks), DEFAULT_VISIBLE_POINTS)

    def _render_body(self) -> None:
        _clear_layout(self.body_layout)
        if self._parse_error:
            self._add_empty("结构化内容解析失败，请重新生成。")
            self._set_actions(can_expand=False, can_copy=False)
            return
        visible_count = self._visible_task_count()
        visible = self._tasks[:visible_count]
        confirmed = [task for task in visible if task["group"] != "suggested"]
        suggested = [task for task in visible if task["group"] == "suggested"]
        if confirmed:
            self._add_section("明确待办")
            for task in confirmed:
                self._add_task(task)
        if suggested:
            self._add_section("建议跟进")
            for task in suggested:
                self._add_task(task)
        if not self._tasks:
            self._add_empty("本次消息中未发现明确待办。")

        shown_unresolved = len(self._unresolved) if self._expanded else 0
        if shown_unresolved:
            self._add_section("待确认信息")
            for value in self._unresolved:
                self._add_point(value if self._expanded else _shorten(value, 160))

        hidden_tasks = len(self._tasks) - visible_count
        extras: list[str] = []
        if hidden_tasks:
            extras.append(f"{hidden_tasks} 项任务")
        if self._unresolved and not self._expanded:
            extras.append(f"{len(self._unresolved)} 项待确认信息")
        if extras:
            self._add_meta("另有 " + "、".join(extras))

        can_expand = self._expanded or hidden_tasks > 0 or bool(self._unresolved)
        self._set_actions(can_expand=can_expand)

    def _add_section(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("CardSectionTitle")
        label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_layout.addWidget(label)

    def _add_empty(self, text: str) -> None:
        label = _WrapLabel(text)
        label.setObjectName("CardEmpty")
        self.body_layout.addWidget(label)

    def _add_meta(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("CardMeta")
        label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_layout.addWidget(label)

    def _add_task(self, task: dict[str, str]) -> None:
        row = QFrame()
        row.setObjectName("CardTaskRow")
        row.setProperty("last", False)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 8)
        layout.setSpacing(3)
        title = _WrapLabel(
            task["title"] if self._expanded else _shorten(task["title"], 120)
        )
        title.setObjectName("CardTaskTitle")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(title)
        meta_parts: list[str] = []
        if task["owner"]:
            meta_parts.append(f"负责人 {task['owner']}")
        if task["due"]:
            meta_parts.append(f"截止 {task['due']}")
        if task["group"] == "suggested":
            meta_parts.append("建议")
        if meta_parts:
            meta = _WrapLabel(" · ".join(meta_parts))
            meta.setObjectName("CardTaskMeta")
            layout.addWidget(meta)
        self.body_layout.addWidget(row)

    def _add_point(self, text: str) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        marker = QLabel("•")
        marker.setObjectName("CardBulletMark")
        marker.setFixedWidth(8)
        body = _WrapLabel(text)
        body.setObjectName("CardPendingPoint")
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(marker, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addWidget(body, 1)
        self.body_layout.addWidget(row)

    def _copy_text(self) -> str:
        if self._parse_error:
            return ""
        lines = ["待办事项"]
        confirmed = [task for task in self._tasks if task["group"] != "suggested"]
        suggested = [task for task in self._tasks if task["group"] == "suggested"]
        if confirmed:
            lines.append("明确待办：")
            for task in confirmed:
                lines.append(f"- {task['title']}")
                if task["owner"]:
                    lines.append(f"  负责人：{task['owner']}")
                if task["due"]:
                    lines.append(f"  截止：{task['due']}")
        if suggested:
            lines.append("建议跟进：")
            lines.extend(f"- {task['title']}" for task in suggested)
        if self._unresolved:
            lines.append("待确认信息：")
            lines.extend(f"- {value}" for value in self._unresolved)
        return "\n".join(lines)
