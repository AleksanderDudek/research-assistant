"""Fetch URL tool – downloads a page and extracts clean text."""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
import trafilatura

log = structlog.get_logger(__name__)

_MAX_BYTES = 500_000  # 500 KB
_TIMEOUT_SECONDS = 10.0


async def fetch_url(url: str) -> dict[str, Any]:
    """Fetch a URL and return cleaned text content.

    Uses trafilatura for main-content extraction. Falls back to raw text on
    extraction failure. Caps response at 500 KB to avoid context bloat.

    Args:
        url: The URL to fetch.

    Returns:
        Dict with 'url', 'text', 'char_count', 'latency_ms'.
    """
    t0 = time.monotonic()
    log.info("fetch_url.start", url=url)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=_TIMEOUT_SECONDS,
        headers={"User-Agent": "ResearchAgent/1.0 (+research)"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    raw_bytes = response.content[:_MAX_BYTES]
    html = raw_bytes.decode("utf-8", errors="replace")

    # trafilatura extracts the main article text and discards boilerplate
    text = trafilatura.extract(html, include_links=False, include_comments=False)
    if not text:
        # fallback: strip tags naively
        import re

        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.info("fetch_url.done", url=url, char_count=len(text or ""), latency_ms=latency_ms)
    return {
        "url": url,
        "text": text or "",
        "char_count": len(text or ""),
        "latency_ms": latency_ms,
    }
