"""In-memory course and resource data for the local API preview."""

import asyncio
from uuid import UUID

from fastapi import HTTPException

from .api_models import Course, CourseSnapshot, Resource, StudentReport


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
