"""Structured AI cards for chat summaries and extracted todos.

The model is asked for JSON, but older models and historical messages still use
Markdown.  This module validates both shapes and always produces a compact
Markdown fallback for clients that do not understand the card metadata.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Iterable


SUMMARY_TITLE = "群聊总结"
SCHEDULED_SUMMARY_TITLE = "定时群聊总结"
TODO_TITLE = "待办事项"

# The agent runtime owns the surrounding system instructions.  Keeping the
# structured todo contract here lets it import the same shape that this module
# validates, including optional fields and the provenance rules for due dates.
TODO_JSON_SCHEMA_PROMPT = (
    '{"tasks":[{"title":"任务标题","description":"说明（可选）",'
    '"owner":"明确负责人（可选）","due":"原文期限（可选）",'
    '"due_at":"ISO 8601 时间（仅在消息支持时）",'
    '"priority":"low/normal/high（可选）",'
    '"priority_basis":"explicit/inferred/default",'
    '"status":"open/completed","group":"confirmed/suggested",'
    '"sources":[消息编号],"source_message_id":消息编号,'
    '"todo_id":现有待办ID（可选）,"sync_status":现有同步状态（可选）}],'
    '"unresolved":["待确认信息"]}。'
    "只输出依据消息能够确认的事实；未知字段省略，不得编造负责人、期限或完成状态。"
    "priority 可以给出建议，但必须用 priority_basis 标明是显式、推断还是默认值。"
    "due_at 只在能够从消息或明确 ISO 8601 日期可靠换算为时间时提供。"
    "status 必须依据明确的完成或进行中语境填写。"
    "todo_id 和 sync_status 只在透传已有数据时保留，不得为了填写字段而创建或推测值。"
)

_PLACEHOLDERS = {
    "",
    "-",
    "—",
    "无",
    "暂无",
    "未知",
    "未明确",
    "未提及",
    "未指定",
    "不明确",
    "待定",
    "待确认",
    "n/a",
    "na",
    "none",
    "null",
}
_UNCERTAIN_MARKERS = (
    "可能",
    "也许",
    "或许",
    "推测",
    "似乎",
    "大概",
    "估计",
    "不确定",
    "尚未确认",
    "尚未确定",
    "尚未决定",
    "未决定",
    "还没定",
    "待确认",
    "待定",
)
_META_REQUEST = re.compile(
    r"(?:请|能否|可以|帮我|麻烦)?\s*(?:总结|摘要|提取|整理|列出|生成).{0,16}(?:聊天|消息|记录|对话|待办)",
    re.IGNORECASE,
)
_QUESTION_START = re.compile(
    r"^(?:是否|能否|要不要|有没有|如何|怎么|为什么|为何|何时|谁|哪些|什么|请问)"
)
_USER_QUESTION = re.compile(
    r"^(?:用户|有人|成员|群友|大家).{0,8}(?:问|询问|提出|想知道|疑惑)"
)
_ACTION_START = re.compile(
    r"^(?:确认|核实|检查|跟进|讨论|确定|评估|决定|安排|准备|完成|提供|补充|更新|联系|修复|整理|发布|评审|测试|部署|发送|上传|提交)"
)
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
_SELF_COMMITMENT = re.compile(
    r"我(?P<due>今天|明天|后天|本周[一二三四五六日天]?|下周[一二三四五六日天]?)?"
    r"(?P<time>(?:凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*"
    r"(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*(?:点|时)"
    r"(?:半|[零一二两三四五六七八九十\d]{1,3}\s*分)?)?"
    r"(?:(?:要|会|计划)(?:去)?|(?:还)?有(?:一场|一个|一次)?)"
    r"(?P<title>(?:(?:参加|开|完成|准备|提交|发送|整理|跟进|核实|检查|确认|修复|更新|发布|测试|部署|联系|安排|提供|补充|评审)[^，。！？,!?；;]{1,70}|"
    r"[^，。！？,!?；;]{1,24}(?:会议|活动|面试|面谈|汇报|体检|出差|约会|行程)))"
)


def _clean_text(value: Any, limit: int = 600) -> str:
    if not isinstance(value, str):
        return ""
    clean = re.sub(r"\s+", " ", value).strip()
    clean = clean.strip("-*• \t")
    clean = re.sub(r"^#+\s*", "", clean)
    return clean[:limit].strip()


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in _PLACEHOLDERS


def _norm_key(value: str) -> str:
    clean = value.lower()
    clean = re.sub(r"[`*#>\-•\s，。,.!?！？:：;；、（）()\[\]【】\"'“”‘’]+", "", clean)
    return clean


def _dedupe_title_key(value: Any) -> str:
    clean = _norm_key(str(value or ""))
    return re.sub(
        r"^(?:去)?(?:参加|出席|准备|完成|处理|跟进|安排|提交|整理)+"
        r"(?:一场|一个|一次)?",
        "",
        clean,
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_text(value)
        if not clean or _is_placeholder(clean):
            continue
        key = _norm_key(clean)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _looks_like_question(text: str) -> bool:
    clean = text.strip()
    return clean.endswith(("?", "？")) or bool(_QUESTION_START.match(clean))


def _looks_uncertain(text: str) -> bool:
    return any(marker in text for marker in _UNCERTAIN_MARKERS)


def _looks_meta_request(text: str) -> bool:
    return bool(_META_REQUEST.search(text)) and any(
        marker in text for marker in ("请", "帮我", "你", "能否", "可以")
    )


def _bigrams(value: str) -> set[str]:
    clean = _norm_key(value)
    return {clean[index : index + 2] for index in range(max(0, len(clean) - 1))}


def _similar_text(left: str, right: str) -> bool:
    left_key = _norm_key(left)
    right_key = _norm_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    if len(shorter) >= 8 and shorter in longer:
        return True
    left_grams = _bigrams(left)
    right_grams = _bigrams(right)
    if not left_grams or not right_grams:
        return False
    overlap = len(left_grams & right_grams)
    return overlap / min(len(left_grams), len(right_grams)) >= 0.62


def _range_for(messages: list[dict[str, Any]]) -> dict[str, int] | None:
    ids: list[int] = []
    for message in messages:
        try:
            ids.append(int(message.get("id")))
        except (TypeError, ValueError):
            continue
    if not ids:
        return None
    return {"start_id": min(ids), "end_id": max(ids)}


def _period_for(messages: list[dict[str, Any]]) -> dict[str, float] | None:
    stamps: list[float] = []
    for message in messages:
        try:
            stamp = float(message.get("created_at", message.get("timestamp", 0)) or 0)
        except (TypeError, ValueError):
            continue
        if stamp > 10_000_000_000:
            stamp /= 1000
        if stamp > 0:
            stamps.append(stamp)
    if not stamps:
        return None
    return {"start_at": min(stamps), "end_at": max(stamps)}


def _message_ids(messages: list[dict[str, Any]]) -> set[int]:
    ids: set[int] = set()
    for message in messages:
        try:
            ids.add(int(message.get("id")))
        except (TypeError, ValueError):
            continue
    return ids


def _source_ids(value: Any, valid_ids: set[int]) -> list[int]:
    raw_values: list[Any]
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]
    result: list[int] = []
    for raw in raw_values:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            candidates = [raw]
        elif isinstance(raw, str):
            candidates = [int(token) for token in re.findall(r"\d+", raw)]
        else:
            candidates = []
        for candidate in candidates:
            if candidate in valid_ids and candidate not in result:
                result.append(candidate)
    return result


def _as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return [value]
    return []


def _item_text_sources(
    item: Any, valid_ids: set[int]
) -> tuple[str, list[int]]:
    if isinstance(item, str):
        return _clean_text(item), []
    if not isinstance(item, dict):
        return "", []
    text = ""
    for key in ("text", "title", "content", "summary", "point", "task"):
        candidate = item.get(key)
        if isinstance(candidate, str) and candidate.strip():
            text = _clean_text(candidate)
            break
    raw_sources = item.get("sources", item.get("source", item.get("source_message_id")))
    return text, _source_ids(raw_sources, valid_ids)


def extract_json_object(source: str) -> dict[str, Any] | None:
    """Extract one JSON object without accepting arbitrary surrounding HTML."""

    clean = source.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```\s*$", "", clean)
    try:
        value = json.loads(clean)
    except (TypeError, ValueError):
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(clean[start : end + 1])
        except (TypeError, ValueError):
            # Some providers append a second object or trailing commentary even
            # when JSON mode is requested. Decode the first complete object and
            # leave the rest untouched instead of discarding the whole answer.
            decoder = json.JSONDecoder()
            for match in re.finditer(r"\{", clean):
                try:
                    candidate, _end = decoder.raw_decode(clean[match.start() :])
                except (TypeError, ValueError):
                    continue
                if isinstance(candidate, dict):
                    return candidate
            return None
    return value if isinstance(value, dict) else None


def _parse_legacy_summary(text: str) -> dict[str, Any]:
    overview_parts: list[str] = []
    conclusions: list[str] = []
    pending: list[str] = []
    current = ""
    aliases = {
        "主要话题": "topic",
        "主题": "topic",
        "核心结论": "conclusion",
        "结论": "conclusion",
        "待确认事项": "pending",
        "未决问题": "pending",
        "待确认": "pending",
    }
    for raw_line in text.replace("\r\n", "\n").splitlines():
        raw = raw_line.strip()
        if not raw:
            continue
        heading = re.match(r"^#{1,6}\s*(.+?)\s*$", raw)
        heading_text = heading.group(1).strip(" *#：:") if heading else ""
        if heading_text:
            if heading_text in aliases:
                current = aliases[heading_text]
            continue
        clean = _clean_text(raw)
        if not clean:
            continue
        match = re.match(
            r"^(主要话题|主题|核心结论|结论|待确认事项|未决问题|待确认)\s*[：:]\s*(.*)$",
            clean,
        )
        if match:
            bucket = aliases[match.group(1)]
            value = _clean_text(match.group(2))
            if not _is_placeholder(value):
                if bucket == "topic":
                    overview_parts.append(value)
                elif bucket == "conclusion":
                    conclusions.append(value)
                else:
                    pending.append(value)
            continue
        if _is_placeholder(clean):
            continue
        if current == "topic":
            overview_parts.append(clean)
        elif current == "pending":
            pending.append(clean)
        elif current == "conclusion":
            conclusions.append(clean)
        else:
            conclusions.append(clean)
    return {
        "overview": "；".join(_dedupe(overview_parts)),
        "conclusions": conclusions,
        "pending": pending,
    }


def _normalize_summary(
    payload: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    scheduled: bool,
    generated_at: float | None,
    parse_error: bool = False,
) -> dict[str, Any]:
    valid_ids = _message_ids(messages)
    overview = _clean_text(
        payload.get("overview")
        or payload.get("summary")
        or payload.get("概述")
        or payload.get("主要话题"),
        500,
    )
    conclusions_raw = _as_items(
        payload.get("conclusions")
        or payload.get("key_points")
        or payload.get("points")
        or payload.get("核心结论")
    )
    pending_raw = _as_items(
        payload.get("pending")
        or payload.get("open_questions")
        or payload.get("unresolved")
        or payload.get("待确认事项")
    )
    conclusions: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    seen_conclusions: set[str] = set()
    seen_pending: set[str] = set()

    for item in conclusions_raw:
        if len(conclusions) >= 12:
            break
        text, sources = _item_text_sources(item, valid_ids)
        if (
            not text
            or _is_placeholder(text)
            or _looks_meta_request(text)
            or _USER_QUESTION.match(text)
        ):
            continue
        if _looks_like_question(text) or _looks_uncertain(text):
            key = _norm_key(text)
            if key and key not in seen_pending:
                seen_pending.add(key)
                pending.append({"text": text, "sources": sources})
            continue
        key = _norm_key(text)
        if not key or key in seen_conclusions:
            continue
        seen_conclusions.add(key)
        conclusions.append({"text": text, "sources": sources})

    for item in pending_raw:
        if len(pending) >= 12:
            break
        text, sources = _item_text_sources(item, valid_ids)
        if not text or _is_placeholder(text) or _looks_meta_request(text):
            continue
        key = _norm_key(text)
        if not key or key in seen_pending:
            continue
        # The same event must not appear as both a settled conclusion and an
        # open question.  Unresolved wording wins; otherwise the conclusion wins.
        similar = next(
            (
                item
                for item in conclusions
                if _similar_text(item["text"], text)
            ),
            None,
        )
        if similar is not None:
            if _looks_uncertain(text) or _looks_like_question(text):
                conclusions = [item for item in conclusions if item is not similar]
                seen_conclusions.discard(_norm_key(similar["text"]))
            else:
                continue
        seen_pending.add(key)
        pending.append({"text": text, "sources": sources})

    if overview and _is_placeholder(overview):
        overview = ""
    source_ids = _dedupe_source_ids(
        source for item in (*conclusions, *pending) for source in item.get("sources", [])
    )
    message_range = _range_for(messages)
    if message_range and not source_ids:
        source_ids = [message_range["start_id"], message_range["end_id"]]
    return {
        "kind": "summary",
        "title": SCHEDULED_SUMMARY_TITLE if scheduled else SUMMARY_TITLE,
        "overview": overview,
        "conclusions": conclusions,
        "pending": pending,
        "sources": source_ids,
        "range": message_range,
        "period": _period_for(messages),
        "message_count": len(messages),
        "generated_at": float(generated_at if generated_at is not None else time.time()),
        "parse_error": bool(parse_error),
    }


def _dedupe_source_ids(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            clean = int(value)
        except (TypeError, ValueError):
            continue
        if clean > 0 and clean not in result:
            result.append(clean)
    return result


def _looks_structured(text: str) -> bool:
    clean = text.lstrip()
    if clean.startswith(("{", "[")):
        return True
    if clean[:12].lower().startswith("```json"):
        return True
    lowered = clean.lower()
    return "{" in clean and any(
        f'"{key}"' in lowered
        for key in ("overview", "conclusions", "pending", "tasks", "unresolved")
    )


def parse_summary_card(
    text: str,
    messages: list[dict[str, Any]],
    *,
    scheduled: bool = False,
    generated_at: float | None = None,
) -> dict[str, Any]:
    payload = extract_json_object(text)
    if payload is not None:
        return _normalize_summary(
            payload, messages, scheduled=scheduled, generated_at=generated_at
        )
    if _looks_structured(text):
        return _normalize_summary(
            {}, messages, scheduled=scheduled, generated_at=generated_at, parse_error=True
        )
    legacy = _parse_legacy_summary(text)
    return _normalize_summary(
        legacy, messages, scheduled=scheduled, generated_at=generated_at
    )


def _strip_markdown_line(raw: str) -> str:
    clean = raw.strip()
    clean = re.sub(r"^#{1,6}\s*", "", clean)
    clean = clean.replace("**", "").replace("__", "")
    clean = re.sub(r"^[-*•]\s*", "", clean)
    return clean.strip()


def _parse_legacy_todo(text: str) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    unresolved: list[str] = []
    current_group = "confirmed"
    current: dict[str, Any] | None = None
    for raw_line in text.replace("\r\n", "\n").splitlines():
        raw = raw_line.strip()
        if not raw:
            continue
        heading = re.match(r"^#{1,6}\s*(.+?)\s*$", raw)
        heading_text = heading.group(1).strip(" *#：:") if heading else ""
        if heading_text:
            if "建议" in heading_text or "跟进" in heading_text:
                current_group = "suggested"
            elif "信息缺失" in heading_text or "待确认" in heading_text:
                current_group = "unresolved"
            else:
                current_group = "confirmed"
            current = None
            continue
        clean = _strip_markdown_line(raw)
        if not clean or _is_placeholder(clean):
            continue
        if clean in {"明确任务", "建议跟进", "信息缺失", "待办提取", "群聊总结"}:
            if clean == "建议跟进":
                current_group = "suggested"
            elif clean == "信息缺失":
                current_group = "unresolved"
            elif clean == "明确任务":
                current_group = "confirmed"
            current = None
            continue
        if current_group == "unresolved":
            unresolved.append(clean)
            continue
        match = re.match(r"^(?:事项|任务|标题)\s*[：:]\s*(.*)$", clean)
        if match:
            current = {
                "title": _clean_text(match.group(1)),
                "group": current_group,
            }
            tasks.append(current)
            continue
        match = re.match(r"^(?:负责人|owner|assignee)\s*[：:]\s*(.*)$", clean, re.IGNORECASE)
        if match:
            if current is None:
                current = {"title": "", "group": current_group}
                tasks.append(current)
            current["owner"] = _clean_text(match.group(1))
            continue
        match = re.match(r"^(?:期限|截止时间|截止|due|deadline)\s*[：:]\s*(.*)$", clean, re.IGNORECASE)
        if match:
            if current is None:
                current = {"title": "", "group": current_group}
                tasks.append(current)
            current["due"] = _clean_text(match.group(1))
            continue
        title = clean
        owner = ""
        due = ""
        inline = re.search(
            r"[（(]\s*(?:负责人|owner)\s*[：:]\s*([^，,）)]+)"
            r"(?:\s*[，,]\s*(?:期限|截止|due)\s*[：:]\s*([^）)]+))?\s*[）)]",
            title,
            re.IGNORECASE,
        )
        if inline:
            owner = _clean_text(inline.group(1))
            due = _clean_text(inline.group(2) or "")
            title = title[: inline.start()].strip()
        current = {"title": _clean_text(title), "owner": owner, "due": due, "group": current_group}
        tasks.append(current)
    return {"tasks": tasks, "unresolved": unresolved}


_PRIORITY_ALIASES = {
    "high": "high",
    "urgent": "high",
    "critical": "high",
    "p0": "high",
    "p1": "high",
    "高": "high",
    "较高": "high",
    "紧急": "high",
    "重要": "high",
    "normal": "normal",
    "medium": "normal",
    "p2": "normal",
    "普通": "normal",
    "一般": "normal",
    "中": "normal",
    "low": "low",
    "p3": "low",
    "低": "low",
    "较低": "low",
    "不急": "low",
}
_HIGH_PRIORITY_MARKERS = (
    "紧急",
    "尽快",
    "立即",
    "马上",
    "火速",
    "优先处理",
    "阻塞",
    "今天必须",
    "今日必须",
    "urgent",
    "critical",
    "blocker",
    "asap",
)
_LOW_PRIORITY_MARKERS = (
    "不急",
    "有空",
    "有时间",
    "方便时",
    "低优先级",
    "慢慢",
    "when convenient",
    "low priority",
)
_GROUP_OWNERS = {"全体成员", "所有人", "全员", "大家", "团队"}
_SELF_OWNERS = {"我", "本人", "自己", "me", "self"}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        clean = int(value)
    except (TypeError, ValueError):
        return None
    return clean if clean > 0 else None


def _epoch_seconds(value: Any) -> int | None:
    """Return epoch seconds for numbers and unambiguous ISO 8601 strings."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
    else:
        clean = _clean_text(str(value), 80)
        if not clean:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", clean):
            stamp = float(clean)
        else:
            iso_value = clean[:-1] + "+00:00" if clean.endswith(("Z", "z")) else clean
            if not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?",
                iso_value,
            ):
                return None
            try:
                parsed = datetime.fromisoformat(iso_value)
            except ValueError:
                return None
            return int(parsed.timestamp())
    if stamp > 10_000_000_000:
        stamp /= 1000
    if stamp <= 0 or stamp > 253_402_300_799:
        return None
    return int(stamp)


def _message_epoch(message: dict[str, Any]) -> int | None:
    for key in ("created_at", "timestamp", "sent_at"):
        stamp = _epoch_seconds(message.get(key))
        if stamp is not None:
            return stamp
    return None


def _source_message(
    messages: list[dict[str, Any]], sources: list[int]
) -> dict[str, Any] | None:
    source_set = set(sources)
    for message in messages:
        try:
            message_id = int(message.get("id"))
        except (TypeError, ValueError):
            continue
        if message_id in source_set:
            return message
    return None


def _number_token(value: str) -> int | None:
    clean = value.strip()
    if clean.isdigit():
        return int(clean)
    if not clean:
        return None
    if "十" in clean:
        left, _, right = clean.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        units = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + units
    if len(clean) == 1 and clean in _CN_DIGITS:
        return _CN_DIGITS[clean]
    return None


def _due_datetime(display: str, base: datetime) -> datetime | None:
    clean = _clean_text(display, 80)
    if not clean or _is_placeholder(clean):
        return None

    direct = _epoch_seconds(clean)
    if direct is not None:
        return datetime.fromtimestamp(direct).astimezone()

    clock = re.search(r"(\d{1,2})[:：](\d{2})(?::(\d{2}))?", clean)
    period_match = re.search(r"凌晨|早上|上午|中午|下午|傍晚|晚上", clean)
    period = period_match.group(0) if period_match else ""
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2))
        second = int(clock.group(3) or 0)
    else:
        chinese_clock = re.search(
            r"(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*"
            r"(?P<hour>\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*(?:点|时)"
            r"(?:(?P<half>半)|(?P<minute>[零一二两三四五六七八九十\d]{1,3})\s*分)?",
            clean,
        )
        if chinese_clock:
            period = chinese_clock.group("period") or ""
            hour = _number_token(chinese_clock.group("hour")) or 0
            minute = 30 if chinese_clock.group("half") else (
                _number_token(chinese_clock.group("minute") or "") or 0
            )
            second = 0
        else:
            hour, minute, second = 23, 59, 59
    if period in {"下午", "傍晚", "晚上"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    if hour > 23 or minute > 59 or second > 59:
        return None

    target = None
    today = base.date()
    if "今天" in clean or "今日" in clean:
        target = today
    elif "明天" in clean or "明日" in clean:
        target = today + timedelta(days=1)
    elif "后天" in clean:
        target = today + timedelta(days=2)
    else:
        full_date = re.search(
            r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]?",
            clean,
        )
        month_day = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", clean)
        if full_date:
            try:
                target = datetime(
                    int(full_date.group(1)),
                    int(full_date.group(2)),
                    int(full_date.group(3)),
                ).date()
            except ValueError:
                return None
        elif month_day:
            try:
                target = datetime(
                    today.year, int(month_day.group(1)), int(month_day.group(2))
                ).date()
            except ValueError:
                return None
            if target < today:
                try:
                    target = target.replace(year=today.year + 1)
                except ValueError:
                    return None
        else:
            weekday_names = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
            week_match = re.search(r"(下+)?(?:周|星期)([一二三四五六日天])", clean)
            if week_match:
                target = today - timedelta(days=today.weekday()) + timedelta(
                    days=weekday_names[week_match.group(2)]
                    + (7 * len(week_match.group(1) or ""))
                )
                if not week_match.group(1) and target < today:
                    target += timedelta(days=7)

    if target is None:
        return None
    try:
        return datetime.combine(target, datetime.min.time(), tzinfo=base.tzinfo).replace(
            hour=hour, minute=minute, second=second
        )
    except ValueError:
        return None


def _relative_commitment_due(
    title: str, source: dict[str, Any] | None
) -> tuple[str, int | None] | None:
    """Prefer an explicit first-person date over a model-converted ISO value."""

    if source is None or source.get("sender_kind", "user") != "user":
        return None
    content = str(source.get("content") or "")
    if not content:
        return None
    base_stamp = _message_epoch(source)
    if base_stamp is None:
        return None
    base = datetime.fromtimestamp(base_stamp).astimezone()
    for match in _SELF_COMMITMENT.finditer(content):
        due = _clean_text(
            f"{match.group('due') or ''}{match.group('time') or ''}", 80
        )
        if not due or (title and not _similar_text(title, match.group("title"))):
            continue
        parsed = _due_datetime(due, base)
        return due, int(parsed.timestamp()) if parsed is not None else None
    return None


def _has_explicit_clock(value: str) -> bool:
    return bool(
        re.search(
            r"\d{1,2}[:：]\d{2}|[零一二两三四五六七八九十\d]{1,3}\s*(?:点|时)",
            value,
        )
    )


def _same_due_day(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_due = _epoch_seconds(left.get("due_at"))
    right_due = _epoch_seconds(right.get("due_at"))
    if left_due is not None and right_due is not None:
        return datetime.fromtimestamp(left_due).date() == datetime.fromtimestamp(
            right_due
        ).date()
    left_text = _clean_text(str(left.get("due") or ""), 80)
    right_text = _clean_text(str(right.get("due") or ""), 80)
    return bool(left_text and left_text == right_text)


def _tasks_can_merge(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _norm_key(left.get("owner")) != _norm_key(right.get("owner")):
        return False
    if not _same_due_day(left, right):
        return False
    left_title = str(left.get("title") or "")
    right_title = str(right.get("title") or "")
    if not (
        _dedupe_title_key(left_title) == _dedupe_title_key(right_title)
        or _similar_text(left_title, right_title)
    ):
        return False
    left_due = _clean_text(str(left.get("due") or ""), 80)
    right_due = _clean_text(str(right.get("due") or ""), 80)
    if _has_explicit_clock(left_due) and _has_explicit_clock(right_due):
        left_stamp = _epoch_seconds(left.get("due_at"))
        right_stamp = _epoch_seconds(right.get("due_at"))
        if (
            left_stamp is not None
            and right_stamp is not None
            and abs(left_stamp - right_stamp) >= 60
        ):
            return False
    return True


def _merge_similar_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for task in tasks:
        target = next(
            (existing for existing in merged if _tasks_can_merge(existing, task)),
            None,
        )
        if target is None:
            merged.append(task)
            continue
        if len(str(task.get("title") or "")) > len(str(target.get("title") or "")):
            target["title"] = task["title"]
        if not target.get("description") and task.get("description"):
            target["description"] = task["description"]
        if not target.get("owner") and task.get("owner"):
            target["owner"] = task["owner"]
            target["assignee_user_id"] = task.get("assignee_user_id")
            target["owner_kind"] = task.get("owner_kind")
        basis_rank = {"default": 0, "inferred": 1, "explicit": 2}
        if basis_rank.get(str(task.get("priority_basis")), 0) > basis_rank.get(
            str(target.get("priority_basis")), 0
        ):
            target["priority"] = task.get("priority")
            target["priority_basis"] = task.get("priority_basis")
        task_clock = _has_explicit_clock(str(task.get("due") or ""))
        target_clock = _has_explicit_clock(str(target.get("due") or ""))
        if task_clock and not target_clock:
            target["due"] = task.get("due")
            target["due_at"] = task.get("due_at")
        if target.get("due_at") is None and task.get("due_at") is not None:
            target["due_at"] = task["due_at"]
        if task.get("group") == "confirmed":
            target["group"] = "confirmed"
        target["status"] = (
            "completed"
            if target.get("status") == "completed"
            and task.get("status") == "completed"
            else "open"
        )
        target["sources"] = _dedupe_source_ids(
            [*target.get("sources", []), *task.get("sources", [])]
        )
        if target.get("todo_id") is None and task.get("todo_id") is not None:
            target["todo_id"] = task["todo_id"]
        if not target.get("sync_status") and task.get("sync_status"):
            target["sync_status"] = task["sync_status"]
    return merged


def _normalize_priority(
    value: Any, basis: Any, *, title: str, description: str, due: str
) -> tuple[str, str]:
    explicit = _clean_text(str(value or ""), 40).lower()
    priority = _PRIORITY_ALIASES.get(explicit)
    clean_basis = _clean_text(str(basis or ""), 30).lower()
    if clean_basis == "default":
        return "normal", "default"
    if priority and clean_basis in {"explicit", "inferred"}:
        return priority, clean_basis
    if priority:
        evidence = " ".join((title, description, due)).lower()
        inferred = any(
            marker in evidence
            for marker in (*_HIGH_PRIORITY_MARKERS, *_LOW_PRIORITY_MARKERS)
        )
        return priority, "inferred" if inferred else "default"
    evidence = " ".join((title, description, due)).lower()
    if any(marker in evidence for marker in _HIGH_PRIORITY_MARKERS):
        return "high", "inferred"
    if any(marker in evidence for marker in _LOW_PRIORITY_MARKERS):
        return "low", "inferred"
    return "normal", "default"


def _normalize_status(item: dict[str, Any]) -> str:
    raw = item.get("status")
    if raw is None and "completed" in item:
        raw = "completed" if item.get("completed") is True else "open"
    clean = _clean_text(str(raw or ""), 30).lower()
    if clean in {"completed", "complete", "done", "resolved", "已完成", "完成", "已办"}:
        return "completed"
    return "open"


def _mention_user_id(mention: dict[str, Any]) -> int | None:
    raw = mention.get("member_id")
    if raw is None and str(mention.get("member_kind") or "").lower() == "user":
        raw = mention.get("member_user_id") or mention.get("id")
    if isinstance(raw, str) and raw.lower().startswith("agent:"):
        return None
    if isinstance(raw, str):
        match = re.fullmatch(r"(?:user\s*:\s*)?(\d+)", raw.strip(), re.IGNORECASE)
        if not match:
            return None
        raw = match.group(1)
    return _positive_int(raw)


def _identity_names(messages: list[dict[str, Any]]) -> dict[str, set[int]]:
    names: dict[str, set[int]] = {}

    def add(name: Any, user_id: Any) -> None:
        clean_name = _clean_text(name, 80).lstrip("@")
        clean_id = _positive_int(user_id)
        key = _norm_key(clean_name)
        if key and clean_id is not None:
            names.setdefault(key, set()).add(clean_id)

    for message in messages:
        if str(message.get("sender_kind") or "user").lower() == "user":
            add(message.get("sender_name"), message.get("sender_id"))
        for mention in message.get("mentions") or []:
            if not isinstance(mention, dict):
                continue
            user_id = _mention_user_id(mention)
            if user_id is None:
                continue
            for key in ("display", "name", "username"):
                add(mention.get(key), user_id)
    return names


def _name_for_user(messages: list[dict[str, Any]], user_id: int) -> str:
    for message in messages:
        if (
            _positive_int(message.get("sender_id")) == user_id
            and str(message.get("sender_kind") or "user").lower() == "user"
        ):
            name = _clean_text(message.get("sender_name"), 80).lstrip("@")
            if name:
                return name
    return ""


def _normalize_owner(owner: str, names: set[str], requester_name: str) -> str:
    clean = _clean_text(owner, 80).lstrip("@")
    if _is_placeholder(clean):
        return ""
    clean = re.sub(r"^(?:负责人|owner|assignee)\s*[：:]\s*", "", clean, flags=re.IGNORECASE)
    if clean in _SELF_OWNERS:
        return requester_name
    if clean in _GROUP_OWNERS:
        return clean
    if not names:
        return ""
    clean_key = _norm_key(clean)
    for name in names:
        name_key = _norm_key(name)
        if not name_key:
            continue
        if name_key == clean_key:
            return name
        if any(
            clean_key.startswith(name_key + _norm_key(separator))
            for separator in (" ", "（", "(", "/", "-")
        ):
            return name
    return ""


def _owner_kind(
    owner: str, owner_id: int | None, requested_by: int | None, requester_name: str
) -> str:
    if not owner:
        return "unassigned"
    if owner_id is not None and requested_by is not None and owner_id == requested_by:
        return "current"
    if requester_name and _norm_key(owner) == _norm_key(requester_name):
        return "current"
    return "other"


def _conversation_source(messages: list[dict[str, Any]]) -> tuple[str, str] | None:
    for message in messages:
        conversation_type = _clean_text(
            str(
                message.get("conversation_type")
                or message.get("source_conversation_type")
                or ""
            ),
            40,
        )
        conversation_id = _clean_text(
            str(
                message.get("conversation_id")
                or message.get("source_conversation_id")
                or ""
            ),
            160,
        )
        if conversation_type and conversation_id:
            return conversation_type, conversation_id
    return None


def _normalize_todo(
    payload: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    generated_at: float | None,
    parse_error: bool = False,
    requested_by: int | None = None,
    requester_name: str = "",
    filter_owner: bool = True,
    require_sources: bool = True,
) -> dict[str, Any]:
    valid_ids = _message_ids(messages)
    requested_by = _positive_int(requested_by)
    identity = _identity_names(messages)
    names = {
        _clean_text(message.get("sender_name"), 80)
        for message in messages
        if message.get("sender_name")
    }
    names.update(
        _clean_text(mention.get("display"), 80)
        for message in messages
        for mention in (message.get("mentions") or [])
        if isinstance(mention, dict)
        and _mention_user_id(mention) is not None
        and mention.get("display")
    )
    requester_name = _clean_text(requester_name, 80).lstrip("@")
    if requested_by is not None and not requester_name:
        requester_name = _name_for_user(messages, requested_by)
    if requester_name:
        names.add(requester_name)
        if requested_by is None:
            requester_ids = identity.get(_norm_key(requester_name), set())
            if len(requester_ids) == 1:
                requested_by = next(iter(requester_ids))
    if requested_by is not None and requester_name:
        identity.setdefault(_norm_key(requester_name), set()).add(requested_by)
    tasks: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    raw_tasks: list[Any] = list(
        _as_items(payload.get("tasks") or payload.get("items") or payload.get("待办"))
    )
    for key, group_name in (("明确任务", "confirmed"), ("建议跟进", "suggested")):
        for item in _as_items(payload.get(key)):
            if isinstance(item, dict):
                item = {**item, "group": item.get("group") or group_name}
            elif isinstance(item, str):
                item = {"title": item, "group": group_name}
            raw_tasks.append(item)
    for item in raw_tasks:
        if len(tasks) >= 40:
            break
        if not isinstance(item, dict):
            continue
        title = ""
        for key in ("title", "task", "content", "text", "name", "事项"):
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate.strip():
                title = _clean_text(candidate, 200)
                break
        if not title or _is_placeholder(title):
            continue
        if title.endswith(("?", "？")) and not _ACTION_START.match(title):
            continue
        if _QUESTION_START.match(title) and not _ACTION_START.match(title):
            continue
        group_value = str(item.get("group") or item.get("type") or "confirmed").lower()
        group = "suggested" if group_value in {"suggested", "suggestion", "建议", "follow_up", "pending"} else "confirmed"
        if item.get("confirmed") is False:
            group = "suggested"
        owner = _normalize_owner(
            str(item.get("owner") or item.get("assignee") or item.get("负责人") or ""),
            names,
            requester_name,
        )
        owner_ids = identity.get(_norm_key(owner), set()) if owner else set()
        assignee_user_id = next(iter(owner_ids)) if len(owner_ids) == 1 else None
        owner_kind = _owner_kind(
            owner, assignee_user_id, requested_by, requester_name
        )
        if filter_owner and requested_by is not None and owner_kind != "current":
            continue
        description = _clean_text(
            str(
                item.get("description")
                or item.get("details")
                or item.get("notes")
                or item.get("说明")
                or item.get("备注")
                or ""
            ),
            600,
        )
        if _is_placeholder(description):
            description = ""
        due = _clean_text(
            str(item.get("due") or item.get("deadline") or item.get("期限") or item.get("截止时间") or ""),
            80,
        )
        if _is_placeholder(due):
            due = ""
        sources = _source_ids(
            item.get("sources", item.get("source_message_id", item.get("source"))), valid_ids
        )
        if require_sources and not sources:
            continue
        source = _source_message(messages, sources)
        relative_due = _relative_commitment_due(title, source)
        if relative_due is not None:
            due, relative_due_at = relative_due
        else:
            relative_due_at = None
        due_at = _epoch_seconds(item.get("due_at") or item.get("dueAt"))
        if due:
            base_stamp = _message_epoch(source) if source is not None else None
            if base_stamp is None:
                message_stamps = [
                    stamp
                    for message in messages
                    if (stamp := _message_epoch(message)) is not None
                ]
                base_dates = {
                    datetime.fromtimestamp(stamp).astimezone().date()
                    for stamp in message_stamps
                }
                if len(base_dates) == 1:
                    base_stamp = message_stamps[0]
            explicit_date = bool(
                re.search(
                    r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}", due
                )
            )
            if base_stamp is not None or explicit_date:
                parsed_due = _due_datetime(
                    due,
                    datetime.fromtimestamp(base_stamp).astimezone()
                    if base_stamp is not None
                    else datetime.now().astimezone(),
                )
                if parsed_due is not None:
                    due_at = int(parsed_due.timestamp())
        if relative_due_at is not None:
            due_at = relative_due_at
        priority, priority_basis = _normalize_priority(
            item.get("priority"),
            item.get("priority_basis"),
            title=title,
            description=description,
            due=due,
        )
        status = _normalize_status(item)
        due_key = str(due_at) if due_at is not None else _norm_key(due)
        key = f"{_dedupe_title_key(title)}|{_norm_key(owner)}|{due_key}"
        if not key:
            continue
        existing = seen.get(key)
        if existing:
            if group == "confirmed":
                existing["group"] = "confirmed"
            if not existing.get("due") and due:
                existing["due"] = due
            if not existing.get("description") and description:
                existing["description"] = description
            if not existing.get("assignee_user_id") and assignee_user_id is not None:
                existing["assignee_user_id"] = assignee_user_id
            if not existing.get("owner") and owner:
                existing["owner"] = owner
                existing["owner_kind"] = owner_kind
            if existing.get("due_at") is None and due_at is not None:
                existing["due_at"] = due_at
            basis_rank = {"default": 0, "inferred": 1, "explicit": 2}
            if basis_rank[priority_basis] > basis_rank.get(
                str(existing.get("priority_basis") or "default"), 0
            ):
                existing["priority"] = priority
                existing["priority_basis"] = priority_basis
            if status == "completed":
                existing["status"] = status
            existing["sources"] = _dedupe_source_ids([*existing.get("sources", []), *sources])
            continue
        task: dict[str, Any] = {
            "title": title,
            "owner": owner,
            "assignee_user_id": assignee_user_id,
            "owner_kind": owner_kind,
            "due": due,
            "due_at": due_at,
            "priority": priority,
            "priority_basis": priority_basis,
            "status": status,
            "group": group,
            "sources": sources,
        }
        if description:
            task["description"] = description
        todo_id = _positive_int(item.get("todo_id"))
        if todo_id is not None:
            task["todo_id"] = todo_id
        sync_status = _clean_text(str(item.get("sync_status") or ""), 80)
        if sync_status and not _is_placeholder(sync_status):
            task["sync_status"] = sync_status
        seen[key] = task
        tasks.append(task)
    for message in messages:
        if len(tasks) >= 40:
            break
        if message.get("sender_kind") != "user":
            continue
        sender_id = _positive_int(message.get("sender_id"))
        if (
            filter_owner
            and requested_by is not None
            and sender_id != requested_by
        ):
            continue
        source_id = message.get("id")
        owner_name = _clean_text(message.get("sender_name"), 80)
        if not isinstance(source_id, int) or source_id not in valid_ids or not owner_name:
            continue
        for match in _SELF_COMMITMENT.finditer(str(message.get("content") or "")):
            title = _clean_text(match.group("title"), 200)
            due = _clean_text(
                f"{match.group('due') or ''}{match.group('time') or ''}", 80
            )
            base_stamp = _message_epoch(message)
            parsed_due = (
                _due_datetime(due, datetime.fromtimestamp(base_stamp).astimezone())
                if due and base_stamp is not None
                else None
            )
            due_at = int(parsed_due.timestamp()) if parsed_due is not None else None
            task_key = (
                f"{_dedupe_title_key(title)}|{_norm_key(owner_name)}|"
                f"{due_at if due_at is not None else _norm_key(due)}"
            )
            if not title or title.endswith(("吗", "呢")) or task_key in seen:
                continue
            existing = next(
                (
                    task
                    for task in tasks
                    if task.get("owner") == owner_name
                    and _similar_text(task.get("title"), title)
                    and source_id in task.get("sources", [])
                ),
                None,
            )
            if existing is not None:
                same_due = (
                    not existing.get("due")
                    or not due
                    or existing.get("due") == due
                    or (
                        existing.get("due_at") is not None
                        and due_at is not None
                        and int(existing["due_at"]) == int(due_at)
                    )
                )
                if same_due:
                    if not existing.get("due") and due:
                        existing["due"] = due
                    if existing.get("due_at") is None and due_at is not None:
                        existing["due_at"] = due_at
                    continue
            priority, priority_basis = _normalize_priority(
                None, None, title=title, description="", due=due
            )
            task: dict[str, Any] = {
                "title": title,
                "owner": owner_name,
                "assignee_user_id": sender_id,
                "owner_kind": _owner_kind(
                    owner_name, sender_id, requested_by, requester_name
                ),
                "due": due,
                "due_at": due_at,
                "priority": priority,
                "priority_basis": priority_basis,
                "status": "open",
                "group": "confirmed",
                "sources": [source_id],
            }
            tasks.append(task)
            seen[task_key] = task
            if len(tasks) >= 40:
                break
    tasks = _merge_similar_tasks(tasks)
    unresolved = _dedupe(
        _item_text_sources(value, set())[0]
        for value in _as_items(
            payload.get("unresolved")
            or payload.get("missing")
            or payload.get("待确认")
            or payload.get("信息缺失")
        )
    )[:12]
    if (filter_owner and requested_by is not None) or not tasks:
        unresolved = []
    message_range = _range_for(messages)
    if requested_by is not None:
        rank = {"current": 0, "unassigned": 1, "other": 2}
        tasks.sort(key=lambda item: rank.get(str(item.get("owner_kind")), 3))
    sources = _dedupe_source_ids(
        source for task in tasks for source in task.get("sources", [])
    )
    if message_range and not sources:
        sources = [message_range["start_id"], message_range["end_id"]]
    conversation = _conversation_source(messages)
    if conversation is not None:
        for task in tasks:
            task["source_conversation_type"] = conversation[0]
            task["source_conversation_id"] = conversation[1]
    card: dict[str, Any] = {
        "kind": "todo",
        "title": TODO_TITLE,
        "tasks": tasks,
        "unresolved": unresolved,
        "sources": sources,
        "range": message_range,
        "period": _period_for(messages),
        "message_count": len(messages),
        "generated_at": float(generated_at if generated_at is not None else time.time()),
        "parse_error": bool(parse_error),
        "requester_id": requested_by,
        "requester_name": requester_name,
    }
    if conversation is not None:
        card["source_conversation_type"], card["source_conversation_id"] = conversation
    return card


def parse_todo_card(
    text: str,
    messages: list[dict[str, Any]],
    *,
    generated_at: float | None = None,
    requested_by: int | None = None,
    requester_name: str = "",
    filter_owner: bool = True,
    require_sources: bool = True,
) -> dict[str, Any]:
    payload = extract_json_object(text)
    if payload is not None:
        return _normalize_todo(
            payload, messages, generated_at=generated_at,
            requested_by=requested_by, requester_name=requester_name,
            filter_owner=filter_owner, require_sources=require_sources,
        )
    if _looks_structured(text):
        return _normalize_todo(
            {}, messages, generated_at=generated_at, parse_error=True,
            requested_by=requested_by, requester_name=requester_name,
            filter_owner=filter_owner, require_sources=require_sources,
        )
    legacy = _parse_legacy_todo(text)
    return _normalize_todo(
        legacy, messages, generated_at=generated_at,
        requested_by=requested_by, requester_name=requester_name,
        filter_owner=filter_owner, require_sources=False,
    )


def _summary_markdown(card: dict[str, Any]) -> str:
    title = str(card.get("title") or SUMMARY_TITLE)
    overview = str(card.get("overview") or "")
    conclusions = [item["text"] for item in card.get("conclusions", [])]
    pending = [item["text"] for item in card.get("pending", [])]
    if card.get("parse_error"):
        conclusion_lines = ["- 结构化内容解析失败，请重新生成。"]
    else:
        conclusion_lines = [f"- {value}" for value in conclusions] or ["- 未明确"]
    pending_lines = [f"- {value}" for value in pending] or ["- 未明确"]
    return "\n\n".join(
        [
            f"## {title}",
            "### 主要话题\n" + (f"- {overview}" if overview else "- 未明确"),
            "### 核心结论\n" + "\n".join(conclusion_lines),
            "### 待确认事项\n" + "\n".join(pending_lines),
        ]
    )


def _todo_markdown(card: dict[str, Any]) -> str:
    tasks = card.get("tasks", [])
    confirmed = [task for task in tasks if task.get("group") != "suggested"]
    suggested = [task for task in tasks if task.get("group") == "suggested"]
    unresolved = card.get("unresolved", [])
    if card.get("parse_error"):
        confirmed = [{"title": "结构化内容解析失败，请重新生成。"}]
    contact_lines: list[str] = []
    for task in confirmed:
        lines = [f"- **事项**：{task.get('title', '')}"]
        if task.get("owner"):
            lines.append(f"  **负责人**：{task['owner']}")
        if task.get("due"):
            lines.append(f"  **期限**：{task['due']}")
        contact_lines.append("  \n".join(lines))
    suggestion_lines = [f"- {task.get('title', '')}" for task in suggested]
    unresolved_lines = [f"- {value}" for value in unresolved]
    return "\n\n".join(
        [
            "## 待办提取",
            "### 明确任务\n" + ("\n".join(contact_lines) or "- 未发现明确待办"),
            "### 建议跟进\n" + ("\n".join(suggestion_lines) or "- 无"),
            "### 信息缺失\n" + ("\n".join(unresolved_lines) or "- 无"),
        ]
    )


def build_agent_card(
    task_type: str,
    text: str,
    messages: list[dict[str, Any]],
    *,
    scheduled: bool = False,
    generated_at: float | None = None,
    requested_by: int | None = None,
    requester_name: str = "",
    filter_owner: bool = True,
    require_sources: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """Return validated card metadata and a Markdown compatibility fallback."""

    clean = str(text or "").replace("\r\n", "\n").strip()
    if task_type == "summary":
        card = parse_summary_card(
            clean, messages, scheduled=scheduled, generated_at=generated_at
        )
        return card, _summary_markdown(card)
    if task_type == "todo":
        card = parse_todo_card(
            clean, messages, generated_at=generated_at,
            requested_by=requested_by, requester_name=requester_name,
            filter_owner=filter_owner, require_sources=require_sources,
        )
        return card, _todo_markdown(card)
    return None, clean
