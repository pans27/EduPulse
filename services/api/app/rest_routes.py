"""REST endpoints for courses, resources, student reports, and metrics."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from .api_models import Course, ReportCreate, Resource, ResourceCreate, StudentReport
from .in_memory_registry import InMemoryRegistry


def get_registry(request: Request) -> InMemoryRegistry:
    # Provide the preview registry when local mode is enabled.
    if request.app.state.registry is None:
        raise HTTPException(503, "Course API storage is unavailable")
    return request.app.state.registry


Registry = Annotated[InMemoryRegistry, Depends(get_registry)]
router = APIRouter(prefix="/api")


@router.get("/courses")
async def list_courses(registry: Registry) -> list[Course]:
    # List the courses available in the preview.
    return list(registry.courses.values())


@router.get("/courses/{course_id}/resources")
async def list_resources(course_id: str, registry: Registry) -> list[Resource]:
    # List the resources registered for a course.
    return registry.snapshot(course_id).resources


@router.post("/courses/{course_id}/resources", status_code=201)
async def register_resource(course_id: str, data: ResourceCreate, registry: Registry) -> Resource:
    # Register a resource and schedule its first check.
    registry.course(course_id)
    now = datetime.now(UTC)
    resource = Resource(
        **data.model_dump(),
        id=uuid4(),
        course_id=course_id,
        next_check_at=max(now, data.release_at) if data.release_at else now,
    )
    registry.publish(resource)
    return resource


@router.get("/courses/{course_id}/resources/{resource_id}")
async def get_resource(course_id: str, resource_id: UUID, registry: Registry) -> Resource:
    # Return a single resource within its course.
    return registry.resource(course_id, resource_id)


@router.post("/courses/{course_id}/resources/{resource_id}/reports", status_code=201)
async def report_resource(
    course_id: str, resource_id: UUID, data: ReportCreate, registry: Registry
) -> StudentReport:
    # Save a student report and make the resource due for a check.
    resource = registry.resource(course_id, resource_id)
    now = datetime.now(UTC)
    report = StudentReport(**data.model_dump(), id=uuid4(), resource_id=resource_id, created_at=now)
    registry.reports.append(report)
    registry.publish(resource.model_copy(update={"next_check_at": min(resource.next_check_at, now)}))
    return report


@router.get("/metrics")
async def metrics(registry: Registry) -> dict[str, int]:
    # Return the totals for registered resources and student reports.
    return {"registered_resources": len(registry.resources), "student_reports": len(registry.reports)}
