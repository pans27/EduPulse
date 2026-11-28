"""API mappings for PostgreSQL tables managed by Alembic."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Text, false, func, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class RegistryBase(DeclarativeBase):
    pass


class CourseRow(RegistryBase):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class ResourceRow(RegistryBase):
    __tablename__ = "resources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"))
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    check_interval_seconds: Mapped[int]
    release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str | None] = mapped_column(String(20))
    recheck_requested: Mapped[bool] = mapped_column(server_default=false())
    consecutive_failures: Mapped[int] = mapped_column(server_default="0")
    lease_token: Mapped[UUID | None]


class StudentReportRow(RegistryBase):
    __tablename__ = "student_reports"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    resource_id: Mapped[UUID] = mapped_column(ForeignKey("resources.id"))
    description: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRow(RegistryBase):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(server_default=false())
    is_active: Mapped[bool] = mapped_column(server_default=true())


class CourseMemberRow(RegistryBase):
    __tablename__ = "course_members"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(7))


class SessionRow(RegistryBase):
    __tablename__ = "sessions"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
