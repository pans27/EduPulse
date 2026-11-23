"""notification outbox: one row per alert email, written in the worker's state-change transaction

Revision ID: e76abc3e4d9f
Revises: b35d43cd818a
Create Date: 2026-11-23 01:00:00

The worker inserts these rows in the same transaction as the state change, so an alert exists
if and only if the state change committed. The dispatcher claims pending rows with a lease
(like the check worker), sends them, and marks them sent; the row id is the idempotency key.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e76abc3e4d9f'
down_revision: Union[str, Sequence[str], None] = 'b35d43cd818a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notification_outbox',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('resource_id', sa.UUID(), nullable=False),
        sa.Column('incident_id', sa.UUID(), nullable=True),
        sa.Column('recipient_email', sa.String(length=320), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('status', sa.String(length=10), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('lease_owner', sa.String(length=120), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_token', sa.UUID(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('incident_opened', 'incident_updated', 'incident_resolved')", name='ck_outbox_kind'),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name='ck_outbox_status'),
        sa.CheckConstraint('attempts >= 0', name='ck_outbox_attempts'),
        sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    # The dispatcher's claim query scans only rows still waiting to be sent.
    op.create_index(
        'ix_outbox_pending', 'notification_outbox', ['next_attempt_at'],
        unique=False, postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index('ix_outbox_pending', table_name='notification_outbox', postgresql_where=sa.text("status = 'pending'"))
    op.drop_table('notification_outbox')
