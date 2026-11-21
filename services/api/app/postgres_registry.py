"""Async SQLAlchemy implementation of the course resource API."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .api_models import Course, CourseSnapshot, ReportCreate, Resource, ResourceCreate, StudentReport
from .registry_models import CourseRow, ResourceRow, StudentReportRow


class PostgresRegistry:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], notify_channel: str | None, allow_writes: bool
    ) -> None:
        # Keep the session factory and local-development write configuration.
        self.sessions = sessions
        self.notify_channel = notify_channel
        self.allow_writes = allow_writes

    async def list_courses(self) -> list[Course]:
        # Read the provisioned course catalog.
        async with self.sessions() as session:
            courses = await session.scalars(select(CourseRow).order_by(CourseRow.id))
            return [Course.model_validate(course, from_attributes=True) for course in courses]

    async def get_snapshot(self, course_id: str) -> CourseSnapshot:
        # Use one query to get a consistent snapshot even for courses with no resources.
        async with self.sessions() as session:
            result = await session.execute(
                select(CourseRow, ResourceRow)
                .outerjoin(ResourceRow, ResourceRow.course_id == CourseRow.id)
                .where(CourseRow.id == course_id)
                .order_by(ResourceRow.name, ResourceRow.id)
            )
            rows = result.all()
            if not rows:
                raise HTTPException(404, "Course not found")
            return CourseSnapshot(
                course=Course.model_validate(rows[0][0], from_attributes=True),
                resources=[
                    Resource.model_validate(resource, from_attributes=True)
                    for _, resource in rows
                    if resource is not None
                ],
            )

    async def get_resource(self, course_id: str, resource_id: UUID) -> Resource:
        # Look up a resource within the requested course.
        async with self.sessions() as session:
            resource = await session.scalar(
                select(ResourceRow).where(ResourceRow.course_id == course_id, ResourceRow.id == resource_id)
            )
            if resource is None:
                raise HTTPException(404, "Resource not found")
            return Resource.model_validate(resource, from_attributes=True)

    async def course_for_resource(self, resource_id: UUID) -> str | None:
        # Resolve the owning course of a notified resource.
        async with self.sessions() as session:
            return await session.scalar(select(ResourceRow.course_id).where(ResourceRow.id == resource_id))

    async def register(self, course_id: str, resource_input: ResourceCreate) -> Resource:
        # Insert a resource and notify listeners when the transaction commits.
        self._require_writes()
        async with self.sessions.begin() as session:
            course = await session.get(CourseRow, course_id, with_for_update={"read": True, "key_share": True})
            if course is None:
                raise HTTPException(404, "Course not found")
            now = (await session.execute(select(func.now()))).scalar_one()
            resource = ResourceRow(
                course_id=course_id,
                name=resource_input.name,
                url=str(resource_input.url),
                check_interval_seconds=resource_input.check_interval_seconds,
                release_at=resource_input.release_at,
                next_check_at=max(now, resource_input.release_at or now),
            )
            session.add(resource)
            await session.flush()
            await self._notify(session, resource.id)
            return Resource.model_validate(resource, from_attributes=True)

    async def report(self, course_id: str, resource_id: UUID, report_input: ReportCreate) -> StudentReport:
        # Persist the report and make the resource due in the same transaction.
        self._require_writes()
        async with self.sessions.begin() as session:
            updated_resource_id = await session.scalar(
                update(ResourceRow)
                .where(ResourceRow.course_id == course_id, ResourceRow.id == resource_id)
                .values(next_check_at=func.least(ResourceRow.next_check_at, func.now()))
                .returning(ResourceRow.id)
            )
            if updated_resource_id is None:
                raise HTTPException(404, "Resource not found")
            report = StudentReportRow(resource_id=resource_id, description=report_input.description)
            session.add(report)
            await session.flush()
            await self._notify(session, resource_id)
            return StudentReport.model_validate(report, from_attributes=True)

    async def metrics(self) -> dict[str, int]:
        # Preserve the dashboard totals using persistent records.
        async with self.sessions() as session:
            counts = await session.execute(
                select(
                    select(func.count()).select_from(ResourceRow).scalar_subquery().label("registered_resources"),
                    select(func.count()).select_from(StudentReportRow).scalar_subquery().label("student_reports"),
                )
            )
            return dict(counts.mappings().one())

    def _require_writes(self) -> None:
        # Gate unauthenticated database mutations behind explicit local opt-in.
        if not self.allow_writes:
            raise HTTPException(403, "Database writes are disabled")

    async def _notify(self, session: AsyncSession, resource_id: UUID) -> None:
        # Send the resource ID through PostgreSQL in the same transaction as the write.
        if self.notify_channel:
            await session.execute(select(func.pg_notify(self.notify_channel, str(resource_id))))
