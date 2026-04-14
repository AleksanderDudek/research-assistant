"""Knowledge-base search tool – local FAISS vector store with sentence-transformers.

This tool exposes a small local knowledge base as an MCP tool. Documents are
loaded from KB_DOCUMENTS_DIR (default ./kb_documents/) at startup.

If you built Project 1 (RAG system), replace the FAISS logic here with an
HTTP call to its /query endpoint – the MCP interface stays identical.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_KB_DIR = Path(os.environ.get("KB_DOCUMENTS_DIR", "./kb_documents"))
_TOP_K = 5

# Lazy-loaded index
_index: Any = None
_documents: list[dict[str, str]] = []


def _ensure_index() -> None:
    """Build or load the FAISS index from text files in KB_DOCUMENTS_DIR."""
    global _index, _documents

    if _index is not None:
        return  # already loaded

    try:
        import faiss  # type: ignore[import]
        import numpy as np
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
    except ImportError:
        log.warning("search_kb.deps_missing", msg="faiss/sentence-transformers not installed; KB search disabled")
        _index = "disabled"
        return

    if not _KB_DIR.exists():
        log.warning("search_kb.no_dir", path=str(_KB_DIR))
        _index = "disabled"
        return

    model = SentenceTransformer("all-MiniLM-L6-v2")
    _documents = []
    texts = []

    for f in sorted(_KB_DIR.iterdir()):
        if f.suffix in {".txt", ".md", ".json"}:
            content = f.read_text(encoding="utf-8")
            if f.suffix == ".json":
                try:
                    entries = json.loads(content)
                    if isinstance(entries, list):
                        for e in entries:
                            _documents.append({"source": f.name, "text": str(e)})
                            texts.append(str(e))
                    continue
                except json.JSONDecodeError:
                    pass
            _documents.append({"source": f.name, "text": content})
            texts.append(content)

    if not texts:
        _index = "disabled"
        return

    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    dim = embeddings.shape[1]
    idx = faiss.IndexFlatL2(dim)
    idx.add(embeddings.astype(np.float32))  # type: ignore[arg-type]
    _index = (idx, model)
    log.info("search_kb.index_built", n_docs=len(_documents))


async def search_knowledge_base(query: str, top_k: int = _TOP_K) -> dict[str, Any]:
    """Search the local knowledge base for relevant passages.

    Args:
        query: The query string to search for.
        top_k: Number of top results to return (default 5).

    Returns:
        Dict with 'query', 'results' (list of {source, text, score}).
    """
    t0 = time.monotonic()
    log.info("search_kb.start", query=query)

    _ensure_index()

    if _index == "disabled" or _index is None:
        return {
            "query": query,
            "results": [],
            "note": "Knowledge base not available (no index or missing dependencies)",
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }

    import numpy as np
    idx, model = _index

    embedding = model.encode([query], convert_to_numpy=True, show_progress_bar=False)
    k = min(top_k, len(_documents))
    distances, indices = idx.search(embedding.astype(np.float32), k)

    results = []
    for dist, i in zip(distances[0], indices[0], strict=False):
        if i < 0:
            continue
        doc = _documents[i]
        results.append({
            "source": doc["source"],
            "text": doc["text"][:2000],  # cap per-result size
            "score": float(1.0 / (1.0 + dist)),  # convert L2 to similarity-ish
        })

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.info("search_kb.done", query=query, n_results=len(results), latency_ms=latency_ms)
    return {"query": query, "results": results, "latency_ms": latency_ms}
