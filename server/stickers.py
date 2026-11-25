"""Local sticker catalog helpers used by the Agent runtime and admin console."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .media import image_dimensions, valid_image_signature


MAX_STICKER_SIZE = 2 * 1024 * 1024
ALLOWED_STICKER_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_STICKER_TAGS = 12
MAX_STICKER_TAG_LENGTH = 24
DEFAULT_STICKER_CATEGORY = "通用"
MAX_STICKER_AGENTS = 64
STICKER_REQUEST_RE = re.compile(
    r"(?:发|来|送|整|抛|丢).{0,8}(?:表情包|表情|贴纸|sticker|meme)"
    r"|(?:表情包|贴纸|sticker|meme).{0,8}(?:来|发|要|给我|回复)",
    re.IGNORECASE,
)
STICKER_DIRECTIVE_RE = re.compile(
    r"[ \t]*\[(?:sticker|表情包)\s*[:：]\s*([^\]\r\n]+)\][ \t]*",
    re.IGNORECASE,
)
ROLE_CATEGORY_HINTS = (
    ("开心", ("开心", "高兴", "哈哈", "笑", "庆祝", "成功", "完成", "好耶", "太棒")),
    ("鼓励", ("加油", "鼓励", "辛苦", "坚持", "努力", "支持")),
    ("疑问", ("为什么", "怎么", "吗", "？", "?", "疑问", "不懂")),
    ("安慰", ("难过", "失败", "遗憾", "别急", "安慰", "抱歉")),
    ("惊讶", ("居然", "竟然", "震惊", "没想到", "惊讶", "哇")),
)


def normalize_sticker_name(value: Any) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("表情包名称需为 1 至 40 个字符")
    if any(ord(char) < 32 for char in name):
        raise ValueError("表情包名称包含无效字符")
    return name


def normalize_sticker_category(value: Any) -> str:
    category = str(value or DEFAULT_STICKER_CATEGORY).strip() or DEFAULT_STICKER_CATEGORY
    if len(category) > 24:
        raise ValueError("表情包分类不能超过 24 个字符")
    if any(ord(char) < 32 for char in category):
        raise ValueError("表情包分类包含无效字符")
    return category


def normalize_sticker_tags(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_items: Iterable[Any] = re.split(r"[,，、\s]+", value)
    elif isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raise ValueError("表情包标签必须是列表或逗号分隔文本")
    tags: list[str] = []
    for raw in raw_items:
        tag = str(raw or "").strip()
        if not tag:
            continue
        if len(tag) > MAX_STICKER_TAG_LENGTH:
            raise ValueError(f"单个标签不能超过 {MAX_STICKER_TAG_LENGTH} 个字符")
        if any(ord(char) < 32 for char in tag):
            raise ValueError("表情包标签包含无效字符")
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= MAX_STICKER_TAGS:
            break
    return tags


def normalize_sticker_agent_ids(value: Any) -> list[str]:
    """Normalize role-card assignments while leaving existence checks to storage."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_items: Iterable[Any] = re.split(r"[,，、\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raise ValueError("表情包角色卡必须是列表或逗号分隔文本")
    agent_ids: list[str] = []
    for raw in raw_items:
        agent_id = str(raw or "").strip()
        if not agent_id:
            continue
        if len(agent_id) > 80 or any(char.isspace() or ord(char) < 32 for char in agent_id):
            raise ValueError("表情包角色卡标识无效")
        if agent_id not in agent_ids:
            agent_ids.append(agent_id)
        if len(agent_ids) >= MAX_STICKER_AGENTS:
            break
    return agent_ids


def validate_sticker_image(raw: bytes, mime: str) -> None:
    if mime not in ALLOWED_STICKER_MIME:
        raise ValueError("仅支持 PNG、JPEG、GIF 和 WebP 表情包")
    if not raw or len(raw) > MAX_STICKER_SIZE:
        raise ValueError("表情包不能为空且不能超过 2 MiB")
    if not valid_image_signature(raw, mime):
        raise ValueError("表情包内容与声明格式不一致")
    dimensions = image_dimensions(raw, mime)
    if dimensions:
        width, height = dimensions
        if width <= 0 or height <= 0 or width > 4096 or height > 4096:
            raise ValueError("表情包尺寸无效")


def is_sticker_request(text: str) -> bool:
    return bool(STICKER_REQUEST_RE.search(str(text or "")))


def extract_sticker_directive(text: str) -> tuple[str, str | None]:
    """Remove model-only sticker markers wherever they appear in a reply."""
    clean = str(text or "")
    matches = list(STICKER_DIRECTIVE_RE.finditer(clean))
    if not matches:
        return clean, None
    requested_name = next(
        (match.group(1).strip() for match in matches if match.group(1).strip()),
        None,
    )

    def remove_marker(match: re.Match[str]) -> str:
        left = clean[match.start() - 1] if match.start() else ""
        right = clean[match.end()] if match.end() < len(clean) else ""
        return " " if left and right and not left.isspace() and not right.isspace() else ""

    visible = STICKER_DIRECTIVE_RE.sub(remove_marker, clean)
    visible = re.sub(r"[ \t]+\n", "\n", visible)
    visible = re.sub(r"\n[ \t]+", "\n", visible)
    visible = re.sub(r"\n{3,}", "\n\n", visible)
    return visible.strip(), requested_name


def _role_text(agent: dict[str, Any]) -> str:
    card = agent.get("card") if isinstance(agent.get("card"), dict) else {}
    return " ".join(
        str(value or "")
        for value in (
            agent.get("name"),
            agent.get("description"),
            card.get("personality"),
            card.get("background"),
            card.get("language_style"),
        )
    )


def _context_categories(context: str) -> set[str]:
    folded = context.casefold()
    return {
        category
        for category, cues in ROLE_CATEGORY_HINTS
        if any(cue.casefold() in folded for cue in cues)
    }


def score_sticker(sticker: dict[str, Any], context: str, role_text: str, explicit_name: str = "") -> int:
    folded = context.casefold()
    role = role_text.casefold()
    score = 0
    name = str(sticker.get("name", "")).casefold()
    category = str(sticker.get("category", "")).casefold()
    if explicit_name and explicit_name.casefold() in {name, str(sticker.get("id", "")).casefold()}:
        score += 20
    if name and name in folded:
        score += 6
    for tag in sticker.get("tags") or []:
        token = str(tag).casefold().strip()
        if token and token in folded:
            score += 3
    if category and category in role:
        score += 2
    if category in _context_categories(context):
        score += 2
    return score


def select_sticker(
    stickers: list[dict[str, Any]],
    *,
    context: str,
    agent: dict[str, Any],
    explicit: bool = False,
    explicit_name: str = "",
    avoid_id: str = "",
) -> dict[str, Any] | None:
    """Pick a sticker deterministically from local tags and role-card cues."""
    candidates = [dict(item) for item in stickers if item.get("enabled", True)]
    if not candidates:
        return None
    role_text = _role_text(agent)
    ranked = sorted(
        candidates,
        key=lambda item: (
            score_sticker(item, context, role_text, explicit_name),
            str(item.get("id", "")),
        ),
        reverse=True,
    )
    if explicit_name:
        exact = next(
            (
                item
                for item in ranked
                if explicit_name.casefold()
                in {str(item.get("name", "")).casefold(), str(item.get("id", "")).casefold()}
            ),
            None,
        )
        if exact:
            return exact
    best = ranked[0]
    if explicit:
        alternatives = [item for item in ranked if str(item.get("id", "")) != avoid_id]
        return alternatives[0] if alternatives else best
    if avoid_id and str(best.get("id", "")) == avoid_id:
        alternatives = [item for item in ranked[1:] if str(item.get("id", "")) != avoid_id]
        if alternatives:
            best = alternatives[0]
    if score_sticker(best, context, role_text, explicit_name) < 3:
        return None
    return best
