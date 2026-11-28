"""Add email-based accounts, course memberships, and login sessions."""

import os

import sqlalchemy as sa
from alembic import op

revision = "b35d43cd818a"
down_revision = "04792cdc1cee"
branch_labels = None
depends_on = None

DEMO_ADMIN_EMAIL = "admin@edupulse.example.com"
DEMO_ADMIN_HASH = "$argon2id$v=19$m=32768,t=3,p=1$2As3Bg6d99cuwT+gX+Z5ew$KHCqI1Hy9MqqFu9VxGgPcVhiuu3D7hd7myRL26TJP9E"


def upgrade() -> None:
    # Create accounts and constrain roles at the database boundary.
    users = op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "course_members",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("course_id", sa.String(100), sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(7), nullable=False),
        sa.CheckConstraint("role IN ('staff', 'student')", name="ck_course_members_role"),
    )
    op.create_index("ix_course_members_course_id", "course_members", ["course_id"])
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    # Seed the known demo credential only when the local migration opts in.
    if os.getenv("EDUPULSE_LOCAL_TESTING") == "1":
        op.bulk_insert(users, [{
            "email": DEMO_ADMIN_EMAIL,
            "password_hash": DEMO_ADMIN_HASH,
            "is_admin": True,
        }])


def downgrade() -> None:
    # Drop sessions and memberships before removing accounts.
    op.drop_table("sessions")
    op.drop_index("ix_course_members_course_id", table_name="course_members")
    op.drop_table("course_members")
    op.drop_table("users")
