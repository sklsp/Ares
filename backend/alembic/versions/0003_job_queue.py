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
    with op.batch_alter_table("research_jobs") as batch:
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="5"))
        batch.add_column(sa.Column("idempotency_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("worker_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("run_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_research_jobs_priority", "research_jobs", ["priority"])
    op.create_unique_constraint("uq_research_jobs_idempotency_key", "research_jobs", ["idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_research_jobs_idempotency_key", "research_jobs", type_="unique")
    op.drop_index("ix_research_jobs_priority", table_name="research_jobs")
    with op.batch_alter_table("research_jobs") as batch:
        batch.drop_column("run_after")
        batch.drop_column("retry_count")
        batch.drop_column("worker_id")
        batch.drop_column("idempotency_key")
        batch.drop_column("priority")
