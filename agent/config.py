"""Centralised configuration via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Anthropic
    anthropic_api_key: str = Field(..., description="Anthropic API key")

    # Models
    planner_model: str = "claude-sonnet-4-6"
    summarizer_model: str = "claude-haiku-4-5-20251001"
    reflector_model: str = "claude-sonnet-4-6"

    # Database
    database_url: str = "postgresql+asyncpg://agent:agent@localhost:5432/agent"

    # OpenTelemetry
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "agentic-research-assistant"

    # MCP
    mcp_server_url: str = "http://localhost:8001"

    # Budget
    default_budget_usd: float = 2.00

    # Sandbox
    sandbox_image: str = "python:3.11-slim"
    sandbox_timeout_seconds: int = 10
    sandbox_memory_limit: str = "128m"

    # Tavily
    tavily_api_key: str = Field(default="", description="Tavily search API key")

    # Logging
    log_level: str = "INFO"

    # Agent limits
    max_replan_cycles: int = 3
    max_steps_per_run: int = 20
    tool_retry_attempts: int = 2
    tool_timeout_seconds: float = 30.0


# Singleton - import this everywhere
settings = Settings()
