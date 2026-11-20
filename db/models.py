
"""EduPulse database schema: core resource monitoring tables."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# 1. Courses
class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


# 2. Resources
class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    course_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("courses.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    check_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default=text("60")
    )

    release_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    state: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # Worker leasing
    lease_owner: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_token: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "check_interval_seconds >= 1",
            name="ck_resources_check_interval",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_resources_consecutive_failures",
        ),
        CheckConstraint(
            "state IS NULL OR state IN ("
            "'HEALTHY', 'DEGRADED', 'SUSPECT', "
            "'DOWN', 'AUTH_REQUIRED', 'MISSING')",
            name="ck_resources_state",
        ),
        Index("ix_resources_due", "next_check_at", "lease_until"),
    )


# 3. Check results
class CheckResult(Base):
    __tablename__ = "check_results"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    resource_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("resources.id"),
        nullable=False,
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    http_status: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    outcome: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_check_results_http_status",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_check_results_latency",
        ),
        Index("ix_check_results_resource_time", "resource_id", "checked_at"),
    )


# 4. Incidents
class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    resource_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("resources.id"),
        nullable=False,
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= opened_at",
            name="ck_incidents_resolution_time",
        ),
        Index(
            "uq_incidents_one_open_per_resource",
            "resource_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )


# 5. Student reports
class StudentReport(Base):
    __tablename__ = "student_reports"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    resource_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("resources.id"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        String(2000), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(description) BETWEEN 1 AND 2000",
            name="ck_student_reports_description",
        ),
        Index(
            "ix_student_reports_resource_time",
            "resource_id",
            "created_at",
        ),
    )
