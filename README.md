# Agentic Research Assistant

A production-grade research agent with real MCP tool integration, persistent state, and full OpenTelemetry tracing. Given a research question, the agent plans a multi-step investigation, executes MCP-exposed tools (web search, PDF reader, Python sandbox, URL fetcher, knowledge base), produces a cited report, and logs every decision as an OpenTelemetry span viewable in Jaeger.

```bash
docker compose up
docker compose exec agent python -m agent.cli run \
  "Compare 2026 pricing of Claude vs GPT-4 for a 1M-token/day workload."
```

---

## Why this project exists

This is not a LangChain wrapper. It is a hand-rolled research agent that demonstrates the patterns senior AI engineers are expected to understand in 2026:

- **Model Context Protocol (MCP):** Tools run as a separate process, exposed over a standardised protocol that Claude natively supports. This is the 2026 production pattern.
- **Planned agents over pure ReAct:** The agent produces a structured JSON plan, executes it, then reflects. Plans are debuggable; ReAct loops are not.
- **Persistent state:** Every run is resumable. Crash mid-run → `resume <run_id>`.
- **Full observability:** Every LLM call and tool call is an OTel span. Open Jaeger and watch the agent think.
- **Cost budgets:** The agent halts gracefully when the USD budget is exceeded — the production reality nobody teaches.
- **Failure-mode tests:** 5 dedicated tests that force specific failure modes and assert correct handling.

---

## System Architecture

```text
              ┌─────────────────┐
   user ──────▶  agent CLI      │
              │  (Python app)   │
              └────────┬────────┘
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Claude   │ │ Postgres │ │   MCP    │
    │  API     │ │  (state) │ │  server  │
    └──────────┘ └──────────┘ └────┬─────┘
                                   │
              ┌────────────────────┼──────────────────┐
              ▼                    ▼                   ▼
      ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
      │ web_search   │   │  read_pdf    │   │ execute_py   │
      │  (tavily)    │   │  (pypdf)     │   │  (docker)    │
      └──────────────┘   └──────────────┘   └──────────────┘

      All arrows emit OpenTelemetry spans → Jaeger UI
```

## Single-Run Flow

```text
User question
     │
     ▼
┌─────────┐      ┌──────────┐      ┌───────────┐
│ Planner │─────▶│ Executor │─────▶│ Reflector │
│ (plan)  │      │(execute) │      │ (reflect) │
└─────────┘      └──────────┘      └─────┬─────┘
                                         │
                     ┌───────────────────┤
                     │                   │
                  sufficient?         more steps?
                     │                   │
                     ▼                   ▼
               Final Answer        Replan (max 3x)
```

---

## Stack

| Layer | Technology |
| --- | --- |
| Language | Python 3.11 |
| Package manager | `uv` |
| LLM | `anthropic` SDK — Sonnet 4 (plan/reflect), Haiku 4.5 (summarise) |
| MCP | Official `mcp` Python SDK |
| State | PostgreSQL 16 + async SQLAlchemy + Alembic |
| Observability | OpenTelemetry → Jaeger |
| Code sandbox | Docker SDK — no network, tmpfs, 10s limit |
| Web search | Tavily (swappable abstraction) |
| PDF | pypdf + pdfplumber fallback |
| CLI | Typer + Rich |
| Tests | pytest, pytest-asyncio, respx, testcontainers |
| Lint/type | ruff + mypy --strict |
| CI | GitHub Actions |

---

## Quickstart

### Prerequisites

- Docker Desktop (running)
- `uv` (install: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- API keys: `ANTHROPIC_API_KEY` and `TAVILY_API_KEY`

### 1. Clone and configure

```bash
git clone <repo>
cd agentic-research-assistant
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and TAVILY_API_KEY
```

### 2. Start services

```bash
docker compose up -d
```

Services started:

- **postgres** → `localhost:5432`
- **jaeger UI** → `http://localhost:16686`
- **mcp-server** → `localhost:8001`
- **agent** → ready for `docker compose exec`

### 3. Run a question

```bash
docker compose exec agent python -m agent.cli run \
  "Compare the 2026 pricing of Claude vs GPT-4 for a 1M-token/day workload, showing your math."
```

### 4. View the trace in Jaeger

Open `http://localhost:16686`, select service `agentic-research-assistant`, and click on the latest trace:

```text
agent.run
├── agent.plan
│   └── llm.call  [model=claude-sonnet-4-6, tokens=1240, cost=$0.02]
├── agent.execute.cycle_0
│   ├── tool.web_search  [latency=820ms]
│   ├── tool.web_search  [latency=740ms]
│   ├── tool.fetch_url   [latency=1200ms]
│   ├── tool.fetch_url   [latency=980ms]
│   └── tool.execute_python [latency=3400ms]
└── agent.reflect
    └── llm.call  [model=claude-sonnet-4-6, tokens=3100, cost=$0.41]
```

### 5. Resume a killed run

```bash
docker compose exec agent python -m agent.cli resume a1b2c3d4-...
```

---

## CLI Reference

```bash
python -m agent.cli run "your research question" --budget 2.00
python -m agent.cli resume <run_id>
python -m agent.cli show <run_id>
```

---

## Repository Layout

```text
agentic-research-assistant/
├── agent/
│   ├── cli.py          # Typer CLI entrypoint
│   ├── core.py         # Agent class: plan → execute → reflect loop
│   ├── planner.py      # produces JSON Plan via Claude
│   ├── executor.py     # walks the plan, calls MCP tools
│   ├── reflector.py    # post-execution review, generates final answer
│   ├── budget.py       # Budget class + BudgetExceeded exception
│   ├── llm_client.py   # Anthropic SDK wrapper with cost + OTel
│   ├── mcp_client.py   # thin MCP HTTP client
│   ├── state.py        # async SQLAlchemy persistence
│   ├── models.py       # Pydantic domain models
│   ├── telemetry.py    # OTel setup
│   ├── config.py       # pydantic-settings
│   └── prompts/        # system prompts for planner and reflector
├── mcp_server/
│   ├── server.py       # MCP server (stdio / HTTP)
│   ├── sandbox.py      # Docker-based Python runner (documented design)
│   └── tools/
│       ├── web_search.py   (Tavily)
│       ├── fetch_url.py    (trafilatura)
│       ├── read_pdf.py     (pypdf + pdfplumber)
│       ├── execute_python.py
│       └── search_kb.py    (FAISS local KB)
├── db/
│   ├── models.py       # SQLAlchemy ORM (runs, steps, tool_calls, messages)
│   ├── session.py      # async session factory
│   └── migrations/     # Alembic
├── tests/
│   ├── unit/           # planner, budget, argument resolution
│   ├── integration/    # full mocked agent run
│   └── failure_modes/  # the differentiator
├── examples/
│   ├── run_trace_1.md  # Claude vs GPT-4 pricing comparison
│   └── run_trace_2.md  # Python asyncio changes 3.11 to 3.13
├── Dockerfile.agent
├── Dockerfile.mcp
└── docker-compose.yml
```

---

## Design Decisions

### Why planned agents instead of pure ReAct?

ReAct loops are flexible but produce non-deterministic behaviour that is difficult to test, trace, or explain. A planned agent produces a structured JSON plan before executing any tools. This means:

1. You can inspect the plan before execution.
2. Test coverage is tractable: mock the planner output and verify executor behaviour independently.
3. Jaeger traces show a clear tree — plan → steps → reflect. A ReAct loop produces a flat stream.

The downside is that plans can be wrong. The reflector handles this by requesting additional steps (up to `max_replan_cycles = 3`).

### Why MCP instead of plain Python functions?

MCP is the protocol Claude natively speaks for tool use. Running tools as a separate process means:

1. The agent container and tool containers have separate resource limits.
2. You can swap tool implementations without touching agent logic.
3. The MCP server is independently deployable and testable.

### Why hand-rolled instead of LangGraph / CrewAI?

For demonstrating systems thinking, a hand-rolled agent forces every design choice to be explicit: how does state persist? how are retries counted? how does budget enforcement integrate with the control loop? The full agent loop is ~200 lines in `agent/core.py`. There is nothing hidden.

### Why PostgreSQL for state?

Two reasons: **resumability** (every step is committed as it completes, enabling crash recovery) and **auditability** (every run, step, tool call, and message is queryable via SQL).

### Why budget enforcement is mandatory

LLM costs are not predictable at plan-time. The `Budget` class makes this a first-class concern — it is injected into the LLM client and raises `BudgetExceeded`, a typed exception mapped to `HALTED_OVER_BUDGET` status. No silent cost explosions.

### execute_python sandbox: why Docker and not exec()

Python's `exec()` gives executed code full interpreter access — filesystem, network, environment variables, and all installed packages. The Docker sandbox uses `--network none`, `tmpfs` at `/workspace`, a memory cap, and a wall-clock timeout. The tradeoff is 1–2s cold-start latency per execution.

---

## Failure-Mode Test Suite

| Test | What it guards against |
| --- | --- |
| `test_tool_timeout.py` | Timeout → retry once → mark step failed → allow replan |
| `test_malformed_tool_output.py` | Bad JSON → MCPError → graceful failure → continue |
| `test_budget_exceeded.py` | Overspend → `BudgetExceeded` → `HALTED_OVER_BUDGET` |
| `test_model_refusal.py` | Claude refusal → treat as sufficient → clean exit |
| `test_infinite_loop_guard.py` | Always-insufficient reflector → halt after 3 replans |

```bash
uv run pytest tests/failure_modes/ -v
```

---

## Running Tests

```bash
uv run pytest -v                        # all tests
uv run pytest tests/unit/ -v            # unit only
uv run pytest tests/failure_modes/ -v   # failure modes only
```

---

## Linting and Type Checking

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy agent/ mcp_server/ db/ --ignore-missing-imports
```

---

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | required | Anthropic API key |
| `TAVILY_API_KEY` | — | Tavily search key |
| `DATABASE_URL` | `postgresql+asyncpg://agent:agent@localhost:5432/agent` | Postgres connection |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Jaeger OTLP gRPC |
| `MCP_SERVER_URL` | `http://localhost:8001` | MCP server address |
| `DEFAULT_BUDGET_USD` | `2.00` | Per-run USD budget |
| `SANDBOX_TIMEOUT_SECONDS` | `10` | Max time for sandboxed code |
| `MAX_REPLAN_CYCLES` | `3` | Hard replan cap |

---

## Example Transcripts

- [run_trace_1.md](examples/run_trace_1.md) — Claude vs GPT-4 pricing for 1M tokens/day (web_search + fetch_url + execute_python)
- [run_trace_2.md](examples/run_trace_2.md) — Python asyncio changes 3.11→3.13 (web_search + fetch_url, triggers one replan)

---

## What is intentionally not built

- No web UI (Typer CLI is the interface)
- No authentication or multi-user support
- No parallel tool execution within a step
- No LangChain/LangGraph/CrewAI
- No cross-run memory

---

## Stretch Goals

1. Parallel tool execution for independent steps
2. FastAPI wrapper with `POST /runs` and SSE span streaming
3. Second MCP server for a different domain (GitHub, Google Drive)
4. Static HTML trace viewer for README demos

---

## License

MIT
