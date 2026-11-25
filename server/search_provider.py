"""Bounded, server-side web search adapters. Search results are untrusted data."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .model_provider import URL_OPENER, validate_api_key


MAX_RESPONSE_BYTES = 512 * 1024
MAX_RESULTS = 5
TEST_QUERY = "Python programming language"
PROVIDERS = {"brave", "tavily", "searxng", "deepseek"}


class SearchError(RuntimeError):
    def __init__(self, message: str, category: str = "unavailable") -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class SearchSettings:
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    enabled: bool = False

    def validated(self) -> "SearchSettings":
        if not self.enabled and not self.provider:
            return SearchSettings()
        if self.provider not in PROVIDERS:
            raise ValueError("不支持的搜索服务")
        key = validate_api_key(self.api_key)
        if self.enabled and self.provider != "searxng" and not key:
            raise ValueError("搜索 API Key 不能为空")
        if self.provider == "searxng":
            if key:
                raise ValueError("SearXNG 不使用 API Key")
            base = self.base_url.strip().rstrip("/")
            parsed = urllib.parse.urlsplit(base)
            if not self.enabled and not base:
                return SearchSettings(self.provider, "", "", False)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("SearXNG 地址必须是有效的 HTTP(S) URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("SearXNG 地址不能包含凭据、查询或片段")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("非本机 SearXNG 必须使用 HTTPS")
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                address = None
            if address and (address.is_multicast or address.is_unspecified or address.is_link_local):
                raise ValueError("SearXNG 地址无效")
            return SearchSettings(self.provider, base, "", self.enabled)
        if self.base_url:
            raise ValueError("托管搜索服务不接受自定义地址")
        return SearchSettings(self.provider, "", key, self.enabled)

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "api_key_set": bool(self.api_key),
        }


def _source_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def _normalize_results(rows: Any, *, snippet_field: str) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        raise SearchError("搜索服务响应格式不兼容", "invalid_response")
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _source_url(row.get("url"))
        if not url or url in seen:
            continue
        title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()[:200]
        snippet = re.sub(r"\s+", " ", str(row.get(snippet_field) or "")).strip()[:700]
        if not title and not snippet:
            continue
        results.append({"title": title or url, "url": url, "snippet": snippet})
        seen.add(url)
        if len(results) == MAX_RESULTS:
            break
    return results


def _search_sync(settings: SearchSettings, query: str) -> list[dict[str, str]]:
    if settings.provider == "brave":
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
            {"q": query, "count": MAX_RESULTS}
        )
        body = None
        headers = {"Accept": "application/json", "X-Subscription-Token": settings.api_key}
        snippet_field = "description"
    elif settings.provider == "tavily":
        url = "https://api.tavily.com/search"
        body = json.dumps({"query": query, "search_depth": "basic", "max_results": MAX_RESULTS,
                           "include_answer": False, "include_raw_content": False}).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "Authorization": f"Bearer {settings.api_key}"}
        snippet_field = "content"
    elif settings.provider == "deepseek":
        if len(query) > 128:
            raise SearchError("DeepSeek 搜索词不能超过 128 个字符", "invalid_request")
        url = "https://api.deepseek.com/v1/tools/search"
        body = json.dumps({"query": query, "count": MAX_RESULTS, "need_content": False}).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "Authorization": f"Bearer {settings.api_key}"}
        snippet_field = "snippet"
    else:
        url = settings.base_url + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
        body = None
        headers = {"Accept": "application/json"}
        snippet_field = "content"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with URL_OPENER.open(request, timeout=8) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SearchError("搜索服务响应过大", "invalid_response")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise SearchError("搜索服务响应格式不兼容", "invalid_response")
        rows = payload.get("web", {}).get("results") if settings.provider == "brave" and isinstance(payload.get("web"), dict) else payload.get("results")
        return _normalize_results(rows, snippet_field=snippet_field)
    except SearchError:
        raise
    except urllib.error.HTTPError as exc:
        category = "authentication" if exc.code in {401, 403} else "rate_limited" if exc.code == 429 else "http_error"
        raise SearchError(f"搜索服务返回 HTTP {exc.code}", category) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SearchError("搜索服务连接或请求超时", "network") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SearchError("搜索服务响应格式不兼容", "invalid_response") from exc


async def search(settings: SearchSettings, query: str) -> list[dict[str, str]]:
    checked = settings.validated()
    if not checked.enabled:
        raise SearchError("联网搜索尚未配置或启用", "unavailable")
    clean = query.strip()
    if not clean or len(clean) > 300:
        raise SearchError("搜索词不能为空且不能超过 300 个字符", "invalid_request")
    return await asyncio.to_thread(_search_sync, checked, clean)
