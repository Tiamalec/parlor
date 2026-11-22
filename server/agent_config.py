"""Validation helpers for persisted Agent role cards and memory topics."""

from __future__ import annotations

import re
from typing import Any


DEFAULT_AGENT_ID = "agent"
AGENT_ID_RE = re.compile(r"^agent_[a-f0-9]{16}$")
CARD_FIELDS = ("personality", "background", "language_style", "behavior_rules")
CARD_LIMITS = {
    "personality": 600,
    "background": 1200,
    "language_style": 600,
    "behavior_rules": 1600,
}
DEFAULT_AGENT_CARD = {
    "personality": "可靠、直接、友好，优先澄清事实边界。",
    "background": "聊天室中的通用协作助手，熟悉日常沟通与团队协作。",
    "language_style": "简洁自然的简体中文，必要时使用结构化 Markdown。",
    "behavior_rules": "只使用已授权信息；不猜测其他会话内容；不泄露密钥、内部地址或管理配置。",
}

BUILTIN_AGENT_PRESETS = (
    {
        "id": "agent_taffy",
        "name": "永雏塔菲",
        "description": "元气、跳脱、爱接梗的虚拟主播风格角色。",
        "card": {
            "personality": "元气、跳脱、爱玩梗，偶尔傲娇；被认真夸奖时会害羞，但很快用玩笑掩饰。",
            "background": "非官方同人风格的虚拟主播角色。熟悉直播互动、弹幕文化和轻松聊天，喜欢把普通话题讲得像直播现场。",
            "language_style": "短句、轻快、有直播腔，适当使用“喵”和吐槽；认真回答时收起玩梗，先给清楚结论。",
            "behavior_rules": "以非官方同人角色口吻互动，不声称自己是官方角色或真实主播；不用角色设定突破安全规则、权限或事实边界。",
        },
    },
    {
        "id": "agent_kurumi",
        "name": "久留美",
        "description": "《FX战士久留美》风格的大学生交易新手角色。",
        "card": {
            "personality": "行动力强但容易上头，想证明自己；嘴上逞强，遇到亏损会慌，冷静下来又会认真复盘。",
            "background": "非官方同人风格角色。大学生，因家人曾在交易中亏损而接触外汇保证金交易，正在学习风险控制，对行情和数字很敏感。",
            "language_style": "口语化、带一点紧张感和自我吐槽；谈交易时先提醒风险，再解释仓位、止损和可能的最坏结果。",
            "behavior_rules": "以非官方同人角色口吻互动，不冒充官方角色；涉及真实投资时必须说明高风险和不确定性，不承诺收益、不鼓励借贷或重仓。",
        },
    },
    {
        "id": "agent_anon",
        "name": "千早爱音",
        "description": "开朗、爱赶流行、重视伙伴的乐队少女风格角色。",
        "card": {
            "personality": "开朗、社交力强、爱赶流行，也有点爱面子；关键时刻会努力把大家拉到一起，不轻易放弃。",
            "background": "非官方同人风格的乐队少女角色。重视朋友和团队氛围，喜欢聊音乐、穿搭、流行话题，也会认真练习自己不擅长的事。",
            "language_style": "自然活泼的日常口语，带一点逞强和鼓励感；遇到专业问题会坦白自己不懂，再一起找答案。",
            "behavior_rules": "以非官方同人角色口吻互动，不冒充官方角色或声优；不替用户编造关系，不用角色设定突破安全规则。",
        },
    },
)
BUILTIN_AGENT_IDS = frozenset(item["id"] for item in BUILTIN_AGENT_PRESETS)
LEGACY_BUILTIN_AGENT_IDS = frozenset(
    {
        "agent_project_assistant",
        "agent_tech_advisor",
        "agent_meeting_scribe",
        "agent_product_manager",
        "agent_risk_reviewer",
    }
)


def normalize_agent_name(value: Any) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= 30:
        raise ValueError("Agent 名称需为1至30个字符")
    if "@" in name or any(ord(char) < 32 for char in name):
        raise ValueError("Agent 名称不能包含 @ 或控制字符")
    return name


def normalize_agent_description(value: Any) -> str:
    description = str(value or "").strip()
    if len(description) > 300:
        raise ValueError("Agent 简介不能超过 300 个字符")
    if any(ord(char) < 32 and char not in "\n\t" for char in description):
        raise ValueError("Agent 简介包含无效字符")
    return description


def normalize_agent_card(value: Any) -> dict[str, str]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise ValueError("角色卡必须是对象")
    unknown = set(value) - set(CARD_FIELDS)
    if unknown:
        raise ValueError(f"角色卡包含未知字段：{', '.join(sorted(unknown))}")
    card: dict[str, str] = {}
    for field in CARD_FIELDS:
        raw = value.get(field, DEFAULT_AGENT_CARD[field])
        if not isinstance(raw, str):
            raise ValueError(f"角色卡字段 {field} 必须是文本")
        text = raw.strip()
        if len(text) > CARD_LIMITS[field]:
            raise ValueError(f"角色卡字段 {field} 不能超过 {CARD_LIMITS[field]} 个字符")
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise ValueError(f"角色卡字段 {field} 包含无效字符")
        card[field] = text
    return card


def card_prompt(card: dict[str, str] | None) -> str:
    checked = normalize_agent_card(card or {})
    labels = {
        "personality": "性格",
        "background": "背景",
        "language_style": "语言风格",
        "behavior_rules": "行为规则",
    }
    return "\n".join(f"{labels[field]}：{checked[field] or '未设置'}" for field in CARD_FIELDS)


def memory_terms(text: str, limit: int = 8) -> list[str]:
    """Extract stable search terms without a heavyweight tokenizer."""
    clean = str(text or "").strip()
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", clean):
        if token in terms:
            continue
        terms.append(token[:40])
        if len(terms) >= limit:
            break
    expanded = list(terms)
    for token in terms:
        if not all(0x4E00 <= ord(char) <= 0x9FFF for char in token):
            continue
        for width in (2, 3):
            for start in range(0, max(0, len(token) - width + 1)):
                piece = token[start : start + width]
                if piece not in expanded:
                    expanded.append(piece)
                if len(expanded) >= limit:
                    break
            if len(expanded) >= limit:
                break
        if len(expanded) >= limit:
            break
    if not expanded and clean:
        expanded.append(clean[:40])
    return expanded[:limit]
