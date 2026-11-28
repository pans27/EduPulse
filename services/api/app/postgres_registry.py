"""Async SQLAlchemy implementation of the course resource API."""

from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .api_models import (
    Course, CourseSnapshot, EmailPreference, ReportCreate, Resource, ResourceCreate, ResourcePatch, StudentReport,
)
from .registry_models import CourseMemberRow, CourseRow, CredentialRow, ResourceRow, StudentReportRow
from .resource_credentials import accepts_token


class PostgresRegistry:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], notify_channel: str | None, cipher: Fernet | None = None
    ) -> None:
        # Keep the session factory and notification channel.
        self.sessions = sessions
        self.notify_channel = notify_channel
        self.cipher = cipher

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

    async def get_email_preference(self, course_id: str, user_id: UUID) -> EmailPreference:
        # Staff receive course email regardless of their saved student preference.
        async with self.sessions() as session:
            member = await session.get(CourseMemberRow, (user_id, course_id))
            if member is None:
                raise HTTPException(403, "Course membership required")
            return EmailPreference(email_opt_in=member.role == "staff" or member.email_opt_in)

    async def set_email_preference(
        self, course_id: str, user_id: UUID, preference: EmailPreference,
    ) -> EmailPreference:
        # Restrict the write to memberships that still have the student role.
        async with self.sessions.begin() as session:
            updated = await session.scalar(
                update(CourseMemberRow)
                .where(CourseMemberRow.user_id == user_id, CourseMemberRow.course_id == course_id,
                       CourseMemberRow.role == "student")
                .values(email_opt_in=preference.email_opt_in)
                .returning(CourseMemberRow.user_id)
            )
            if updated is None:
                raise HTTPException(403, "Student membership required")
            return preference

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
            await self._flush_resource(session)
            if resource_input.api_token is not None:
                ciphertext = self._encrypt_token(resource.id, resource.url, resource_input.api_token)
                session.add(CredentialRow(resource_id=resource.id, token_ciphertext=ciphertext))
            await self.notify_change(session, resource.id)
            return Resource.model_validate(resource, from_attributes=True)

    async def edit(self, course_id: str, resource_id: UUID, resource_input: ResourcePatch) -> Resource:
        # Apply a partial update while fencing checks started with outdated settings.
        async with self.sessions.begin() as session:
            resource = await session.scalar(
                select(ResourceRow)
                .where(ResourceRow.course_id == course_id, ResourceRow.id == resource_id)
                .with_for_update()
            )
            if resource is None:
                raise HTTPException(404, "Resource not found")
            fields = resource_input.model_dump(exclude_unset=True, exclude={"api_token"})
            if "url" in fields:
                fields["url"] = str(resource_input.url)
            changes = {name: value for name, value in fields.items() if getattr(resource, name) != value}
            credential_changed = False
            if "api_token" in resource_input.model_fields_set or "url" in changes:
                credential = await session.get(CredentialRow, resource_id)
                if resource_input.api_token is not None:
                    url = changes.get("url", resource.url)
                    ciphertext = self._encrypt_token(resource_id, url, resource_input.api_token)
                    if credential is None:
                        session.add(CredentialRow(resource_id=resource_id, token_ciphertext=ciphertext))
                    else:
                        credential.token_ciphertext = ciphertext
                    credential_changed = True
                elif credential is not None:
                    await session.delete(credential)
                    credential_changed = True
            if changes or credential_changed:
                monitoring_changed = credential_changed or bool(changes.keys() - {"name"})
                now = await session.scalar(select(func.now())) if monitoring_changed else None
                for name, value in changes.items():
                    setattr(resource, name, value)
                if "url" in changes or credential_changed:
                    resource.state = None
                    resource.consecutive_failures = 0
                if now is not None:
                    resource.next_check_at = now if resource.recheck_requested else max(now, resource.release_at or now)
                if credential_changed or changes.keys() & {"url", "release_at"}:
                    # Fence stale results while keeping the lease deadline to prevent overlapping checks.
                    resource.lease_token = None
                await self._flush_resource(session)
                await self.notify_change(session, resource.id)
            return Resource.model_validate(resource, from_attributes=True)

    def _encrypt_token(self, resource_id: UUID, url: str, token: SecretStr) -> bytes:
        # Bind the token to the resource and exact URL before storing ciphertext.
        if not accepts_token(url):
            raise HTTPException(422, "Resources with an API token require HTTPS")
        if self.cipher is None:
            raise HTTPException(503, "Resource token encryption is unavailable")
        return self.cipher.encrypt(resource_id.bytes + url.encode() + b"\x00" + token.get_secret_value().encode("ascii"))

    async def delete(self, course_id: str, resource_id: UUID) -> None:
        # Delete the course resource and refresh its course after the cascade commits.
        async with self.sessions.begin() as session:
            deleted = await session.scalar(
                delete(ResourceRow)
                .where(ResourceRow.course_id == course_id, ResourceRow.id == resource_id)
                .returning(ResourceRow.id)
            )
            if deleted is None:
                raise HTTPException(404, "Resource not found")
            await self.notify_change(session, f"course:{course_id}")

    async def report(self, course_id: str, resource_id: UUID, report_input: ReportCreate) -> StudentReport:
        # Persist the report and make the resource due in the same transaction.
        async with self.sessions.begin() as session:
            updated_resource_id = await session.scalar(
                update(ResourceRow)
                .where(ResourceRow.course_id == course_id, ResourceRow.id == resource_id)
                # recheck_requested makes the worker check again even if a check is already
                # running: that check's result would otherwise push next_check_at back out.
                .values(next_check_at=func.least(ResourceRow.next_check_at, func.now()), recheck_requested=True)
                .returning(ResourceRow.id)
            )
            if updated_resource_id is None:
                raise HTTPException(404, "Resource not found")
            report = StudentReportRow(resource_id=resource_id, description=report_input.description)
            session.add(report)
            await session.flush()
            await self.notify_change(session, resource_id)
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

    async def _flush_resource(self, session: AsyncSession) -> None:
        # Translate the course URL uniqueness constraint to a user-facing conflict.
        try:
            await session.flush()
        except IntegrityError as error:
            database_error = error.orig.__cause__
            if getattr(database_error, "constraint_name", None) == "uq_resources_course_url":
                raise HTTPException(409, "This URL is already registered for this course") from error
            raise

    async def notify_change(self, session: AsyncSession, payload: UUID | str) -> None:
        # Publish an invalidation in the same transaction as the write.
        if self.notify_channel:
            await session.execute(select(func.pgnotify_change(self.notify_channel, str(payload))))
