"""Compact AI summary and todo cards for the V2 chat timeline."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .icons import lucide_icon, lucide_icon_with_color
from .theme import ThemeSnapshot, active_theme_manager, current_tokens
from .time_text import format_message_time, format_time_range


DEFAULT_VISIBLE_POINTS = 3
MAX_CARD_WIDTH = 620
_PRIORITY_LABELS = {"low": "低优先级", "normal": "普通", "high": "高优先级"}


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
    return format_message_time(stamp)


def _meta_text(card: dict[str, Any]) -> str:
    parts: list[str] = []
    period = card.get("period")
    if isinstance(period, dict):
        try:
            period_text = format_time_range(
                float(period.get("start_at") or 0),
                float(period.get("end_at") or 0),
            )
        except (TypeError, ValueError):
            period_text = ""
        if period_text:
            parts.append(period_text)
    try:
        count = int(card.get("message_count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        parts.append(f"{count} 条消息")
    if not parts:
        start, end = _message_range(card)
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


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        clean = int(value)
    except (TypeError, ValueError):
        return None
    return clean if clean > 0 else None


def _task_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        clean = int(value)
    except (TypeError, ValueError):
        return None
    return clean if clean >= 0 else None


def _boolean(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _owner_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.lstrip("@").strip().casefold().split())


def _normalized_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    clean = unicodedata.normalize("NFKC", value)
    clean = clean.strip().casefold()
    clean = re.sub(
        r"[\s\-—_，。,.!?！？:：;；、（）()\[\]【】\"'“”‘’`*#]+", "", clean
    )
    return re.sub(
        r"^(?:去)?(?:参加|出席|准备|完成|处理|跟进|安排|提交|整理)+"
        r"(?:一场|一个|一次)?",
        "",
        clean,
    )


def _title_bigrams(value: Any) -> set[str]:
    clean = _normalized_title(value)
    return {clean[index : index + 2] for index in range(max(0, len(clean) - 1))}


def _similar_title(left: Any, right: Any) -> bool:
    left_key = _normalized_title(left)
    right_key = _normalized_title(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return True
    left_grams = _title_bigrams(left)
    right_grams = _title_bigrams(right)
    if not left_grams or not right_grams:
        return False
    return len(left_grams & right_grams) / min(
        len(left_grams), len(right_grams)
    ) >= 0.62


def _calendar_date_key(due_at: Any, due: Any) -> str:
    stamp = _positive_int(due_at)
    if stamp is not None:
        return datetime.fromtimestamp(stamp).date().isoformat()
    clean = _safe_text(due, 80)
    if not clean:
        return ""
    full_date = re.search(
        r"((?:19|20)\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})",
        clean,
    )
    if full_date:
        try:
            return date(
                int(full_date.group(1)),
                int(full_date.group(2)),
                int(full_date.group(3)),
            ).isoformat()
        except ValueError:
            pass
    short_date = re.search(
        r"(?<!\d)(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?", clean
    )
    if short_date:
        return f"{int(short_date.group(1)):02d}-{int(short_date.group(2)):02d}"
    relative = re.search(r"大后天|后天|明天|今天|昨天", clean)
    if relative:
        return relative.group(0)
    weekday = re.search(r"(?:周|星期|礼拜)[一二三四五六日天]", clean)
    return weekday.group(0) if weekday else ""


def _explicit_time_key(task: dict[str, Any]) -> int | None:
    due = _safe_text(task.get("due"), 80)
    has_clock = bool(
        re.search(
            r"\d{1,2}[:：]\d{2}|[零一二两三四五六七八九十\d]{1,3}\s*(?:点|时)",
            due,
        )
    )
    if due and not has_clock and _calendar_date_key(None, due):
        return None
    stamp = _positive_int(task.get("due_at"))
    if stamp is not None:
        value = datetime.fromtimestamp(stamp)
        return value.hour * 60 + value.minute
    colon_time = re.search(r"(?<!\d)(\d{1,2})[:：](\d{2})", due)
    if colon_time:
        hour = int(colon_time.group(1))
        minute = int(colon_time.group(2))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour * 60 + minute
        return None
    point_time = re.search(r"(?<!\d)(\d{1,2})\s*(?:点|时)(半|\d{1,2}分?)?", due)
    if point_time:
        hour = int(point_time.group(1))
        suffix = point_time.group(2) or ""
        minute = 30 if suffix == "半" else int(suffix.rstrip("分") or 0)
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour * 60 + minute
    return None


def _tasks_same_calendar_day(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = _calendar_date_key(left.get("due_at"), left.get("due"))
    right_key = _calendar_date_key(right.get("due_at"), right.get("due"))
    if left_key and right_key:
        return left_key == right_key
    return not left_key and not right_key


def _tasks_can_merge(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _owner_key(left.get("owner")) != _owner_key(right.get("owner")):
        return False
    if not _tasks_same_calendar_day(left, right):
        return False
    left_title = _normalized_title(left.get("title"))
    right_title = _normalized_title(right.get("title"))
    if not left_title or not right_title:
        return False
    exact_title = left_title == right_title
    if not exact_title and not _similar_title(left.get("title"), right.get("title")):
        return False
    if not exact_title:
        left_date = _calendar_date_key(left.get("due_at"), left.get("due"))
        right_date = _calendar_date_key(right.get("due_at"), right.get("due"))
        if not left_date or not right_date:
            return False
    left_time = _explicit_time_key(left)
    right_time = _explicit_time_key(right)
    if left_time is not None and right_time is not None and left_time != right_time:
        return False
    return True


def _merge_task(target: dict[str, Any], task: dict[str, Any]) -> None:
    target["sources"] = _source_ids([*target.get("sources", []), *task.get("sources", [])])
    target["source_indices"] = sorted(
        {
            int(target["source_index"]),
            *target.get("source_indices", []),
            *task.get("source_indices", []),
        }
    )
    target["todo_ids"] = _source_ids(
        [*target.get("todo_ids", []), *task.get("todo_ids", [])]
    )
    if target.get("todo_id") is None and task.get("todo_id") is not None:
        target["todo_id"] = task["todo_id"]
    if not target.get("sync_status") and task.get("sync_status"):
        target["sync_status"] = task["sync_status"]
    if not target.get("description") and task.get("description"):
        target["description"] = task["description"]
    if not target.get("owner") and task.get("owner"):
        target["owner"] = task["owner"]
        target["owner_id"] = task.get("owner_id")
    target["current_owner"] = bool(target.get("current_owner")) or bool(
        task.get("current_owner")
    )
    if target.get("priority_inferred") and not task.get("priority_inferred"):
        target["priority"] = task["priority"]
        target["priority_inferred"] = False
    if task.get("group") == "confirmed":
        target["group"] = "confirmed"
    target_explicit_time = _explicit_time_key(target) is not None
    task_explicit_time = _explicit_time_key(task) is not None
    if task_explicit_time and not target_explicit_time:
        target["due"] = task.get("due", "")
        target["due_at"] = task.get("due_at")
    elif target.get("due_at") is None and task.get("due_at") is not None:
        target["due_at"] = task["due_at"]
        if not target.get("due"):
            target["due"] = task.get("due", "")
    if target.get("source_message_id") is None:
        target["source_message_id"] = task.get("source_message_id")

    target_completed = bool(target.get("completed"))
    task_completed = bool(task.get("completed"))
    target["completed"] = target_completed and task_completed
    target["extracted_completed"] = bool(target.get("extracted_completed")) and bool(
        task.get("extracted_completed")
    )
    if target["completed"]:
        target["status"] = "已完成"
    else:
        target["status"] = (
            target.get("status")
            if target.get("status") not in {"", "已完成"}
            else task.get("status")
            if task.get("status") not in {"", "已完成"}
            else "待处理"
        )
    target["extracted_status"] = (
        target.get("extracted_status")
        if target.get("extracted_status") not in {"", "已完成"}
        else task.get("extracted_status")
        if task.get("extracted_status") not in {"", "已完成"}
        else "已完成"
        if target["extracted_completed"]
        else "待处理"
    )


def _personal_todo_completion(
    task: dict[str, Any],
    card: dict[str, Any],
    records: list[dict[str, Any]],
) -> bool | None:
    task_todo_ids = set(task.get("todo_ids") or [])
    if task.get("todo_id") is not None:
        task_todo_ids.add(int(task["todo_id"]))
    for record in records:
        todo_id = _record_todo_id(record)
        if todo_id is not None and todo_id in task_todo_ids:
            return _boolean(record.get("completed"))

    expected_key = _todo_idempotency_key(card, task)
    if expected_key:
        for record in records:
            if str(record.get("idempotency_key") or "") == expected_key:
                return _boolean(record.get("completed"))

    source_id = task.get("source_message_id")
    normalized_title = _normalized_title(task.get("title"))
    if source_id is None or not normalized_title:
        return None
    task_date = _calendar_date_key(task.get("due_at"), task.get("due"))
    for record in records:
        if _record_source_id(record) != source_id:
            continue
        if _normalized_title(record.get("title")) != normalized_title:
            continue
        record_date = _calendar_date_key(record.get("due_at"), record.get("due"))
        if task_date and record_date and task_date != record_date:
            continue
        return _boolean(record.get("completed"))
    return None


def _todo_idempotency_key(card: dict[str, Any], task: dict[str, Any]) -> str:
    owner_id = _positive_int(
        card.get("current_user_id", card.get("requester_id", card.get("requested_by")))
    )
    source_id = _positive_int(task.get("source_message_id"))
    conversation_type = _safe_text(card.get("source_conversation_type"), 40)
    conversation_id = _safe_text(card.get("source_conversation_id"), 160)
    if owner_id is None or source_id is None or not conversation_type or not conversation_id:
        return ""
    due_at = task.get("due_at")
    try:
        due_value = str(float(due_at)) if due_at is not None else ""
    except (TypeError, ValueError):
        due_value = ""
    material = "|".join(
        (
            str(owner_id),
            _normalized_title(task.get("title")),
            conversation_type,
            conversation_id,
            str(source_id),
            due_value,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _priority_value(value: Any) -> str:
    clean = str(value or "").strip().casefold()
    if clean in {"high", "urgent", "高", "高优先级"}:
        return "high"
    if clean in {"low", "低", "低优先级"}:
        return "low"
    return "normal"


def _priority_is_inferred(value: Any, *, estimated: Any = None) -> bool:
    if _boolean(estimated):
        return True
    if value is True:
        return True
    clean = str(value or "").strip().casefold()
    return "inferred" in clean or clean in {
        "inferred",
        "inference",
        "estimated",
        "suggested",
        "suggestion",
        "auto",
        "ai",
        "建议",
        "推测",
        "推断",
    }


def _status_text(value: Any) -> str:
    if isinstance(value, bool):
        return "已完成" if value else "待处理"
    clean = _safe_text(value, 60)
    labels = {
        "pending": "待处理",
        "open": "待处理",
        "todo": "待处理",
        "not_started": "待处理",
        "in_progress": "进行中",
        "in-progress": "进行中",
        "doing": "进行中",
        "completed": "已完成",
        "complete": "已完成",
        "done": "已完成",
        "blocked": "受阻",
        "cancelled": "已取消",
        "canceled": "已取消",
    }
    return labels.get(clean.casefold(), clean)


def _current_owner_context(card: dict[str, Any]) -> tuple[set[str], set[int]]:
    names: set[str] = set()
    ids: set[int] = set()

    current_user = card.get("current_user")
    if isinstance(current_user, dict):
        for key in ("display_name", "name", "username"):
            key_value = _owner_key(current_user.get(key))
            if key_value:
                names.add(key_value)
        user_id = _positive_int(current_user.get("id"))
        if user_id is not None:
            ids.add(user_id)
    elif isinstance(current_user, str):
        current_user_name = _owner_key(current_user)
        if current_user_name:
            names.add(current_user_name)

    for key in (
        "current_owner",
        "current_owner_name",
        "current_user_name",
        "current_username",
        "requester_name",
        "requested_by_name",
        "personal_owner_name",
        "viewer_name",
    ):
        key_value = _owner_key(card.get(key))
        if key_value:
            names.add(key_value)
    for key in ("current_user_id", "requester_id", "requested_by", "personal_owner_id"):
        user_id = _positive_int(card.get(key))
        if user_id is not None:
            ids.add(user_id)
    requested_by = _owner_key(card.get("requested_by"))
    if requested_by and _positive_int(card.get("requested_by")) is None:
        names.add(requested_by)
    return names, ids


def _personal_todo_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("todos", "items", "results", "outcomes", "created", "result"):
            nested = value.get(key)
            if isinstance(nested, (list, tuple)):
                return _personal_todo_items(nested)
        payload = value.get("payload")
        if isinstance(payload, (dict, list, tuple)):
            nested_items = _personal_todo_items(payload)
            if nested_items:
                return nested_items
        todo = value.get("todo")
        if isinstance(todo, dict):
            return [todo]
        if any(key in value for key in ("id", "todo_id", "title", "source_message_id")):
            return [value]
        return []
    if isinstance(value, (list, tuple)):
        result: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            nested_todo = item.get("todo")
            result.append(nested_todo if isinstance(nested_todo, dict) else item)
        return result
    return []


def _record_todo_id(record: dict[str, Any]) -> int | None:
    return _positive_int(record.get("todo_id", record.get("id")))


def _record_source_id(record: dict[str, Any]) -> int | None:
    return _positive_int(record.get("source_message_id", record.get("message_id")))


def _import_state(value: Any) -> tuple[str, str]:
    if value is None:
        return "idle", ""
    if isinstance(value, str):
        clean = value.strip().casefold()
        if clean in {"pending", "queued", "importing", "loading"}:
            return "pending", ""
        if clean in {"success", "ok", "succeeded", "completed", "done"}:
            return "success", ""
        if clean in {"failure", "failed", "error"}:
            return "failure", ""
        return "idle", ""
    if not isinstance(value, dict):
        if isinstance(value, (list, tuple)):
            return "success" if any(isinstance(item, dict) for item in value) else "idle", ""
        return "idle", ""

    error = value.get("error")
    detail = _safe_text(error or value.get("detail") or value.get("message"), 160)
    status = str(value.get("status") or value.get("state") or value.get("kind") or "")
    event_type = str(value.get("type") or "").casefold()
    clean = status.strip().casefold()
    if clean == "partial":
        return "partial", detail
    if error or clean in {"failure", "failed", "error"} or event_type == "error":
        return "failure", detail
    if value.get("ok") is False or value.get("success") is False:
        return "failure", detail
    if (
        _boolean(value.get("pending"))
        or clean in {"pending", "queued", "importing", "loading"}
        or event_type in {"pending", "queued", "ack"}
    ):
        return "pending", ""
    if value.get("ok") is True or value.get("success") is True:
        return "success", ""
    if clean in {"success", "ok", "succeeded", "completed", "done"}:
        return "success", ""
    if any(
        key in value
        for key in (
            "todos", "items", "results", "outcomes", "created", "todo_ids", "mapping"
        )
    ):
        return "success", ""
    if value.get("request_id") and not error:
        return "pending", ""
    payload = value.get("payload")
    if isinstance(payload, dict):
        return _import_state(payload)
    return "idle", ""


def _import_mappings(value: Any) -> dict[int, int]:
    result: dict[int, int] = {}
    if not isinstance(value, dict):
        return result

    payload = value.get("payload")
    if isinstance(payload, dict):
        result.update(_import_mappings(payload))

    todo_ids = value.get("todo_ids")
    task_indices = value.get("task_indices")
    if isinstance(todo_ids, (list, tuple)) and isinstance(task_indices, (list, tuple)):
        for raw_index, raw_todo_id in zip(task_indices, todo_ids):
            index = _task_index(raw_index)
            todo_id = _positive_int(raw_todo_id)
            if index is not None and todo_id is not None:
                result[index] = todo_id

    mapping = value.get("mapping")
    if isinstance(mapping, dict):
        for raw_index, raw_value in mapping.items():
            index = _task_index(raw_index)
            if isinstance(raw_value, dict):
                todo_id = _positive_int(raw_value.get("todo_id", raw_value.get("id")))
            else:
                todo_id = _positive_int(raw_value)
            if index is not None and todo_id is not None:
                result[index] = todo_id

    for key in ("todos", "items", "results", "created", "result"):
        nested = value.get(key)
        if not isinstance(nested, (list, tuple)):
            continue
        for item in nested:
            if not isinstance(item, dict):
                continue
            raw_index = item.get("task_index", item.get("index"))
            index = _task_index(raw_index)
            if index is None:
                continue
            todo_id = _record_todo_id(item)
            if todo_id is not None:
                result[index] = todo_id
    return result


class _TaskSelectionButton(QAbstractButton):
    """Custom, theme-aware task selector with no native checkbox indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hovered = False
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(lambda _snapshot: self.update())

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(22, 22)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(22, 22)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isChecked():
            border = (
                tokens.primary_pressed
                if not self.isEnabled() or self.isDown()
                else tokens.primary
            )
            fill = tokens.primary_pressed if not self.isEnabled() else tokens.primary
        elif not self.isEnabled():
            border = tokens.text_tertiary
            fill = tokens.surface_subtle
        else:
            border = tokens.primary if self._hovered else tokens.border_strong
            fill = tokens.surface
        pen_width = 2 if not self.isEnabled() or self.isChecked() else 1.5
        painter.setPen(QPen(QColor(border), pen_width))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(3, 3, 16, 16, 4, 4)
        if self.isChecked():
            check_pen = QPen(QColor(tokens.surface), 2)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            painter.drawLine(7, 11, 10, 14)
            painter.drawLine(10, 14, 16, 8)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.primary), 1))
            painter.drawRoundedRect(1, 1, 20, 20, 5, 5)
        painter.end()


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
        self._footer_layout = footer_layout
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
    """Extracted tasks with compact selection and personal-todo sync state."""

    import_requested = Signal(int, list)
    todo_requested = Signal(int)

    def __init__(self, card: dict[str, Any]) -> None:
        super().__init__(card, "list-todo", "待办事项")
        self._badge.setText("AI 提取")
        self._tasks = self._task_items(card.get("tasks"))
        self._unresolved = [
            _safe_text(item) for item in card.get("unresolved", []) if _safe_text(item)
        ]
        self._parse_error = bool(card.get("parse_error"))
        self._message_id = (
            _positive_int(card.get("message_id"))
            or _positive_int(card.get("agent_message_id"))
            or 0
        )
        self._runtime_owner_names: set[str] = set()
        self._selected: set[int] = set()
        self._user_selected: set[int] = set()
        self._auto_selected: set[int] = set()
        self._synced: dict[int, int] = {}
        self._imported_by_index: dict[int, int] = {}
        self._import_state = "idle"
        self._feedback_text = ""
        self._feedback_state = ""
        self._completed_expanded = False

        self._import_button = self._action_button(
            "加入个人待办 (0)", "将选中的任务加入个人待办"
        )
        self._import_button.setObjectName("CardImportAction")
        self._import_button.clicked.connect(self._request_import)
        self._footer_layout.insertWidget(
            self._footer_layout.count() - 1, self._import_button
        )

        self._sync_default_selection()
        self._meta_label.setVisible(True)
        self._render_body()

    @property
    def selected_task_indices(self) -> list[int]:
        return sorted(self._selected)

    @property
    def import_state(self) -> str:
        return self._import_state

    @staticmethod
    def _task_items(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        result: list[dict[str, Any]] = []
        for source_index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            title = _safe_text(item.get("title"), 400)
            if not title:
                continue
            sources = _source_ids(item.get("sources"))
            source_id = _positive_int(item.get("source_message_id"))
            if source_id is None and sources:
                source_id = sources[0]
            status = _status_text(item.get("status"))
            todo_ids = _source_ids(
                [
                    item.get("todo_id"),
                    item.get("personal_todo_id"),
                    *(
                        item.get("todo_ids")
                        if isinstance(item.get("todo_ids"), (list, tuple, set))
                        else []
                    ),
                ]
            )
            task = {
                "source_index": source_index,
                "source_indices": [source_index],
                "title": title,
                "description": _safe_text(item.get("description"), 1200),
                "owner": _safe_text(item.get("owner"), 80),
                "owner_id": _positive_int(
                    item.get("assignee_user_id", item.get("owner_id"))
                ),
                "due": _safe_text(item.get("due"), 80),
                "due_at": _positive_int(item.get("due_at")),
                "group": "suggested" if item.get("group") == "suggested" else "confirmed",
                "priority": _priority_value(item.get("priority")),
                "priority_inferred": _priority_is_inferred(
                    item.get("priority_basis", item.get("priority_source")),
                    estimated=item.get("priority_estimated"),
                ),
                "status": status,
                "extracted_status": status,
                "completed": status == "已完成",
                "extracted_completed": status == "已完成",
                "source_message_id": source_id,
                "sources": sources,
                "todo_id": todo_ids[0] if todo_ids else None,
                "todo_ids": todo_ids,
                "sync_status": _safe_text(item.get("sync_status"), 40).casefold(),
                "current_owner": any(
                    _boolean(item.get(key))
                    for key in (
                        "is_current_owner",
                        "owned_by_me",
                        "current_owner",
                        "is_mine",
                        "owner_is_current",
                        "owner_is_current_user",
                    )
                ),
            }
            existing = next(
                (candidate for candidate in result if _tasks_can_merge(candidate, task)),
                None,
            )
            if existing is not None:
                _merge_task(existing, task)
                continue
            result.append(task)
        return result

    def _active_tasks(self) -> list[dict[str, Any]]:
        return [task for task in self._tasks if not task.get("completed")]

    def _completed_tasks(self) -> list[dict[str, Any]]:
        return [task for task in self._tasks if task.get("completed")]

    def _update_meta(self) -> None:
        active_count = len(self._active_tasks())
        completed_count = len(self._completed_tasks())
        count_text = f"{active_count} 项待办"
        if completed_count:
            count_text += f" · {completed_count} 项已完成"
        meta = _meta_text(self.card)
        self._meta_label.setText(f"{count_text} · {meta}" if meta else count_text)

    def _owner_matches_current(self, task: dict[str, Any]) -> bool:
        current_names, current_ids = _current_owner_context(self.card)
        current_names.update(self._runtime_owner_names)
        if task["current_owner"]:
            return True
        owner_key = _owner_key(task["owner"])
        if owner_key and owner_key in current_names:
            return True
        return task["owner_id"] is not None and task["owner_id"] in current_ids

    def _is_selectable(self, task: dict[str, Any]) -> bool:
        if task.get("completed"):
            return False
        if task["owner"] and not self._owner_matches_current(task):
            return False
        return True

    def _sync_default_selection(self) -> None:
        for task in self._tasks:
            index = int(task["source_index"])
            if task.get("completed"):
                self._selected.discard(index)
                continue
            if index in self._auto_selected or index in self._user_selected:
                continue
            if not task["owner"] or self._owner_matches_current(task):
                self._auto_selected.add(index)
                if task["owner"] and index not in self._synced:
                    self._selected.add(index)

    def set_runtime_context(
        self,
        message_id: int,
        personal_todos: Any,
        import_result: Any = None,
    ) -> None:
        clean_message_id = _positive_int(message_id)
        if clean_message_id is not None:
            self._message_id = clean_message_id
        records = _personal_todo_items(personal_todos)
        for record in records:
            for key in ("owner_name", "owner_username", "user_name"):
                owner = _owner_key(record.get(key))
                if owner:
                    self._runtime_owner_names.add(owner)

        state, detail = _import_state(import_result)
        if state == "success" and isinstance(import_result, dict):
            self._imported_by_index.update(_import_mappings(import_result))
            records.extend(
                item for item in _personal_todo_items(import_result)
                if _record_todo_id(item) is not None
            )

        self._apply_personal_completion(records)
        self._sync_default_selection()
        self._refresh_synced(records)
        self._import_state = state
        if state == "pending":
            self._feedback_text = "正在加入个人待办…"
            self._feedback_state = "pending"
        elif state == "success":
            self._selected.difference_update(self._synced)
            self._feedback_text = "已加入个人待办，可前往查看。"
            self._feedback_state = "success"
        elif state == "failure":
            self._feedback_text = f"导入失败：{detail}" if detail else "导入失败，请重试。"
            self._feedback_state = "failure"
        elif state == "partial":
            self._selected.difference_update(self._synced)
            self._feedback_text = (
                f"部分任务未加入：{detail}" if detail else "部分任务未加入，请重试。"
            )
            self._feedback_state = "partial"
        else:
            self._feedback_text = ""
            self._feedback_state = ""
        self._render_body()

    def _apply_personal_completion(self, records: list[dict[str, Any]]) -> None:
        for task in self._tasks:
            matched_completion = _personal_todo_completion(task, self.card, records)
            if matched_completion is None:
                task["completed"] = bool(task.get("extracted_completed"))
                task["status"] = (
                    "已完成"
                    if task["completed"]
                    else task.get("extracted_status") or "待处理"
                )
            else:
                task["completed"] = matched_completion
                task["status"] = "已完成" if matched_completion else "待处理"
            if task["completed"]:
                self._selected.discard(int(task["source_index"]))

    def _refresh_synced(self, records: list[dict[str, Any]]) -> None:
        synced: dict[int, int] = {}
        record_ids = {
            todo_id
            for record in records
            if (todo_id := _record_todo_id(record)) is not None
        }
        for task in self._tasks:
            index = int(task["source_index"])
            direct_todo_id = task["todo_id"]
            matched_todo_id = next(
                (
                    todo_id
                    for todo_id in task.get("todo_ids", [])
                    if todo_id in record_ids
                ),
                None,
            )
            if matched_todo_id is not None:
                synced[index] = matched_todo_id
                continue
            if direct_todo_id is not None and task.get("sync_status") in {
                "created",
                "updated",
                "matched",
                "synced",
                "completed",
            }:
                synced[index] = direct_todo_id
                continue
            imported_todo_id = next(
                (
                    imported
                    for source_index in task.get("source_indices", [index])
                    if (imported := self._imported_by_index.get(source_index))
                    is not None
                ),
                None,
            )
            if imported_todo_id is not None:
                synced[index] = imported_todo_id
                continue
            expected_key = _todo_idempotency_key(self.card, task)
            if expected_key:
                keyed = next(
                    (
                        record
                        for record in records
                        if str(record.get("idempotency_key") or "") == expected_key
                    ),
                    None,
                )
                if keyed is not None:
                    todo_id = _record_todo_id(keyed)
                    if todo_id is not None:
                        synced[index] = todo_id
                        continue
            normalized_title = _normalized_title(task["title"])
            source_id = task["source_message_id"]
            if source_id is None or not normalized_title:
                continue
            for record in records:
                if _record_source_id(record) != source_id:
                    continue
                if _normalized_title(record.get("title")) != normalized_title:
                    continue
                task_date = _calendar_date_key(task.get("due_at"), task.get("due"))
                record_date = _calendar_date_key(
                    record.get("due_at"), record.get("due")
                )
                if task_date and record_date and task_date != record_date:
                    continue
                todo_id = _record_todo_id(record)
                if todo_id is not None:
                    synced[index] = todo_id
                    break
        self._synced = synced
        self._selected.difference_update(synced)

    def _selection_changed(self, index: int, checked: bool) -> None:
        task = next(
            (item for item in self._tasks if int(item["source_index"]) == index),
            None,
        )
        if task is None:
            return
        if task.get("completed"):
            self._selected.discard(index)
            self._refresh_import_button()
            return
        self._user_selected.add(index)
        if checked and self._is_selectable(task) and index not in self._synced:
            self._selected.add(index)
        else:
            self._selected.discard(index)
        self._refresh_import_button()

    def _request_import(self) -> None:
        if not self._selected or self._import_state == "pending":
            return
        self.import_requested.emit(self._message_id, sorted(self._selected))

    def _request_todo(self, todo_id: int) -> None:
        if todo_id > 0:
            self.todo_requested.emit(todo_id)

    def _refresh_import_button(self) -> None:
        count = len(self._selected)
        pending = self._import_state == "pending"
        self._import_button.setText("正在导入…" if pending else f"加入个人待办 ({count})")
        self._import_button.setAccessibleName(
            "正在导入到个人待办"
            if pending
            else f"将选中的 {count} 项任务加入个人待办"
        )
        self._import_button.setEnabled(bool(count) and not pending)
        self._import_button.setToolTip(
            "导入完成后即可查看"
            if pending
            else "选择至少一项可加入的任务"
            if not count
            else "将选中任务加入个人待办"
        )

    def _theme_changed(self, snapshot: ThemeSnapshot) -> None:
        super()._theme_changed(snapshot)
        self._render_body()

    def _visible_task_count(self) -> int:
        active_count = len(self._active_tasks())
        return (
            active_count
            if self._expanded
            else min(active_count, DEFAULT_VISIBLE_POINTS)
        )

    def _toggle_completed_expanded(self) -> None:
        self._completed_expanded = not self._completed_expanded
        self._render_body()

    def _render_body(self) -> None:
        _clear_layout(self.body_layout)
        self._update_meta()
        if self._parse_error:
            self._add_empty("结构化内容解析失败，请重新生成。")
            self._set_actions(can_expand=False, can_copy=False)
            self._import_button.hide()
            return
        if self._feedback_text:
            notice = QFrame()
            notice.setObjectName("CardImportNotice")
            notice.setProperty("state", self._feedback_state)
            notice_layout = QHBoxLayout(notice)
            notice_layout.setContentsMargins(8, 4, 8, 4)
            notice_layout.setSpacing(0)
            feedback = _WrapLabel(self._feedback_text)
            feedback.setObjectName("CardImportFeedback")
            notice_layout.addWidget(feedback, 1)
            self.body_layout.addWidget(notice)
        active_tasks = self._active_tasks()
        completed_tasks = self._completed_tasks()
        visible_count = self._visible_task_count()
        visible = active_tasks[:visible_count]
        confirmed = [task for task in visible if task["group"] != "suggested"]
        suggested = [task for task in visible if task["group"] == "suggested"]
        if confirmed:
            self._add_section("明确待办")
            for task in confirmed:
                self._add_task(task, int(task["source_index"]))
        if suggested:
            self._add_section("建议跟进")
            for task in suggested:
                self._add_task(task, int(task["source_index"]))
        if not active_tasks:
            self._add_empty(
                "暂无进行中的待办。" if completed_tasks else "本次消息中未发现明确待办。"
            )

        shown_unresolved = len(self._unresolved) if self._expanded else 0
        if shown_unresolved:
            self._add_section("待确认信息")
            for value in self._unresolved:
                self._add_point(value if self._expanded else _shorten(value, 160))

        if completed_tasks:
            self._add_completed_group(completed_tasks)

        hidden_tasks = len(active_tasks) - visible_count
        extras: list[str] = []
        if hidden_tasks:
            extras.append(f"{hidden_tasks} 项任务")
        if self._unresolved and not self._expanded:
            extras.append(f"{len(self._unresolved)} 项待确认信息")
        if extras:
            self._add_meta("另有 " + "、".join(extras))

        can_expand = self._expanded or hidden_tasks > 0 or bool(self._unresolved)
        self._set_actions(can_expand=can_expand)
        self._import_button.setVisible(bool(active_tasks))
        if self._tasks:
            self.footer.setVisible(True)
        self._refresh_import_button()

    def _add_completed_group(self, tasks: list[dict[str, Any]]) -> None:
        toggle = QPushButton(f"已完成 ({len(tasks)})")
        toggle.setObjectName("CardAction")
        toggle.setProperty("completedToggle", True)
        toggle.setProperty("completedExpanded", self._completed_expanded)
        toggle.setAccessibleName(
            f"{'收起' if self._completed_expanded else '展开'}已完成任务，"
            f"共 {len(tasks)} 项"
        )
        toggle.setToolTip("查看已完成任务")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        toggle.setIcon(
            lucide_icon(
                "chevron-down" if self._completed_expanded else "chevron-right",
                16,
            )
        )
        toggle.setIconSize(QSize(16, 16))
        toggle.clicked.connect(self._toggle_completed_expanded)
        self.body_layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        if not self._completed_expanded:
            return
        for task in tasks:
            self._add_task(task, int(task["source_index"]), completed=True)

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

    def _add_task(
        self, task: dict[str, Any], index: int, *, completed: bool = False
    ) -> None:
        row = QFrame()
        row.setObjectName("CardTaskRow")
        row.setProperty("last", False)
        synced = index in self._synced
        is_completed = completed or bool(task.get("completed"))
        row.setProperty("taskIndex", index)
        row.setProperty("completed", is_completed)
        row.setProperty(
            "syncState",
            "completed" if is_completed else "synced" if synced else "available",
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 7, 0, 8)
        layout.setSpacing(9)

        selector = _TaskSelectionButton()
        selector.setObjectName("CardTaskCheck")
        selector.setProperty("taskIndex", index)
        selector.setProperty("completed", is_completed)
        if is_completed:
            selector.setAccessibleName(f"已完成任务：{task['title']}")
            selector.setChecked(True)
            selector.setEnabled(False)
            selector.setToolTip("已完成任务不可选择或加入个人待办")
        else:
            selector.setAccessibleName(f"选择任务：{task['title']}")
            selectable = self._is_selectable(task) and not synced
            selector.setEnabled(selectable and self._import_state != "pending")
            selector.setChecked(synced or index in self._selected)
            selector.toggled.connect(
                lambda checked, task_index=index: self._selection_changed(
                    task_index, checked
                )
            )
        if is_completed:
            selector.setToolTip("已完成任务不可选择或加入个人待办")
        elif not self._is_selectable(task):
            selector.setToolTip(f"该任务由 {task['owner']} 负责，当前不可选择")
        elif synced:
            selector.setToolTip("该任务已同步到个人待办")
        elif self._import_state == "pending":
            selector.setToolTip("正在导入，请稍候")
        layout.addWidget(selector, 0, Qt.AlignmentFlag.AlignTop)

        content = QWidget()
        content.setObjectName("CardTaskContent")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        title = _WrapLabel(
            task["title"] if self._expanded else _shorten(task["title"], 120)
        )
        title.setObjectName("CardTaskTitle")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(title)
        if task["description"]:
            description_text = (
                task["description"]
                if self._expanded
                else _shorten(task["description"], 180)
            )
            description = _WrapLabel(description_text)
            description.setObjectName("CardTaskDescription")
            description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(description)

        meta_parts: list[str] = []
        if task["owner"]:
            meta_parts.append(f"负责人 {task['owner']}")
        if task["due"]:
            meta_parts.append(f"截止 {task['due']}")
        if meta_parts:
            meta = _WrapLabel(" · ".join(meta_parts))
            meta.setObjectName("CardTaskMeta")
            column.addWidget(meta)

        detail_row = QHBoxLayout()
        detail_row.setContentsMargins(0, 0, 0, 0)
        detail_row.setSpacing(7)
        priority_wrap = QWidget()
        priority_wrap.setObjectName("CardTaskPriorityRow")
        priority_row = QHBoxLayout(priority_wrap)
        priority_row.setContentsMargins(0, 0, 0, 0)
        priority_row.setSpacing(4)
        tokens = current_tokens()
        priority_color = {
            "high": tokens.danger,
            "normal": tokens.warning,
            "low": tokens.primary,
        }[task["priority"]]
        flag = QLabel()
        flag.setObjectName("CardTaskPriorityFlag")
        flag.setFixedSize(14, 14)
        flag.setAccessibleName("优先级")
        flag_icon = lucide_icon_with_color("flag", priority_color, 14)
        flag.setPixmap(flag_icon.pixmap(14, 14))
        priority_row.addWidget(flag)
        priority = QLabel(_PRIORITY_LABELS[task["priority"]])
        priority.setObjectName("CardTaskPriority")
        priority.setProperty("priority", task["priority"])
        priority.setTextFormat(Qt.TextFormat.PlainText)
        priority_row.addWidget(priority)
        detail_row.addWidget(priority_wrap)
        if task["priority_inferred"]:
            basis = QLabel("建议")
            basis.setObjectName("CardTaskPriorityBasis")
            basis.setTextFormat(Qt.TextFormat.PlainText)
            basis.setAccessibleName("优先级为建议值")
            detail_row.addWidget(basis)
        status_text = "已完成" if is_completed else task["status"]
        if status_text:
            status = QLabel(status_text)
            status.setObjectName("CardTaskStatus")
            status.setTextFormat(Qt.TextFormat.PlainText)
            detail_row.addWidget(status)
        detail_row.addStretch(1)
        column.addLayout(detail_row)

        if synced:
            sync_row = QHBoxLayout()
            sync_row.setContentsMargins(0, 1, 0, 0)
            sync_row.setSpacing(7)
            sync_state = QLabel("已同步")
            sync_state.setObjectName("CardTaskSyncState")
            sync_state.setTextFormat(Qt.TextFormat.PlainText)
            sync_row.addWidget(sync_state)
            todo_id = self._synced[index]
            todo_action = self._action_button(
                "查看待办", f"查看已同步的个人待办：{task['title']}"
            )
            todo_action.setObjectName("CardTaskTodoAction")
            todo_action.clicked.connect(
                lambda _checked=False, clean_id=todo_id: self._request_todo(clean_id)
            )
            sync_row.addWidget(todo_action)
            sync_row.addStretch(1)
            column.addLayout(sync_row)
        layout.addWidget(content, 1)
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
        active_tasks = self._active_tasks()
        completed_tasks = self._completed_tasks()
        confirmed = [task for task in active_tasks if task["group"] != "suggested"]
        suggested = [task for task in active_tasks if task["group"] == "suggested"]
        if confirmed:
            lines.append("明确待办：")
            for task in confirmed:
                self._append_task_copy(lines, task)
        if suggested:
            lines.append("建议跟进：")
            for task in suggested:
                self._append_task_copy(lines, task)
        if self._unresolved:
            lines.append("待确认信息：")
            lines.extend(f"- {value}" for value in self._unresolved)
        if completed_tasks:
            lines.append("已完成：")
            for task in completed_tasks:
                self._append_task_copy(lines, task)
        return "\n".join(lines)

    def _append_task_copy(self, lines: list[str], task: dict[str, Any]) -> None:
        lines.append(f"- {task['title']}")
        if task["description"]:
            lines.append(f"  描述：{task['description']}")
        if task["owner"]:
            lines.append(f"  负责人：{task['owner']}")
        if task["due"]:
            lines.append(f"  截止：{task['due']}")
        priority = _PRIORITY_LABELS[task["priority"]]
        if task["priority_inferred"]:
            priority += "（建议）"
        lines.append(f"  优先级：{priority}")
        status_text = "已完成" if task.get("completed") else task["status"]
        if status_text:
            lines.append(f"  状态：{status_text}")
        if int(task["source_index"]) in self._synced:
            lines.append("  同步：已同步到个人待办")
