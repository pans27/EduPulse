"""Request, response, and WebSocket message models."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, field_validator,
)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ReportText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
HealthState = Literal["HEALTHY", "DEGRADED", "SUSPECT", "DOWN", "AUTH_REQUIRED", "MISSING"]


class Course(BaseModel):
    id: str
    name: str


class ResourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    url: HttpUrl
    check_interval_seconds: int = Field(default=60, ge=1)
    release_at: AwareDatetime | None = None

    @field_validator("url")
    @classmethod
    def url_without_credentials(cls, url: HttpUrl) -> HttpUrl:
        # Reject URLs that embed a username or password.
        if url.username is not None or url.password is not None:
            raise ValueError("URL must omit credentials")
        return url


class Resource(ResourceCreate):
    id: UUID
    course_id: str
    state: HealthState | None = None
    next_check_at: AwareDatetime


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: ReportText


class StudentReport(ReportCreate):
    id: UUID
    resource_id: UUID
    created_at: AwareDatetime


class CourseSnapshot(BaseModel):
    type: Literal["course.snapshot"] = "course.snapshot"
    course: Course
    resources: list[Resource]
