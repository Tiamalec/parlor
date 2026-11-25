"""Safe HTML rendering."""

from __future__ import annotations

from typing import Any
from PySide6.QtGui import QTextDocument
from ..theme import current_tokens
import html
import re


def mentions_to_safe_html(source: str, mentions: list[dict[str, Any]]) -> str:
    """Escape plain text and highlight validated mention ranges without parsing Markdown."""
    tokens = current_tokens()
    parts: list[str] = [f'<span style="color:{tokens.text_primary};">']
    cursor = 0
    for mention in sorted(mentions, key=lambda item: int(item.get("start", -1))):
        start = int(mention.get("start", -1))
        length = int(mention.get("length", 0))
        if start < cursor or length <= 0 or start + length > len(source):
            continue
        parts.append(html.escape(source[cursor:start]).replace("\n", "<br>"))
        token = html.escape(source[start:start + length])
        is_agent = mention.get("kind") == "agent"
        foreground = tokens.mention_agent_fg if is_agent else tokens.mention_user_fg
        background = tokens.mention_agent_bg if is_agent else tokens.mention_user_bg
        parts.append(
            f'<span style="color:{foreground};background-color:{background};font-weight:600;">{token}</span>'
        )
        cursor = start + length
    parts.append(html.escape(source[cursor:]).replace("\n", "<br>"))
    parts.append("</span>")
    return "".join(parts)

def markdown_to_safe_html(source: str) -> str:
    """Render compact chat Markdown without embedded HTML or remote images."""
    tokens = current_tokens()
    document = QTextDocument()
    features = (
        QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        | QTextDocument.MarkdownFeature.MarkdownNoHTML
    )
    document.setMarkdown(source, features)
    rendered = re.sub(r"<img\b[^>]*>", "", document.toHtml(), flags=re.IGNORECASE)
    rendered = rendered.replace("font-family:;", "font-family:'Segoe UI','Microsoft YaHei UI',sans-serif;")
    rendered = rendered.replace("font-weight:400;", f"color:{tokens.text_primary}; font-size:14px; font-weight:400;", 1)
    rendered = rendered.replace("font-size:xx-large", "font-size:16px")
    rendered = rendered.replace("font-size:x-large", "font-size:15px")
    rendered = rendered.replace("font-size:large", "font-size:14px")
    rendered = re.sub(
        r'<h([1-6]) style=" ([^"]*)margin-bottom:0px;([^"]*)">',
        lambda match: (
            f'<h{match.group(1)} style=" {match.group(2)}'
            f"margin-bottom:5px;{match.group(3)}\">"
        ),
        rendered,
    )
    rendered = re.sub(
        r'<p style=" ([^"]*)margin-bottom:0px;([^"]*)">',
        r'<p style=" \1margin-bottom:6px;\2">',
        rendered,
    )
    rendered = re.sub(
        r'<li style=" ([^"]*)margin-bottom:0px;([^"]*)">',
        r'<li style=" \1margin-bottom:2px;\2">',
        rendered,
    )
    rendered = re.sub(
        r'<pre style=" ([^"]*)">',
        f'<table width="100%" cellspacing="0" cellpadding="6" bgcolor="{tokens.code_bg}"><tr><td><pre style=" color:{tokens.code_fg}; margin-top:0px; margin-bottom:0px; \\1">',
        rendered,
    )
    rendered = rendered.replace("</pre>", "</pre></td></tr></table>")
    rendered = rendered.replace(
        "</head>",
        f"<style>a {{ color: {tokens.primary}; }} code {{ color: {tokens.code_fg}; }}</style></head>",
    )
    return rendered
