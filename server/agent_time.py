"""Time-window parsing used by the Agent runtime."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta


TIME_RANGE_LIMIT = 200

@dataclass(frozen=True)
class TimeWindow:
    start_at: float
    end_at: float
    label: str

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_PERIOD_BOUNDS = {
    "凌晨": (0, 6),
    "早上": (5, 9),
    "上午": (8, 12),
    "中午": (11, 13),
    "下午": (12, 18),
    "傍晚": (17, 20),
    "晚上": (18, 24),
}

_CLOCK_TOKEN = (
    r"(?:\d{1,2}\s*[:：]\s*\d{1,2}|"
    r"\d{1,2}\s*[点时](?:\s*半)?|"
    r"[零一二两三四五六七八九十]{1,3}\s*[点时](?:\s*半)?)"
)

_CLOCK_RANGE_RE = re.compile(
    rf"(?P<a>{_CLOCK_TOKEN})\s*(?:到|至|~|～|—|－|–)\s*(?P<b>{_CLOCK_TOKEN})"
)

_DATE_TOKEN = (
    r"(?:\d{4}年\s*\d{1,2}月\s*\d{1,2}[日号]|"
    r"\d{1,2}月\s*\d{1,2}[日号]|"
    r"\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}/\d{1,2})"
)

_DATE_RANGE_RE = re.compile(
    rf"(?P<a>{_DATE_TOKEN})\s*(?P<at>{_CLOCK_TOKEN})?\s*"
    rf"(?:到|至|~|～|—|－|–)\s*"
    rf"(?:(?P<b>{_DATE_TOKEN})|(?P<bd>\d{{1,2}}\s*[日号]))\s*(?P<bt>{_CLOCK_TOKEN})?"
)

_DATE_ONLY_RE = re.compile(_DATE_TOKEN)

_RELATIVE_DAY_RE = re.compile(r"(?P<day>大前天|今天|今日|昨天|昨日|前天)")

_RECENT_RE = re.compile(
    r"(?:最近|近|过去)\s*(?P<count>\d+|[零一二两三四五六七八九十]+)\s*"
    r"(?P<unit>小时|天|日|周|星期|个月|月)"
)

def _integer(value: str) -> int | None:
    clean = value.strip()
    if clean.isdigit():
        return int(clean)
    if not clean or any(char not in _CN_DIGITS and char != "十" for char in clean):
        return None
    if "十" not in clean:
        result = 0
        for char in clean:
            result = result * 10 + _CN_DIGITS[char]
        return result
    tens_text, _, ones_text = clean.partition("十")
    tens = _CN_DIGITS.get(tens_text, 1) if tens_text else 1
    ones = _CN_DIGITS.get(ones_text, 0) if ones_text else 0
    return tens * 10 + ones

def _start_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)

def _detect_period(text: str) -> str | None:
    for word in _PERIOD_BOUNDS:
        if word in text:
            return word
    return None

def _parse_clock(text: str, period: str | None) -> tuple[int, int] | None:
    clean = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"(\d{1,2})[:：](\d{1,2})", clean)
    half = False
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
    else:
        match = re.fullmatch(r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})[点时](半)?", clean)
        if not match:
            return None
        hour = _integer(match.group(1)) or 0
        half = bool(match.group(2))
        minute = 30 if half else 0
    if period in {"下午", "傍晚", "晚上"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period == "凌晨" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour, minute

def _parse_date(text: str, now: datetime) -> datetime | None:
    clean = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]", clean)
    if match:
        year = int(match.group(1)) if match.group(1) else now.year
        month = int(match.group(2))
        day = int(match.group(3))
    else:
        match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", clean)
        if match:
            year, month, day = (int(match.group(index)) for index in (1, 2, 3))
        else:
            match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", clean)
            if not match:
                return None
            year, month, day = now.year, int(match.group(1)), int(match.group(2))
    try:
        return datetime(year, month, day)
    except ValueError:
        return None

def _clock_bounds(
    day: datetime, first: tuple[int, int], last: tuple[int, int]
) -> tuple[datetime, datetime]:
    start = day.replace(hour=first[0], minute=first[1])
    end = day.replace(hour=last[0], minute=last[1])
    if end <= start:
        end += timedelta(days=1)
    return start, end

def _date_text(when: datetime, now: datetime) -> str:
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

def _window_label(start: datetime, end: datetime, now: datetime) -> str:
    if (
        start.hour == 0
        and start.minute == 0
        and end.hour == 0
        and end.minute == 0
    ):
        if end.date() == (start + timedelta(days=1)).date():
            return f"{_date_text(start, now)}（全天）"
        if end.date() > start.date():
            last = end - timedelta(days=1)
            start_text = (
                f"{start.month}月{start.day}日"
                if start.year == now.year
                else f"{start.year}年{start.month}月{start.day}日"
            )
            end_text = (
                f"{last.month}月{last.day}日"
                if last.year == now.year
                else f"{last.year}年{last.month}月{last.day}日"
            )
            return f"{start_text}–{end_text}"
    start_clock = f"{start.hour:02d}:{start.minute:02d}"
    end_clock = f"{end.hour:02d}:{end.minute:02d}"
    if start.date() == end.date():
        return f"{_date_text(start, now)} {start_clock}–{end_clock}"
    return f"{_date_text(start, now)} {start_clock}–{_date_text(end, now)} {end_clock}"

def window_from_bounds(start: datetime, end: datetime, now: datetime) -> TimeWindow:
    if end <= start:
        end = start + timedelta(hours=1)
    if start <= now < end:
        end = now
    return TimeWindow(
        start_at=start.timestamp(),
        end_at=end.timestamp(),
        label=_window_label(start, end, now),
    )

def parse_time_window(prompt: str, now: float | None = None) -> TimeWindow | None:
    clean = prompt.strip()
    moment = datetime.fromtimestamp(now if now is not None else time.time())

    match = _DATE_RANGE_RE.search(clean)
    if match:
        start_date = _parse_date(match.group("a"), moment)
        end_date = (
            _parse_date(match.group("b"), moment) if match.group("b") else None
        )
        if start_date and end_date is None and match.group("bd"):
            day_number = int(re.sub(r"\D", "", match.group("bd")))
            try:
                end_date = start_date.replace(day=day_number)
            except ValueError:
                end_date = None
            if end_date is not None and end_date < start_date:
                year = start_date.year + (1 if start_date.month == 12 else 0)
                month = 1 if start_date.month == 12 else start_date.month + 1
                try:
                    end_date = end_date.replace(year=year, month=month)
                except ValueError:
                    end_date = None
        if start_date and end_date:
            period = _detect_period(clean)
            start = start_date
            end = end_date + timedelta(days=1)
            if match.group("at"):
                clock = _parse_clock(match.group("at"), period)
                if clock:
                    start = start_date.replace(hour=clock[0], minute=clock[1])
            if match.group("bt"):
                clock = _parse_clock(match.group("bt"), period)
                if clock:
                    end = end_date.replace(hour=clock[0], minute=clock[1])
                    if end <= start:
                        end += timedelta(days=1)
            return window_from_bounds(start, end, moment)
        return None

    match = _DATE_ONLY_RE.search(clean)
    if match:
        day = _parse_date(match.group(0), moment)
        if day:
            period = _detect_period(clean)
            clock_range = _CLOCK_RANGE_RE.search(clean)
            if clock_range:
                first = _parse_clock(clock_range.group("a"), period)
                last = _parse_clock(clock_range.group("b"), period)
                if first and last:
                    start, end = _clock_bounds(day, first, last)
                    return window_from_bounds(
                        start,
                        end,
                        moment,
                    )
            single = re.search(_CLOCK_TOKEN, clean)
            if single:
                clock = _parse_clock(single.group(0), period)
                if clock:
                    start = day.replace(hour=clock[0], minute=clock[1])
                    return window_from_bounds(
                        start, start + timedelta(hours=1), moment
                    )
            if period:
                start_hour, end_hour = _PERIOD_BOUNDS[period]
                return window_from_bounds(
                    day.replace(hour=start_hour),
                    day.replace(hour=0) + timedelta(days=1)
                    if end_hour == 24
                    else day.replace(hour=end_hour),
                    moment,
                )
            return window_from_bounds(
                day, day + timedelta(days=1), moment
            )

    match = _RELATIVE_DAY_RE.search(clean)
    if match:
        offset = {
            "今天": 0,
            "今日": 0,
            "昨天": -1,
            "昨日": -1,
            "前天": -2,
            "大前天": -3,
        }[match.group("day")]
        day = _start_of_day(moment) + timedelta(days=offset)
        tail = clean[match.end() :]
        period = _detect_period(tail)
        clock_range = _CLOCK_RANGE_RE.search(tail)
        if clock_range:
            first = _parse_clock(clock_range.group("a"), period)
            last = _parse_clock(clock_range.group("b"), period)
            if first and last:
                start, end = _clock_bounds(day, first, last)
                return window_from_bounds(
                    start,
                    end,
                    moment,
                )
        single = re.search(_CLOCK_TOKEN, tail)
        if single:
            clock = _parse_clock(single.group(0), period)
            if clock:
                start = day.replace(hour=clock[0], minute=clock[1])
                return window_from_bounds(
                    start, start + timedelta(hours=1), moment
                )
        if period:
            start_hour, end_hour = _PERIOD_BOUNDS[period]
            end = (
                day.replace(hour=0) + timedelta(days=1)
                if end_hour == 24
                else day.replace(hour=end_hour)
            )
            return window_from_bounds(
                day.replace(hour=start_hour), end, moment
            )
        return window_from_bounds(
            day, day + timedelta(days=1), moment
        )

    if re.search(r"(?:本周|这周|本星期|这个星期)", clean):
        monday = _start_of_day(moment) - timedelta(days=moment.weekday())
        return window_from_bounds(
            monday, monday + timedelta(days=7), moment
        )
    if re.search(r"上周", clean):
        monday = _start_of_day(moment) - timedelta(days=moment.weekday())
        return window_from_bounds(
            monday - timedelta(days=7), monday, moment
        )
    if re.search(r"(?:本月|这个月)", clean):
        first = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return window_from_bounds(
            first, first + timedelta(days=32), moment
        )
    if re.search(r"上月", clean):
        first = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous = (first - timedelta(days=1)).replace(day=1)
        return window_from_bounds(previous, first, moment)

    match = _RECENT_RE.search(clean)
    if match:
        count = _integer(match.group("count")) or 0
        if count > 0:
            unit = match.group("unit")
            if unit == "小时":
                return window_from_bounds(
                    moment - timedelta(hours=count), moment, moment
                )
            if unit in {"天", "日"}:
                start = _start_of_day(moment) - timedelta(days=count - 1)
                return window_from_bounds(start, moment, moment)
            if unit in {"周", "星期"}:
                start = _start_of_day(moment) - timedelta(days=count * 7 - 1)
                return window_from_bounds(start, moment, moment)
            start = _start_of_day(moment) - timedelta(days=count * 30 - 1)
            return window_from_bounds(start, moment, moment)
    return None

def task_from_prompt(
    prompt: str, now: float | None = None
) -> tuple[str, int, str | None, TimeWindow | None]:
    clean = prompt.strip()
    count_match = re.search(r"最近\s*(\d+)\s*条", clean)
    explicit_count = (
        min(200, max(1, int(count_match.group(1)))) if count_match else None
    )
    count = explicit_count or 50
    search_match = re.fullmatch(r"(?:请\s*)?搜索\s+(.+)", clean, re.IGNORECASE)
    if search_match:
        return "search", count, search_match.group(1).strip(), None
    identity_patterns = (
        r"(?:你|当前|现在).{0,12}(?:什么|哪个|哪种).{0,6}(?:模型|供应商|provider)",
        r"(?:模型|供应商|provider).{0,8}(?:是什么|是哪个|是哪种)",
        r"(?:当前|现在).{0,8}(?:使用|运行).{0,8}(?:模型|供应商|provider)",
    )
    if any(re.search(pattern, clean, re.IGNORECASE) for pattern in identity_patterns):
        return "identity", count, None, None
    if re.search(r"(?:提取|整理|列出|生成).{0,20}(?:待办|todo)", clean, re.IGNORECASE):
        window = parse_time_window(clean, now)
        if window and explicit_count is None:
            count = TIME_RANGE_LIMIT
        return "todo", count, None, window
    history_terms = (
        r"(?:最近\s*\d*\s*条|聊天记录|聊天|消息|对话|会话|本群|今天|今日|昨天|昨日|"
        r"前天|本周|上周|最近\s*\d+\s*(?:天|日|周|小时)|\d{1,2}月\d{1,2}日)"
    )
    if re.search(rf"(?:总结|摘要).{{0,12}}{history_terms}", clean) or re.search(
        rf"{history_terms}.{{0,12}}(?:总结|摘要)", clean
    ):
        window = parse_time_window(clean, now)
        if window and explicit_count is None:
            count = TIME_RANGE_LIMIT
        return "summary", count, None, window
    return "question", count, None, None
