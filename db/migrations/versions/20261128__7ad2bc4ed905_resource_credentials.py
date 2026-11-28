"""Store encrypted API tokens for monitored resources.

Revision ID: 7ad2bc4ed905
Revises: d6bf51ade62b
Create Date: 2026-11-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '7ad2bc4ed905'
down_revision: Union[str, Sequence[str], None] = 'd6bf51ade62b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep encrypted credentials in a separate table with the resource lifecycle.
    op.create_table(
        "credentials",
        sa.Column("resource_id", sa.Uuid(), sa.ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token_ciphertext", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    # Remove the credential table.
    op.drop_table("credentials")
