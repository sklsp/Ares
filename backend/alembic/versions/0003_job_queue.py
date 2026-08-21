"""durable job queue fields

Revision ID: 0003_job_queue
Revises: 0002_market_intelligence
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_job_queue"
down_revision: Union[str, None] = "0002_market_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("research_jobs")}
    additions = {
        "priority": sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        "idempotency_key": sa.Column("idempotency_key", sa.String(64), nullable=True),
        "worker_id": sa.Column("worker_id", sa.String(128), nullable=True),
        "retry_count": sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        "run_after": sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
    }
    with op.batch_alter_table("research_jobs") as batch:
        for name, column in additions.items():
            if name not in existing:
                batch.add_column(column)
    index_names = {index["name"] for index in inspector.get_indexes("research_jobs")}
    if "ix_research_jobs_priority" not in index_names:
        op.create_index("ix_research_jobs_priority", "research_jobs", ["priority"])
    if "uq_research_jobs_idempotency_key" not in index_names:
        op.create_index(
            "uq_research_jobs_idempotency_key", "research_jobs",
            ["idempotency_key"], unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_research_jobs_idempotency_key", table_name="research_jobs")
    op.drop_index("ix_research_jobs_priority", table_name="research_jobs")
    with op.batch_alter_table("research_jobs") as batch:
        batch.drop_column("run_after")
        batch.drop_column("retry_count")
        batch.drop_column("worker_id")
        batch.drop_column("idempotency_key")
        batch.drop_column("priority")
