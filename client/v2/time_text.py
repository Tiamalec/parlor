"""Local-time labels shared by the message timeline and AI cards."""

from __future__ import annotations

from datetime import datetime, timedelta


def _moment(stamp: float) -> datetime:
    return datetime.fromtimestamp(float(stamp))


def _now_moment(now: float | None) -> datetime:
    return _moment(now if now is not None else datetime.now().timestamp())


def _date_label(when: datetime, now: datetime) -> str:
    day = when.date()
    today = now.date()
    if day == today:
        return "今天"
    if day == today - timedelta(days=1):
        return "昨天"
    if day == today - timedelta(days=2):
        return "前天"
    if when.year == now.year:
        return f"{when.month}月{when.day}日"
    return f"{when.year}年{when.month}月{when.day}日"


def _clock(when: datetime) -> str:
    return f"{when.hour:02d}:{when.minute:02d}"


def format_message_time(stamp: float, now: float | None = None) -> str:
    """Format a message timestamp the way the chat timeline shows it."""

    if not stamp:
        return ""
    when = _moment(stamp)
    reference = _now_moment(now)
    return f"{_date_label(when, reference)} {_clock(when)}"


def format_time_range(start: float, end: float, now: float | None = None) -> str:
    """Format a processed message range for AI card metadata."""

    if not start or not end:
        return ""
    first = _moment(start)
    last = _moment(end)
    reference = _now_moment(now)
    if last - first < timedelta(minutes=1):
        return format_message_time(start, now)
    if first.date() == last.date():
        return f"{_date_label(first, reference)} {_clock(first)}–{_clock(last)}"
    return f"{format_message_time(start, now)}–{format_message_time(end, now)}"
