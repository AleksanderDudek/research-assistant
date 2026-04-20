# Agentic Research Assistant

A production-grade AI research agent built from first principles. Given a research question, it plans a multi-step investigation, executes tools via the Model Context Protocol, writes a cited report, and streams the result to a web UI — all while emitting full OpenTelemetry traces.

**This is not a LangChain wrapper.** Every layer is hand-rolled to make the design decisions explicit and auditable.

**[Live demo →](https://aleksanderdudek.github.io/research-assistant/)**

![Research Assistant demo](docs/assets/demo.png)

---

## What it demonstrates

| Engineering concern | How it's handled |
| --- | --- |
| Agent architecture | Plan → Execute → Reflect loop with up to 3 replan cycles |
| Tool protocol | MCP (Model Context Protocol) — the 2026 production standard |
| State & resumability | PostgreSQL — every step committed on completion, crash-safe |
| Observability | Full OpenTelemetry span tree: LLM calls, tool calls, retries |
| Cost control | Typed `BudgetExceeded` exception, halts gracefully at limit |
| Web streaming | Starlette SSE endpoint; phase-labelled status, error retry UX |
| Failure coverage | 5 dedicated tests that force specific failure modes |
| Code quality | ruff + mypy --strict, zero type errors, CI on every push |

---

## Architecture

```text
              ┌─────────────────┐     ┌─────────────────┐
   browser ───▶  Web UI (SSE)  │     │  agent CLI      │
              │  (Starlette)    │     │  (Typer/Rich)   │
              └────────┬────────┘     └────────┬────────┘
                       │                       │
                       └──────────┬────────────┘
                                  ▼
                         ┌─────────────────┐
                         │   Agent Core    │
                         │  plan→exec→reflect
                         └────────┬────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
       ┌──────────┐        ┌──────────┐        ┌──────────┐
       │ Claude   │        │ Postgres │        │   MCP    │
       │  API     │        │  (state) │        │  server  │
       └──────────┘        └──────────┘        └────┬─────┘
                                                    │
                   ┌────────────────────┬───────────┴───────────┐
                   ▼                    ▼                        ▼
           ┌──────────────┐   ┌──────────────┐        ┌──────────────┐
           │ web_search   │   │  read_pdf    │        │ execute_py   │
           │  (Tavily)    │   │  (pypdf)     │        │  (Docker)    │
           └──────────────┘   └──────────────┘        └──────────────┘

      All arrows emit OpenTelemetry spans → Jaeger UI
```

## Agent Loop

```text
User question
     │
     ▼
┌─────────┐      ┌──────────┐      ┌───────────┐
│ Planner │─────▶│ Executor │─────▶│ Reflector │
│ (JSON)  │      │ (MCP)    │      │ (verdict) │
└─────────┘      └──────────┘      └─────┬─────┘
                                         │
                     ┌───────────────────┤
                     │                   │
                  sufficient?         more steps needed?
                     │                   │
                     ▼                   ▼
               Final Answer        Replan (max 3×)
```

The planner produces a structured JSON plan before any tool is called. This means you can inspect and test the plan independently from execution — ReAct loops cannot offer this.

---

## Key Design Decisions

### Planned agents, not pure ReAct

ReAct loops are flexible but non-deterministic and hard to test. The planner produces a structured JSON plan; the executor walks it step by step. Jaeger shows a clear `plan → steps → reflect` tree rather than a flat event stream. The reflector can request additional steps (up to `max_replan_cycles = 3`).

### MCP over plain Python functions

Tools run as a separate process (the MCP server) over a standardised protocol Claude natively speaks. This gives independent resource limits per container, the ability to swap tool implementations without touching agent logic, and independent testability of each layer.

### Hand-rolled instead of LangGraph/CrewAI

Frameworks hide the hard parts. Here every design decision is explicit: how does state persist? how are retries counted? how does budget enforcement integrate with the control loop? The full agent loop is ~200 lines in `agent/core.py`. Nothing is hidden.

### PostgreSQL for state

Every step is committed as it completes. A mid-run crash → `resume <run_id>` picks up where it left off. Every run, step, tool call, and message is queryable via SQL — no proprietary datastore.

### Budget enforcement as a first-class concern

LLM costs are not predictable at plan time. The `Budget` class is injected into the LLM client and raises `BudgetExceeded`, a typed exception mapped to `HALTED_OVER_BUDGET` run status. The web UI renders halted runs with an amber card and a halted-notice banner.

### Docker sandbox for `execute_python`

Python's `exec()` gives executed code full interpreter access. The Docker sandbox uses `--network none`, `tmpfs` at `/workspace`, a memory cap, and a 10-second wall-clock timeout. Trade-off: 1–2 s cold-start latency per execution.

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
| Code sandbox | Docker SDK — no network, tmpfs, 10 s limit |
| Web search | Tavily (swappable abstraction) |
| PDF | pypdf + pdfplumber fallback |
| Web UI | Starlette + SSE streaming, HTML/PDF export, IP rate limiting |
| CLI | Typer + Rich |
| Tests | pytest, pytest-asyncio, respx, testcontainers |
| Lint / type | ruff + mypy --strict |
| CI | GitHub Actions |

---

## Quickstart

### Prerequisites

- Docker Desktop (running)
- `uv` — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- API keys: `ANTHROPIC_API_KEY` and `TAVILY_API_KEY`

### 1. Clone and configure

```bash
git clone <repo>
cd agentic-research-assistant
cp .env.example .env
# set ANTHROPIC_API_KEY and TAVILY_API_KEY in .env
```

### 2. Start services

```bash
docker compose up -d
```

| Service | Address |
| --- | --- |
| Postgres | `localhost:5432` |
| Jaeger UI | `http://localhost:16686` |
| MCP server | `localhost:8001` |
| Agent | `docker compose exec agent …` |

The web UI is included in `docker-compose.prod.yml` (port 8080). To run it locally:

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8080
```

### 3. Run a question

```bash
docker compose exec agent python -m agent.cli run \
  "Compare the 2026 pricing of Claude vs GPT-4 for a 1M-token/day workload."
```

### 4. View the trace in Jaeger

Open `http://localhost:16686`, select service `agentic-research-assistant`, and click the latest trace:

```text
agent.run
├── agent.plan
│   └── llm.call  [model=claude-sonnet-4-6, tokens=1240, cost=$0.02]
├── agent.execute.cycle_0
│   ├── tool.web_search    [latency=820ms]
│   ├── tool.web_search    [latency=740ms]
│   ├── tool.fetch_url     [latency=1200ms]
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
python -m agent.cli run "your question" --budget 2.00
python -m agent.cli resume <run_id>
python -m agent.cli show <run_id>
```

---

## Failure-Mode Test Suite

The five tests that matter most for production confidence:

| Test | What it guards against |
| --- | --- |
| `test_tool_timeout.py` | Timeout → retry once → mark step failed → allow replan |
| `test_malformed_tool_output.py` | Bad JSON → `MCPError` → graceful failure → continue |
| `test_budget_exceeded.py` | Overspend → `BudgetExceeded` → `HALTED_OVER_BUDGET` status |
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
uv run pytest tests/failure_modes/ -v   # failure modes
```

---

## Linting and Type Checking

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy agent/ mcp_server/ db/ --ignore-missing-imports
```

---

## Repository Layout

```text
agentic-research-assistant/
├── agent/
│   ├── cli.py          # Typer CLI entrypoint
│   ├── core.py         # Agent class: plan → execute → reflect loop (~200 lines)
│   ├── planner.py      # Produces JSON Plan via Claude
│   ├── executor.py     # Walks the plan, calls MCP tools
│   ├── reflector.py    # Post-execution review, generates final answer
│   ├── budget.py       # Budget class + BudgetExceeded exception
│   ├── llm_client.py   # Anthropic SDK wrapper with cost tracking + OTel
│   ├── mcp_client.py   # Thin MCP HTTP client
│   ├── state.py        # Async SQLAlchemy persistence
│   ├── models.py       # Pydantic domain models
│   ├── telemetry.py    # OTel setup
│   ├── config.py       # pydantic-settings
│   └── prompts/        # System prompts for planner and reflector
├── mcp_server/
│   ├── server.py       # MCP server (stdio / HTTP)
│   ├── sandbox.py      # Docker-based Python runner
│   └── tools/
│       ├── web_search.py    # Tavily
│       ├── fetch_url.py     # trafilatura
│       ├── read_pdf.py      # pypdf + pdfplumber fallback
│       ├── execute_python.py
│       └── search_kb.py     # FAISS local knowledge base
├── db/
│   ├── models.py       # SQLAlchemy ORM (runs, steps, tool_calls, messages)
│   ├── session.py      # Async session factory
│   └── migrations/     # Alembic
├── web/
│   ├── __init__.py
│   └── app.py          # Starlette SPA: SSE /run endpoint, IP rate limiting, HTML export
├── docs/
│   └── index.html      # Public demo page
├── tests/
│   ├── unit/           # Planner, budget, argument resolution
│   ├── integration/    # Full mocked agent run
│   └── failure_modes/  # 5 tests that assert correct failure handling
├── examples/
│   ├── run_trace_1.md  # Claude vs GPT-4 pricing comparison
│   └── run_trace_2.md  # Python asyncio changes 3.11→3.13
├── Dockerfile.agent
├── Dockerfile.mcp
├── Dockerfile.web
├── docker-compose.yml
└── docker-compose.prod.yml
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
| `RATE_LIMIT_FILE` | `/data/rate_limits.json` | Web UI rate limit state (prod) |

---

## Example Transcripts

- [run_trace_1.md](examples/run_trace_1.md) — Claude vs GPT-4 pricing for 1M tokens/day (web_search + fetch_url + execute_python)
- [run_trace_2.md](examples/run_trace_2.md) — Python asyncio changes 3.11→3.13 (triggers one replan cycle)

---

## License

MIT
