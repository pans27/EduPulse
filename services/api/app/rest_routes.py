"""REST endpoints for courses, resources, student reports, and metrics."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from .api_models import Course, ReportCreate, Resource, ResourceCreate, StudentReport
from .in_memory_registry import InMemoryRegistry
from .postgres_registry import PostgresRegistry


def get_registry(request: Request) -> InMemoryRegistry | PostgresRegistry:
    # Provide the preview registry for the selected storage mode.
    if request.app.state.registry is None:
        raise HTTPException(503, "Course API storage is unavailable")
    return request.app.state.registry


Registry = Annotated[InMemoryRegistry | PostgresRegistry, Depends(get_registry)]
router = APIRouter(prefix="/api")


@router.get("/courses")
async def list_courses(registry: Registry) -> list[Course]:
    # List the courses available in the registry.
    return await registry.list_courses()


@router.get("/courses/{course_id}/resources")
async def list_resources(course_id: str, registry: Registry) -> list[Resource]:
    # List the resources registered for a course.
    return (await registry.get_snapshot(course_id)).resources


@router.post("/courses/{course_id}/resources", status_code=201)
async def register_resource(course_id: str, resource_input: ResourceCreate, registry: Registry) -> Resource:
    # Register a resource and schedule its first check.
    return await registry.register(course_id, resource_input)


@router.get("/courses/{course_id}/resources/{resource_id}")
async def get_resource(course_id: str, resource_id: UUID, registry: Registry) -> Resource:
    # Return a single resource within its course.
    return await registry.get_resource(course_id, resource_id)


@router.post("/courses/{course_id}/resources/{resource_id}/reports", status_code=201)
async def report_resource(
    course_id: str, resource_id: UUID, report_input: ReportCreate, registry: Registry
) -> StudentReport:
    # Save a student report and make the resource due for a check.
    return await registry.report(course_id, resource_id, report_input)


@router.get("/metrics")
async def metrics(registry: Registry) -> dict[str, int]:
    # Return the totals for registered resources and student reports.
    return await registry.metrics()
