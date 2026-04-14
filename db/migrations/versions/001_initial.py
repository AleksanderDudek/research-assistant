"""Initial schema: runs, steps, tool_calls, messages.

Revision ID: 001
Revises:
Create Date: 2026-04-14
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("ended_at", sa.DateTime, nullable=True),
        sa.Column("total_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("final_answer", sa.Text, nullable=False, server_default=""),
        sa.Column("replan_count", sa.Integer, nullable=False, server_default="0"),
    )

    op.create_table(
        "steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("content_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("ended_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_steps_run_id_ordinal", "steps", ["run_id", "ordinal"])

    op.create_table(
        "tool_calls",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("step_id", UUID(as_uuid=True), sa.ForeignKey("steps.id"), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("result_json", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
    )

    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_messages_run_id", "messages", ["run_id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("tool_calls")
    op.drop_table("steps")
    op.drop_table("runs")
