"""PDF reader tool – extracts text with pypdf, falls back to pdfplumber."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 15.0


def _extract_with_pypdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n\n".join(pages)


def _extract_with_pdfplumber(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = []
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    return "\n\n".join(pages)


async def _load_bytes(source: str) -> bytes:
    """Load PDF bytes from a local path or HTTP(S) URL."""
    if source.startswith("http://") or source.startswith("https://"):
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(source)
            resp.raise_for_status()
            return resp.content
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {source}")
    return path.read_bytes()


async def read_pdf(source: str) -> dict[str, Any]:
    """Extract text from a PDF file or URL.

    Tries pypdf first (fast, good for text PDFs). Falls back to pdfplumber
    when the extraction is empty (e.g. scanned PDFs with embedded text layers).

    Args:
        source: Local file path or HTTP(S) URL to a PDF.

    Returns:
        Dict with 'source', 'text', 'page_count', 'extractor', 'latency_ms'.
    """
    t0 = time.monotonic()
    log.info("read_pdf.start", source=source)

    data = await _load_bytes(source)

    # Count pages first with pypdf (lightweight)
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    page_count = len(reader.pages)

    text = _extract_with_pypdf(data).strip()
    extractor = "pypdf"

    if not text:
        log.info("read_pdf.fallback_pdfplumber", source=source)
        text = _extract_with_pdfplumber(data).strip()
        extractor = "pdfplumber"

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "read_pdf.done",
        source=source,
        page_count=page_count,
        char_count=len(text),
        extractor=extractor,
        latency_ms=latency_ms,
    )
    return {
        "source": source,
        "text": text,
        "page_count": page_count,
        "extractor": extractor,
        "latency_ms": latency_ms,
    }
