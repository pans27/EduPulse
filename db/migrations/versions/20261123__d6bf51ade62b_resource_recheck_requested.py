"""resources.recheck_requested: a student report's recheck request, kept on the locked row

Revision ID: d6bf51ade62b
Revises: e76abc3e4d9f
Create Date: 2026-11-23 03:00:00

A report that lands while a check is running must still lead to a fresh check. The API sets this
flag in the same UPDATE that makes the resource due; the worker clears it when it claims the
resource and reads it again under the row lock when it records the result. Because it is a column
of the locked row, PostgreSQL re-reads it after waiting for the report's transaction to commit,
which a lookup in student_reports within the same statement would not.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd6bf51ade62b'
down_revision: Union[str, Sequence[str], None] = 'e76abc3e4d9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('resources', sa.Column('recheck_requested', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('resources', 'recheck_requested')
