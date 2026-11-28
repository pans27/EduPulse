"""Request, response, and WebSocket message models."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, field_validator, model_validator,
)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ReportText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
HealthState = Literal["HEALTHY", "DEGRADED", "SUSPECT", "DOWN", "AUTH_REQUIRED", "MISSING"]


def reject_credentials(url: HttpUrl | None) -> HttpUrl | None:
    # Reject URLs that embed a username or password.
    if url is not None and (url.username is not None or url.password is not None):
        raise ValueError("URL must omit credentials")
    return url


class Course(BaseModel):
    id: str
    name: str


class ResourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    url: HttpUrl
    # Default to 60 seconds and allow 1 through PostgreSQL's INTEGER maximum, inclusive.
    check_interval_seconds: int = Field(default=60, ge=1, le=2_147_483_647)
    release_at: AwareDatetime | None = None

    @field_validator("url")
    @classmethod
    def url_without_credentials(cls, url: HttpUrl) -> HttpUrl:
        return reject_credentials(url)


class ResourceUpdate(BaseModel):
    # A partial edit: only the fields sent are changed. release_at may be set to null to clear it;
    # the other fields cannot be null.
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    url: HttpUrl | None = None
    check_interval_seconds: int | None = Field(default=None, ge=1, le=2_147_483_647)
    release_at: AwareDatetime | None = None

    @field_validator("url")
    @classmethod
    def url_without_credentials(cls, url: HttpUrl | None) -> HttpUrl | None:
        return reject_credentials(url)

    @model_validator(mode="after")
    def at_least_one_field(self) -> "ResourceUpdate":
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change")
        for name in ("name", "url", "check_interval_seconds"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


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
