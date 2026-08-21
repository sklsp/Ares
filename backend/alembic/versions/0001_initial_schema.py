"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-08-21 19:09:49.800234
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agent_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.String(length=64), nullable=False),
    sa.Column('user_request', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('final_response', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('messages', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('pending_tool_calls', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('iterations', sa.Integer(), nullable=False),
    sa.Column('tool_calls_made', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agent_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_runs_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_runs_started_at'), ['started_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_runs_status'), ['status'], unique=False)

    op.create_table('products',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('price', sa.Float(), nullable=False),
    sa.Column('category', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_products_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_products_sku'), ['sku'], unique=True)

    op.create_table('agent_steps',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('agent_run_id', sa.Integer(), nullable=False),
    sa.Column('step_number', sa.Integer(), nullable=False),
    sa.Column('step_type', sa.String(length=32), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('tool_name', sa.String(length=64), nullable=True),
    sa.Column('input', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('output', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agent_steps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_steps_agent_run_id'), ['agent_run_id'], unique=False)
        batch_op.create_index('ix_agent_steps_run_number', ['agent_run_id', 'step_number'], unique=False)

    op.create_table('approval_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('agent_run_id', sa.Integer(), nullable=False),
    sa.Column('tool_name', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('preview', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('approval_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approval_requests_agent_run_id'), ['agent_run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_approval_requests_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_approval_requests_status'), ['status'], unique=False)

    op.create_table('inventory',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('reorder_point', sa.Integer(), nullable=False),
    sa.Column('warehouse', sa.String(length=40), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('inventory', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_inventory_product_id'), ['product_id'], unique=True)

    op.create_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('total', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_product_id'), ['product_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_product_id'))
        batch_op.drop_index(batch_op.f('ix_orders_created_at'))

    op.drop_table('orders')
    with op.batch_alter_table('inventory', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inventory_product_id'))

    op.drop_table('inventory')
    with op.batch_alter_table('approval_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_approval_requests_status'))
        batch_op.drop_index(batch_op.f('ix_approval_requests_created_at'))
        batch_op.drop_index(batch_op.f('ix_approval_requests_agent_run_id'))

    op.drop_table('approval_requests')
    with op.batch_alter_table('agent_steps', schema=None) as batch_op:
        batch_op.drop_index('ix_agent_steps_run_number')
        batch_op.drop_index(batch_op.f('ix_agent_steps_agent_run_id'))

    op.drop_table('agent_steps')
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_products_sku'))
        batch_op.drop_index(batch_op.f('ix_products_category'))

    op.drop_table('products')
    with op.batch_alter_table('agent_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_runs_status'))
        batch_op.drop_index(batch_op.f('ix_agent_runs_started_at'))
        batch_op.drop_index(batch_op.f('ix_agent_runs_session_id'))

    op.drop_table('agent_runs')
