"""REST endpoints for courses, resources, student reports, and metrics."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from .api_models import Course, ReportCreate, Resource, ResourceCreate, StudentReport
from .auth import require_access
from .postgres_registry import PostgresRegistry


def get_registry(request: Request) -> PostgresRegistry:
    # Provide the PostgreSQL registry for this request.
    return request.app.state.registry


Registry = Annotated[PostgresRegistry, Depends(get_registry)]
router = APIRouter(prefix="/api")


@router.get("/courses")
async def list_courses(registry: Registry, request: Request) -> list[Course]:
    # List the courses available in the registry.
    account = await require_access(request)
    courses = await registry.list_courses()
    return [course for course in courses if course.id in account.courses]


@router.get("/courses/{course_id}/resources")
async def list_resources(course_id: str, registry: Registry, request: Request) -> list[Resource]:
    # List the resources registered for a course.
    await require_access(request, course_id)
    return (await registry.get_snapshot(course_id)).resources


@router.post("/courses/{course_id}/resources", status_code=201)
async def register_resource(
    course_id: str, resource_input: ResourceCreate, registry: Registry, request: Request
) -> Resource:
    # Register a resource and schedule its first check.
    await require_access(request, course_id, staff=True)
    return await registry.register(course_id, resource_input)


@router.get("/courses/{course_id}/resources/{resource_id}")
async def get_resource(course_id: str, resource_id: UUID, registry: Registry, request: Request) -> Resource:
    # Return a single resource within its course.
    await require_access(request, course_id)
    return await registry.get_resource(course_id, resource_id)


@router.post("/courses/{course_id}/resources/{resource_id}/reports", status_code=201)
async def report_resource(
    course_id: str, resource_id: UUID, report_input: ReportCreate, registry: Registry, request: Request
) -> StudentReport:
    # Save a student report and make the resource due for a check.
    await require_access(request, course_id)
    return await registry.report(course_id, resource_id, report_input)


@router.get("/metrics")
async def metrics(registry: Registry, request: Request) -> dict[str, int]:
    # Return the totals for registered resources and student reports.
    if not (await require_access(request)).is_admin:
        raise HTTPException(403, "Administrator access required")
    return await registry.metrics()
