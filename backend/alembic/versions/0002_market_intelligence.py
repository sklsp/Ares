"""market intelligence persistence

Revision ID: 0002_market_intelligence
Revises: 0001_initial
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import Text
from alembic import op

revision: str = "0002_market_intelligence"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")


def upgrade() -> None:
    op.create_table("external_stores", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("domain", sa.String(255), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("niche", sa.String(160), nullable=False), sa.Column("platform", sa.String(40), nullable=False), sa.Column("country", sa.String(8), nullable=False), sa.Column("product_count", sa.Integer()), sa.Column("metadata", JSON, nullable=False), sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_crawled_at", sa.DateTime(timezone=True)), sa.Column("crawl_status", sa.String(24), nullable=False))
    op.create_index("ix_external_stores_domain", "external_stores", ["domain"], unique=True)
    op.create_table("external_products", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("store_id", sa.Integer(), sa.ForeignKey("external_stores.id", ondelete="CASCADE"), nullable=False), sa.Column("source_url", sa.Text(), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("normalized_name", sa.String(255), nullable=False), sa.Column("brand", sa.String(160), nullable=False), sa.Column("category", sa.String(160), nullable=False), sa.Column("price", sa.Float()), sa.Column("currency", sa.String(8), nullable=False), sa.Column("availability", sa.String(40), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("image_url", sa.Text(), nullable=False), sa.Column("attributes", JSON, nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_external_products_source_url", "external_products", ["source_url"], unique=True)
    op.create_index("ix_external_products_store_id", "external_products", ["store_id"])
    op.create_index("ix_external_products_normalized_name", "external_products", ["normalized_name"])
    op.create_index("ix_external_products_last_seen_at", "external_products", ["last_seen_at"])
    op.create_table("product_snapshots", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("external_product_id", sa.Integer(), sa.ForeignKey("external_products.id", ondelete="CASCADE"), nullable=False), sa.Column("price", sa.Float()), sa.Column("availability", sa.String(40), nullable=False), sa.Column("review_count", sa.Integer()), sa.Column("raw", JSON, nullable=False), sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_product_snapshots_external_product_id", "product_snapshots", ["external_product_id"])
    op.create_index("ix_product_snapshots_captured_at", "product_snapshots", ["captured_at"])
    op.create_table("research_jobs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("objective", sa.Text(), nullable=False), sa.Column("query", sa.String(255), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("stage", sa.String(80), nullable=False), sa.Column("stats", JSON, nullable=False), sa.Column("error", sa.Text()), sa.Column("result", JSON), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_research_jobs_query", "research_jobs", ["query"])
    op.create_index("ix_research_jobs_status", "research_jobs", ["status"])
    op.create_index("ix_research_jobs_created_at", "research_jobs", ["created_at"])
    op.create_table("opportunities", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("research_job_id", sa.Integer(), sa.ForeignKey("research_jobs.id", ondelete="SET NULL")), sa.Column("type", sa.String(40), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("recommended_action", sa.Text(), nullable=False), sa.Column("evidence", JSON, nullable=False), sa.Column("source_urls", JSON, nullable=False), sa.Column("score", sa.Float(), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("competition_level", sa.String(24), nullable=False), sa.Column("demand_signals", JSON, nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_opportunities_research_job_id", "opportunities", ["research_job_id"])
    op.create_index("ix_opportunities_type", "opportunities", ["type"])
    op.create_index("ix_opportunities_score", "opportunities", ["score"])
    op.create_index("ix_opportunities_status", "opportunities", ["status"])
    op.create_index("ix_opportunities_discovered_at", "opportunities", ["discovered_at"])
    op.create_table("opportunity_evidence", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False), sa.Column("source_url", sa.Text(), nullable=False), sa.Column("source_domain", sa.String(255), nullable=False), sa.Column("claim", sa.Text(), nullable=False), sa.Column("extraction_method", sa.String(80), nullable=False), sa.Column("observed_value", JSON, nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_opportunity_evidence_opportunity_id", "opportunity_evidence", ["opportunity_id"])
    op.create_index("ix_opportunity_evidence_source_domain", "opportunity_evidence", ["source_domain"])


def downgrade() -> None:
    op.drop_table("opportunity_evidence")
    op.drop_table("opportunities")
    op.drop_table("research_jobs")
    op.drop_table("product_snapshots")
    op.drop_table("external_products")
    op.drop_table("external_stores")
