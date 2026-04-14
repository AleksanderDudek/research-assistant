"""Execute Python tool – runs code in a Docker sandbox."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from mcp_server.sandbox import run_python_in_sandbox

log = structlog.get_logger(__name__)


async def execute_python(code: str) -> dict[str, Any]:
    """Execute a Python code snippet in a sandboxed Docker container.

    The sandbox has NO network access and NO access to the host filesystem.
    stdout + stderr are captured and returned. Execution is killed after
    SANDBOX_TIMEOUT_SECONDS (default 10).

    Args:
        code: Python source code to execute.

    Returns:
        Dict with 'stdout', 'stderr', 'exit_code', 'timed_out', 'latency_ms'.
    """
    t0 = time.monotonic()
    log.info("execute_python.start", code_len=len(code))

    # run_python_in_sandbox is sync (Docker SDK is sync) – offload to thread
    result: dict[str, Any] = await asyncio.get_event_loop().run_in_executor(
        None, run_python_in_sandbox, code
    )

    result["latency_ms"] = int((time.monotonic() - t0) * 1000)
    log.info(
        "execute_python.done",
        exit_code=result["exit_code"],
        timed_out=result["timed_out"],
        latency_ms=result["latency_ms"],
    )
    return result
