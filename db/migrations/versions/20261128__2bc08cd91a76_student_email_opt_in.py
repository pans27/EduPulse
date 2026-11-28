"""Add student email preferences to course memberships.

Revision ID: 2bc08cd91a76
Revises: 7ad2bc4ed905
Create Date: 2026-11-28
"""
from alembic import op
import sqlalchemy as sa

revision = "2bc08cd91a76"
down_revision = "7ad2bc4ed905"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing and new student memberships start opted out.
    op.add_column("course_members", sa.Column("email_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    # Remove saved student email preferences.
    op.drop_column("course_members", "email_opt_in")
