"""tenant scoping

Revision ID: 0005_tenant_scoping
Revises: 0004_identity
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tenant_scoping"
down_revision: Union[str, None] = "0004_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("agent_runs", "external_stores", "research_jobs", "opportunities"):
        inspector = sa.inspect(op.get_bind())
        existing = {column["name"] for column in inspector.get_columns(table)}
        if "organization_id" not in existing:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        index_name = f"ix_{table}_organization_id"
        indexes = {index["name"] for index in inspector.get_indexes(table)}
        if index_name not in indexes:
            op.create_index(index_name, table, ["organization_id"])

    # Legacy unique constraint on external_stores.domain prevented two
    # organizations from tracking the same domain independently; replace it
    # with a per-organization unique index.
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"]: index for index in inspector.get_indexes("external_stores")}
    if "ix_external_stores_domain" in indexes and indexes["ix_external_stores_domain"].get("unique"):
        op.drop_index("ix_external_stores_domain", table_name="external_stores")
    if "uq_external_stores_org_domain" not in indexes:
        op.create_index(
            "uq_external_stores_org_domain", "external_stores",
            ["organization_id", "domain"], unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_external_stores_org_domain", table_name="external_stores")
    for table in ("opportunities", "research_jobs", "external_stores", "agent_runs"):
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("organization_id")
