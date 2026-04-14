"""Web search tool – backed by Tavily with swappable abstraction."""

from __future__ import annotations

import os
import time
from typing import Any

import structlog
from tavily import TavilyClient

log = structlog.get_logger(__name__)


class SearchResult:
    def __init__(self, title: str, url: str, snippet: str) -> None:
        self.title = title
        self.url = url
        self.snippet = snippet

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


def _get_client() -> TavilyClient:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    return TavilyClient(api_key=api_key)


async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web and return structured results.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return (1-10).

    Returns:
        Dict with 'results' list and 'query' echo.
    """
    max_results = max(1, min(10, max_results))
    t0 = time.monotonic()
    log.info("web_search.start", query=query, max_results=max_results)

    client = _get_client()
    response = client.search(query=query, max_results=max_results, search_depth="basic")

    results = []
    for r in response.get("results", []):
        results.append(
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            ).to_dict()
        )

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.info("web_search.done", query=query, n_results=len(results), latency_ms=latency_ms)
    return {"query": query, "results": results, "latency_ms": latency_ms}
