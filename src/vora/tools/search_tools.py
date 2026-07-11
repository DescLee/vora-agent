from __future__ import annotations

import io
import html
import ipaddress
import re
import socket
import warnings
from contextlib import redirect_stderr
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from duckduckgo_search import DDGS

from vora.redaction import redact_sensitive_text
from vora.tools.base import BaseTool, ToolResult


class WebSearchTool(BaseTool):
    """联网搜索工具，使用 DuckDuckGo 搜索引擎（免费，无需 API key）。"""

    name = "web_search"
    description = "Search the web using DuckDuckGo. Returns a list of results with title, snippet, and URL. Use this to get current information from the internet."
    risk_level = "safe"
    is_read_only = True

    def describe_preview(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        max_results = kwargs.get("max_results", 5)
        return f"Search web for: {query} (max {max_results} results)"

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of search results to return (1-20).",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    def run(self, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: query",
                error_code="INVALID_TOOL_PARAMS",
            )
        safe_query = redact_sensitive_text(query)
        max_results = _bounded_int(kwargs.get("max_results", 5), default=5, minimum=1, maximum=20)
        if max_results is None:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="invalid max_results: must be an integer",
                error_code="INVALID_TOOL_PARAMS",
            )

        warning_records = []
        stderr_buffer = io.StringIO()
        try:
            with warnings.catch_warnings(record=True) as warning_records:
                warnings.simplefilter("always")
                with redirect_stderr(stderr_buffer):
                    with DDGS() as ddgs:
                        results = list(ddgs.text(query, max_results=max_results))
        except Exception as exc:
            captured_warnings = _format_warning_records(warning_records)
            captured_stderr = _format_captured_stderr(stderr_buffer.getvalue())
            data = _web_search_data(query, 0, captured_warnings, captured_stderr)
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=redact_sensitive_text(f"Search failed: {exc}"),
                error_code="SEARCH_FAILED",
                data=data,
            )

        captured_warnings = _format_warning_records(warning_records)
        captured_stderr = _format_captured_stderr(stderr_buffer.getvalue())
        if not results:
            data = _web_search_data(query, 0, captured_warnings, captured_stderr)
            return ToolResult(
                tool_name=self.name,
                ok=True,
                summary=f"No results found for: {safe_query}",
                content="No results found.",
                data=data,
            )

        lines: list[str] = []
        for i, r in enumerate(results, 1):
            title = redact_sensitive_text(r.get("title", "").strip())
            snippet = redact_sensitive_text(r.get("body", r.get("snippet", "")).strip())
            url = redact_sensitive_text(r.get("href", r.get("link", "")).strip())
            lines.append(f"{i}. {title}")
            if snippet:
                lines.append(f"   {_truncate(snippet, 200)}")
            if url:
                lines.append(f"   URL: {url}")
            lines.append("")

        content = "\n".join(lines).strip()
        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"Found {len(results)} results for: {safe_query}",
            content=content,
            data=_web_search_data(query, len(results), captured_warnings, captured_stderr),
        )


class FetchWebpageTool(BaseTool):
    """获取网页文本内容工具。给定 URL，返回网页的纯文本内容（去除 HTML 标签）。"""

    name = "fetch_webpage"
    description = "Fetch and extract readable text content from a webpage URL. Use this to read the full content of a page found via web_search."
    risk_level = "safe"
    is_read_only = True

    def describe_preview(self, **kwargs: Any) -> str:
        url = str(kwargs.get("url", "")).strip()
        max_chars = kwargs.get("max_chars", 8000)
        return f"Fetch webpage: {url} (max {max_chars} chars)"

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL of the webpage to fetch (must start with http:// or https://).",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of text content to return.",
                    "default": 8000,
                    "minimum": 1000,
                    "maximum": 50000,
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        }

    def run(self, **kwargs: Any) -> ToolResult:
        url = str(kwargs.get("url", "")).strip()
        if not url:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: url",
                error_code="INVALID_TOOL_PARAMS",
            )
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="Invalid URL: must start with http:// or https://",
                error_code="INVALID_TOOL_PARAMS",
            )
        if not parsed.hostname:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="Invalid URL: missing host",
                error_code="INVALID_TOOL_PARAMS",
            )
        try:
            parsed.port
        except ValueError as exc:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"Invalid URL port: {exc}",
                error_code="INVALID_TOOL_PARAMS",
            )
        max_chars = _bounded_int(kwargs.get("max_chars", 8000), default=8000, minimum=1000, maximum=50000)
        if max_chars is None:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="invalid max_chars: must be an integer",
                error_code="INVALID_TOOL_PARAMS",
            )
        protected_reason = _protected_fetch_url_reason(url)
        if protected_reason is not None:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"protected URL: {protected_reason}",
                error_code="PROTECTED_URL",
            )
        safe_url = redact_sensitive_text(url)

        try:
            resp = _fetch_with_protected_redirects(url)
            resp.raise_for_status()
            text = _strip_html_tags(resp.text)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n[... content truncated ...]"
            content_type = resp.headers.get("content-type", "")
        except ProtectedFetchUrlError as exc:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=redact_sensitive_text(f"protected URL: {exc}"),
                error_code="PROTECTED_URL",
            )
        except requests.RequestException as exc:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=redact_sensitive_text(f"Failed to fetch {url}: {exc}"),
                error_code="FETCH_FAILED",
            )

        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"Fetched {safe_url} ({len(text)} chars)",
            content=text,
            data={"url": safe_url, "chars": len(text), "content_type": content_type},
        )


def _fetch_with_protected_redirects(url: str, *, max_redirects: int = 5) -> requests.Response:
    current_url = url
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Vora/1.0; +https://github.com/vora)",
    }
    for _ in range(max_redirects + 1):
        resp = requests.get(current_url, timeout=15, headers=headers, allow_redirects=False)
        if not getattr(resp, "is_redirect", False):
            return resp
        location = resp.headers.get("location") or resp.headers.get("Location")
        if not location:
            return resp
        next_url = urljoin(current_url, location)
        protected_reason = _protected_fetch_url_reason(next_url)
        if protected_reason is not None:
            raise ProtectedFetchUrlError(f"redirect target blocked: {protected_reason}")
        current_url = next_url
    raise requests.TooManyRedirects(f"exceeded {max_redirects} redirects")


class ProtectedFetchUrlError(requests.RequestException):
    pass


def _strip_html_tags(html_content: str) -> str:
    """Strip HTML tags and extract readable text."""
    text = re.sub(r"<head[^>]*>.*?</head>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def _protected_fetch_url_reason(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return "missing host"
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        return f"invalid port: {exc}"
    try:
        addresses = set()
        for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            sockaddr = info[4]
            if sockaddr and isinstance(sockaddr[0], str):
                addresses.add(sockaddr[0])
    except socket.gaierror as exc:
        return f"host resolution failed: {exc}"
    for address in addresses:
        if _is_protected_address(address):
            return f"{host} resolves to protected address {address}"
    return None


def _is_protected_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _format_warning_records(records) -> list[str]:
    formatted = []
    for record in records:
        category = getattr(record.category, "__name__", "Warning")
        message = redact_sensitive_text(str(record.message).strip())
        if message:
            formatted.append(f"{category}: {message}")
    return formatted


def _format_captured_stderr(stderr: str) -> list[str]:
    return [redact_sensitive_text(line.strip()) for line in stderr.splitlines() if line.strip()]


def _web_search_data(
    query: str,
    result_count: int,
    captured_warnings: list[str],
    captured_stderr: list[str],
) -> dict[str, Any]:
    data: dict[str, Any] = {"result_count": result_count, "query": redact_sensitive_text(query)}
    if captured_warnings:
        data["warnings"] = captured_warnings
    if captured_stderr:
        data["stderr"] = captured_stderr
    return data


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(parsed, maximum))


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."
