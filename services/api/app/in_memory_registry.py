"""In-memory course and resource data for the local API preview."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException

from .api_models import Course, CourseSnapshot, ReportCreate, Resource, ResourceCreate, StudentReport


class InMemoryRegistry:
    def __init__(self) -> None:
        # Seed the sample courses and initialize preview storage.
        self.courses = {
            "ece1779": Course(id="ece1779", name="ECE1779"),
            "demo": Course(id="demo", name="Demo Course"),
        }
        self.resources: dict[UUID, Resource] = {}
        self.reports: list[StudentReport] = []
        self.listeners: dict[str, set[asyncio.Event]] = {
            course_id: set() for course_id in self.courses
        }

    def course(self, course_id: str) -> Course:
        # Look up a course by ID or return a 404.
        if course_id not in self.courses:
            raise HTTPException(404, "Course not found")
        return self.courses[course_id]

    def resource(self, course_id: str, resource_id: UUID) -> Resource:
        # Find a resource that belongs to the requested course.
        resource = self.resources.get(resource_id)
        if resource is None or resource.course_id != course_id:
            raise HTTPException(404, "Resource not found")
        return resource

    def snapshot(self, course_id: str) -> CourseSnapshot:
        # Build a snapshot of the course and its current resources.
        return CourseSnapshot(
            course=self.course(course_id),
            resources=[resource for resource in self.resources.values() if resource.course_id == course_id],
        )

    def publish(self, resource: Resource) -> None:
        # Save the resource and signal listeners for its course.
        self.resources[resource.id] = resource
        for event in self.listeners[resource.course_id]:
            event.set()

    async def list_courses(self) -> list[Course]:
        # Return the seeded preview courses.
        return list(self.courses.values())

    async def get_snapshot(self, course_id: str) -> CourseSnapshot:
        # Return the preview snapshot for a course.
        return self.snapshot(course_id)

    async def get_resource(self, course_id: str, resource_id: UUID) -> Resource:
        # Return a resource within its course.
        return self.resource(course_id, resource_id)

    async def register(self, course_id: str, resource_input: ResourceCreate) -> Resource:
        # Add a preview resource and schedule its first check.
        self.course(course_id)
        now = datetime.now(UTC)
        resource = Resource(
            **resource_input.model_dump(),
            id=uuid4(),
            course_id=course_id,
            next_check_at=max(now, resource_input.release_at or now),
        )
        self.publish(resource)
        return resource

    async def report(self, course_id: str, resource_id: UUID, report_input: ReportCreate) -> StudentReport:
        # Record a preview report and bring the next check forward.
        resource = self.resource(course_id, resource_id)
        now = datetime.now(UTC)
        report = StudentReport(**report_input.model_dump(), id=uuid4(), resource_id=resource_id, created_at=now)
        self.reports.append(report)
        self.publish(resource.model_copy(update={"next_check_at": min(resource.next_check_at, now)}))
        return report

    async def metrics(self) -> dict[str, int]:
        # Count resources and reports in the preview.
        return {"registered_resources": len(self.resources), "student_reports": len(self.reports)}
