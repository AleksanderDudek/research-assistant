"""CLI entrypoint.

Usage:
    python -m agent.cli run "What is X?" --budget 2.00
    python -m agent.cli resume <run_id>
    python -m agent.cli show <run_id>
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
import typer
from rich.console import Console
from rich.panel import Panel

from agent.budget import Budget
from agent.config import settings
from agent.telemetry import configure_telemetry

app = typer.Typer(name="agent", add_completion=False)
console = Console()
log = structlog.get_logger(__name__)


def _setup() -> None:
    """Configure logging and telemetry before any command runs."""
    import logging

    import structlog

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    configure_telemetry(console_fallback=False)


@app.command()
def run(
    question: str = typer.Argument(..., help="The research question to investigate"),
    budget: float = typer.Option(settings.default_budget_usd, help="Max USD budget for this run"),
    mcp_url: str | None = typer.Option(None, help="Override MCP server URL"),
) -> None:
    """Run the research agent on a new question."""
    _setup()

    async def _run() -> None:
        from agent.core import Agent

        agent = Agent(mcp_url=mcp_url)
        b = Budget(limit_usd=budget)

        console.print(Panel(f"[bold green]Research question:[/] {question}"))
        console.print(f"Budget: ${budget:.2f}")

        run_record = await agent.run(question=question, budget=b)

        console.print(f"\n[bold]Run ID:[/] {run_record.id}")
        console.print(f"[bold]Status:[/] {run_record.status.value}")
        console.print(f"[bold]Cost:[/] ${run_record.total_cost_usd:.4f}")
        console.print(f"[bold]Replan cycles:[/] {run_record.replan_count}")
        console.print()
        console.print(Panel(run_record.final_answer or "(no answer)", title="Final Answer"))

    asyncio.run(_run())


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="UUID of the run to resume"),
    budget: float = typer.Option(settings.default_budget_usd, help="Max USD budget for this run"),
    mcp_url: str | None = typer.Option(None, help="Override MCP server URL"),
) -> None:
    """Resume a previously started run."""
    _setup()

    async def _resume() -> None:
        from agent.core import Agent

        parsed_id = uuid.UUID(run_id)
        agent = Agent(mcp_url=mcp_url)
        b = Budget(limit_usd=budget)

        console.print(Panel(f"[bold yellow]Resuming run:[/] {run_id}"))
        run_record = await agent.run(question="", budget=b, resume_run_id=parsed_id)
        console.print(f"[bold]Status:[/] {run_record.status.value}")
        console.print(Panel(run_record.final_answer or "(no answer)", title="Final Answer"))

    asyncio.run(_resume())


@app.command()
def show(
    run_id: str = typer.Argument(..., help="UUID of the run to display"),
) -> None:
    """Display the final answer and status of a completed run."""
    _setup()

    async def _show() -> None:
        from agent.state import load_run

        parsed_id = uuid.UUID(run_id)
        run_record = await load_run(parsed_id)
        console.print(f"[bold]Status:[/] {run_record.status.value}")
        console.print(f"[bold]Cost:[/] ${run_record.total_cost_usd:.4f}")
        console.print(Panel(run_record.final_answer or "(no answer)", title="Final Answer"))

    asyncio.run(_show())


if __name__ == "__main__":
    app()
