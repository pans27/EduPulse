"""Request, response, and WebSocket message models."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, field_validator,
)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ReportText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
HealthState = Literal["HEALTHY", "DEGRADED", "SUSPECT", "DOWN", "AUTH_REQUIRED", "MISSING"]


def url_without_credentials(url: HttpUrl) -> HttpUrl:
    # Reject URLs that embed a username or password.
    if url.username is not None or url.password is not None:
        raise ValueError("URL must omit credentials")
    return url


ResourceURL = Annotated[HttpUrl, AfterValidator(url_without_credentials)]
# Check intervals are positive PostgreSQL INTEGER values, in seconds.
CheckInterval = Annotated[int, Field(ge=1, le=2_147_483_647)]


class Course(BaseModel):
    id: str
    name: str


class ResourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    url: ResourceURL
    check_interval_seconds: CheckInterval = 60
    release_at: AwareDatetime | None = None


class ResourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    url: ResourceURL | None = None
    check_interval_seconds: CheckInterval | None = None
    release_at: AwareDatetime | None = None

    @field_validator("name", "url", "check_interval_seconds")
    @classmethod
    def reject_null(cls, value: str | HttpUrl | int | None) -> str | HttpUrl | int:
        # Allow fields to be omitted, but only the release time can be cleared.
        if value is None:
            raise ValueError("Field cannot be null")
        return value


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
