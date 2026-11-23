"""Structured AI cards for chat summaries and extracted todos.

The model is asked for JSON, but older models and historical messages still use
Markdown.  This module validates both shapes and always produces a compact
Markdown fallback for clients that do not understand the card metadata.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterable


SUMMARY_TITLE = "群聊总结"
SCHEDULED_SUMMARY_TITLE = "定时群聊总结"
TODO_TITLE = "待办事项"

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


def _normalize_owner(owner: str, names: set[str]) -> str:
    clean = _clean_text(owner, 80).lstrip("@")
    if _is_placeholder(clean):
        return ""
    clean = re.sub(r"^(?:负责人|owner|assignee)\s*[：:]\s*", "", clean, flags=re.IGNORECASE)
    if clean in {"全体成员", "所有人", "全员", "大家", "团队"}:
        return clean
    if not names:
        return ""
    for name in names:
        if not name:
            continue
        if name == clean:
            return clean
        if any(
            clean.startswith(name + separator)
            for separator in (" ", "（", "(", "/", "-")
        ):
            return clean
    return ""


def _normalize_todo(
    payload: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    generated_at: float | None,
    parse_error: bool = False,
) -> dict[str, Any]:
    valid_ids = _message_ids(messages)
    names = {
        _clean_text(message.get("sender_name"), 80)
        for message in messages
        if message.get("sender_name")
    }
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
                title = _clean_text(candidate, 300)
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
        )
        due = _clean_text(
            str(item.get("due") or item.get("deadline") or item.get("期限") or item.get("截止时间") or ""),
            80,
        )
        if _is_placeholder(due):
            due = ""
        sources = _source_ids(
            item.get("sources", item.get("source_message_id", item.get("source"))), valid_ids
        )
        key = _norm_key(title)
        if not key:
            continue
        existing = seen.get(key)
        if existing:
            if group == "confirmed":
                existing["group"] = "confirmed"
            if not existing.get("owner") and owner:
                existing["owner"] = owner
            if not existing.get("due") and due:
                existing["due"] = due
            existing["sources"] = _dedupe_source_ids([*existing.get("sources", []), *sources])
            continue
        task = {"title": title, "owner": owner, "due": due, "group": group, "sources": sources}
        seen[key] = task
        tasks.append(task)
    unresolved = _dedupe(
        _item_text_sources(value, set())[0]
        for value in _as_items(
            payload.get("unresolved")
            or payload.get("missing")
            or payload.get("待确认")
            or payload.get("信息缺失")
        )
    )[:12]
    message_range = _range_for(messages)
    sources = _dedupe_source_ids(
        source for task in tasks for source in task.get("sources", [])
    )
    if message_range and not sources:
        sources = [message_range["start_id"], message_range["end_id"]]
    return {
        "kind": "todo",
        "title": TODO_TITLE,
        "tasks": tasks,
        "unresolved": unresolved,
        "sources": sources,
        "range": message_range,
        "generated_at": float(generated_at if generated_at is not None else time.time()),
        "parse_error": bool(parse_error),
    }


def parse_todo_card(
    text: str,
    messages: list[dict[str, Any]],
    *,
    generated_at: float | None = None,
) -> dict[str, Any]:
    payload = extract_json_object(text)
    if payload is not None:
        return _normalize_todo(payload, messages, generated_at=generated_at)
    if _looks_structured(text):
        return _normalize_todo({}, messages, generated_at=generated_at, parse_error=True)
    legacy = _parse_legacy_todo(text)
    return _normalize_todo(legacy, messages, generated_at=generated_at)


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
) -> tuple[dict[str, Any] | None, str]:
    """Return validated card metadata and a Markdown compatibility fallback."""

    clean = str(text or "").replace("\r\n", "\n").strip()
    if task_type == "summary":
        card = parse_summary_card(
            clean, messages, scheduled=scheduled, generated_at=generated_at
        )
        return card, _summary_markdown(card)
    if task_type == "todo":
        card = parse_todo_card(clean, messages, generated_at=generated_at)
        return card, _todo_markdown(card)
    return None, clean
