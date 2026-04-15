"""Web UI for the Agentic Research Assistant.

Serves a single-page HTML interface that streams research results via SSE.
Rate-limits to 1 request per day per IP address (persisted in RATE_LIMIT_FILE).

Run with:
    uvicorn web.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from datetime import date, datetime
from pathlib import Path

import structlog
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response, StreamingResponse
from starlette.routing import Route

log = structlog.get_logger(__name__)

RATE_LIMIT_FILE = Path(os.environ.get("RATE_LIMIT_FILE", "/data/rate_limits.json"))

# ---------------------------------------------------------------------------
# Rate limiting (file-backed, single-process safe)
# ---------------------------------------------------------------------------


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_MAX_DAILY = 999
_COOLDOWN_SECS = 0


def _check_and_record(ip: str) -> tuple[bool, str]:
    """Return (allowed, error_message).

    Enforces: max 3 requests per day per IP, with a 60-minute cooldown between
    each request. Reads/writes RATE_LIMIT_FILE synchronously — safe inside a
    single-process asyncio event loop (no yield points → executes atomically).
    """
    try:
        raw: dict[str, dict[str, object]] = (
            json.loads(RATE_LIMIT_FILE.read_text()) if RATE_LIMIT_FILE.exists() else {}
        )
    except Exception:
        raw = {}

    today = date.today().isoformat()
    now = datetime.now().timestamp()
    entry: dict[str, object] = raw.get(ip, {})

    if entry.get("date") != today:
        entry = {"date": today, "count": 0, "last_ts": 0.0}

    count = int(entry.get("count", 0))
    last_ts = float(entry.get("last_ts", 0.0))

    if last_ts > 0:
        elapsed = now - last_ts
        if elapsed < _COOLDOWN_SECS:
            wait = int(_COOLDOWN_SECS - elapsed)
            mins, secs = divmod(wait, 60)
            remaining = _MAX_DAILY - count
            return (
                False,
                f"Please wait {mins}m {secs:02d}s before your next request "
                f"({remaining} remaining today).",
            )

    if count >= _MAX_DAILY:
        return False, f"Daily limit reached ({_MAX_DAILY} requests/day). Resets at midnight."

    entry["count"] = count + 1
    entry["last_ts"] = now
    entry["date"] = today
    raw[ip] = entry
    RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RATE_LIMIT_FILE.write_text(json.dumps(raw))
    return True, ""


# ---------------------------------------------------------------------------
# HTML (embedded — no template files needed)
# ---------------------------------------------------------------------------

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Research Assistant</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #f7f8fa;
    color: #1a1a2e;
    min-height: 100vh;
  }
  .container { max-width: 780px; margin: 48px auto; padding: 0 20px 60px; }
  h1 { font-size: 1.9rem; font-weight: 700; letter-spacing: -0.5px; }
  .sub  { color: #666; margin: 6px 0 28px; font-size: 0.95rem; }
  .notice {
    background: #fffbe6;
    border: 1px solid #ffe58f;
    border-radius: 8px;
    padding: 11px 16px;
    margin-bottom: 22px;
    font-size: 0.88rem;
    color: #5c4a00;
  }
  .notice strong { color: #3a2e00; }
  textarea {
    width: 100%;
    min-height: 96px;
    padding: 12px 14px;
    border: 1px solid #d0d5dd;
    border-radius: 8px;
    font-size: 1rem;
    font-family: inherit;
    resize: vertical;
    outline: none;
    transition: border-color .15s;
  }
  textarea:focus { border-color: #0070f3; }
  .row { display: flex; gap: 10px; margin-top: 12px; align-items: center; }
  button {
    padding: 10px 26px;
    background: #0070f3;
    color: #fff;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
    transition: background .15s;
    white-space: nowrap;
  }
  button:hover:not(:disabled) { background: #005ad4; }
  button:disabled { background: #aaa; cursor: not-allowed; }
  #hint { font-size: 0.82rem; color: #888; }
  #output { margin-top: 30px; }
  .status {
    display: flex;
    align-items: center;
    gap: 10px;
    color: #555;
    font-size: 0.95rem;
  }
  .spinner {
    width: 18px; height: 18px;
    border: 2px solid #ccc;
    border-top-color: #0070f3;
    border-radius: 50%;
    animation: spin .75s linear infinite;
    flex-shrink: 0;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .answer {
    background: #fff;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    padding: 22px 24px;
    white-space: pre-wrap;
    line-height: 1.7;
    font-size: 0.97rem;
  }
  .error {
    background: #fff5f5;
    border-color: #fc8181;
    color: #c53030;
  }
</style>
</head>
<body>
<div class="container">
  <h1>Research Assistant</h1>
  <p class="sub">Powered by Claude &amp; MCP research tools</p>

  <div class="notice">
    &#9432;&nbsp; <strong>Free tier:</strong> 1 research request per day per IP address.
    Results may take 1&ndash;3 minutes.
  </div>

  <form id="form">
    <textarea
      id="question"
      placeholder="Ask a research question, e.g. &ldquo;Latest advances in fusion energy?&rdquo;"
      required
    ></textarea>
    <div class="row">
      <button type="submit" id="btn">Research</button>
      <span id="hint"></span>
    </div>
  </form>

  <div id="output"></div>
</div>

<script>
  const form = document.getElementById('form');
  const btn  = document.getElementById('btn');
  const hint = document.getElementById('hint');
  const out  = document.getElementById('output');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const question = document.getElementById('question').value.trim();
    if (!question) return;

    btn.disabled = true;
    btn.textContent = 'Researching\u2026';
    hint.textContent = '';
    out.innerHTML = '<div class="status">'
      + '<div class="spinner"></div>'
      + '<span id="stxt">Starting\u2026</span></div>';

    try {
      const resp = await fetch('/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });

      if (!resp.body) {
        showError('Browser does not support streaming responses.');
        return;
      }

      const reader  = resp.body.getReader();
      const decoder = new TextDecoder();
      let   buffer  = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (line.startsWith('data: ')) handleEvent(line.slice(6));
        }
      }
    } catch (err) {
      showError('Network error: ' + String(err));
    } finally {
      btn.disabled   = false;
      btn.textContent = 'Research';
    }
  });

  function handleEvent(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }

    if (msg.type === 'status') {
      const el = document.getElementById('stxt');
      if (el) el.textContent = msg.text;
    } else if (msg.type === 'done') {
      out.innerHTML = '<div class="answer">' + esc(msg.answer) + '</div>';
    } else if (msg.type === 'error') {
      showError(msg.message);
    }
  }

  function showError(msg) {
    out.innerHTML = '<div class="answer error">' + esc(msg) + '</div>';
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def homepage(request: Request) -> HTMLResponse:
    return HTMLResponse(_HTML, headers=_SECURITY_HEADERS)


_MAX_QUESTION = 2000
_SSE = "data: "  # SSE line prefix
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


async def run_research(request: Request) -> Response:
    try:
        body = await request.json()
        question = str(body.get("question", "")).strip()
    except Exception:
        return Response("Invalid request", status_code=400)

    if not question:
        return Response("Question is required", status_code=400)

    if len(question) > _MAX_QUESTION:
        return Response(f"Question too long (max {_MAX_QUESTION} characters)", status_code=400)

    ip = _get_ip(request)

    async def stream() -> AsyncGenerator[str, None]:
        # ── Rate limit ──────────────────────────────────────────────────────
        allowed, rate_msg = _check_and_record(ip)
        if not allowed:
            yield _SSE + json.dumps({"type": "error", "message": rate_msg}) + "\n\n"
            return

        yield _SSE + json.dumps({"type": "status", "text": "Starting research\u2026"}) + "\n\n"

        # ── Agent run in background task ────────────────────────────────────
        from agent.budget import Budget  # lazy import — avoids eager settings load
        from agent.core import Agent

        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        async def _run() -> None:
            try:
                record = await Agent().run(question=question, budget=Budget(limit_usd=2.00))
                await queue.put({"type": "done", "answer": record.final_answer or "(no answer)"})
            except Exception as exc:
                log.exception("agent.run_error", error=str(exc))
                await queue.put(
                    {
                        "type": "error",
                        "message": "Research failed due to an internal error. Please try again.",
                    }
                )

        task = asyncio.create_task(_run())
        tick = 0
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield _SSE + json.dumps(msg) + "\n\n"
                    # Trailing SSE comment ensures nginx flushes the final chunk
                    # before the generator returns, preventing ERR_INCOMPLETE_CHUNKED_ENCODING
                    yield ": done\n\n"
                    return
                except TimeoutError:
                    tick += 1
                    yield (
                        _SSE
                        + json.dumps(
                            {
                                "type": "status",
                                "text": f"Researching\u2026 ({tick * 5}s elapsed)",
                            }
                        )
                        + "\n\n"
                    )
        finally:
            task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            **_SECURITY_HEADERS,
        },
    )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO))


_configure_logging()

app = Starlette(
    routes=[
        Route("/", homepage),
        Route("/run", run_research, methods=["POST"]),
    ]
)

# Allow GitHub Pages (and any other origin) to call the /run endpoint.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type"],
)
