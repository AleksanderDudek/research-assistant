"""Docker-based Python sandbox for safe code execution.

Design rationale
----------------
We deliberately do NOT use Python's built-in exec() or subprocess.run() for
user-supplied code. The risks are:

1. exec() gives full access to the interpreter process: filesystem, network,
   environment variables, and the ability to import anything installed.
2. subprocess.run() with a system Python still inherits the network namespace
   and the host filesystem (unless carefully namespaced).

Instead, every code snippet is run inside a short-lived Docker container:
  - Image: python:3.11-slim (no extra packages)
  - Network: disabled (--network none) – the container cannot make outbound
    connections. This is verified by the test suite.
  - Filesystem: a tmpfs is mounted at /workspace; no host paths are mounted.
  - CPU/memory: limited via Docker's --memory flag and the timeout.
  - Wall-clock timeout: 10 seconds (configurable). The container is killed and
    removed on timeout.
  - User: the default (root inside the container is unavoidable for slim images,
    but the network isolation is the primary defence).

This approach means a malicious snippet cannot: access the host filesystem,
call external services, or persist state between runs.
"""

from __future__ import annotations

import os
import time
from typing import Any

import docker
import docker.errors
import structlog

log = structlog.get_logger(__name__)

_SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "python:3.11-slim")
_SANDBOX_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT_SECONDS", "10"))
_MEMORY_LIMIT = os.environ.get("SANDBOX_MEMORY_LIMIT", "128m")


def run_python_in_sandbox(code: str) -> dict[str, Any]:
    """Run a Python snippet in an isolated Docker container.

    Args:
        code: Python source code to execute.

    Returns:
        Dict with 'stdout', 'stderr', 'exit_code', 'timed_out', 'latency_ms'.
    """
    t0 = time.monotonic()
    client = docker.from_env()

    try:
        container = client.containers.run(
            image=_SANDBOX_IMAGE,
            command=["python3", "-c", code],
            detach=True,
            network_mode="none",        # no network access
            mem_limit=_MEMORY_LIMIT,    # memory cap
            memswap_limit=_MEMORY_LIMIT,
            read_only=False,            # tmpfs needs write
            tmpfs={"/workspace": "rw,noexec,nosuid,size=64m"},
            working_dir="/workspace",
            remove=False,               # we remove manually after capture
        )

        timed_out = False
        try:
            exit_status = container.wait(timeout=_SANDBOX_TIMEOUT)
            exit_code: int = exit_status.get("StatusCode", -1)
        except Exception:
            timed_out = True
            exit_code = -1
            container.kill()

        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "sandbox.run",
        exit_code=exit_code,
        timed_out=timed_out,
        latency_ms=latency_ms,
    )
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "latency_ms": latency_ms,
    }
