"""Request, response, and WebSocket message models."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, SecretStr, StrictBool,
    StringConstraints, field_validator,
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


def validate_token(token: SecretStr) -> SecretStr:
    # Accept only visible ASCII characters that are safe to send in a header.
    value = token.get_secret_value()
    if not 1 <= len(value) <= 4096 or any(not "!" <= char <= "~" for char in value):
        raise ValueError("Enter a token of 1 to 4096 visible ASCII characters")
    return token


ApiToken = Annotated[SecretStr, AfterValidator(validate_token)]


class Course(BaseModel):
    id: str
    name: str


class EmailPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_opt_in: StrictBool


class ResourceFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    url: ResourceURL
    check_interval_seconds: CheckInterval = 60
    release_at: AwareDatetime | None = None


class ResourceCreate(ResourceFields):
    api_token: ApiToken | None = None


class ResourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    url: ResourceURL | None = None
    check_interval_seconds: CheckInterval | None = None
    release_at: AwareDatetime | None = None
    api_token: ApiToken | None = None

    @field_validator("name", "url", "check_interval_seconds")
    @classmethod
    def reject_null(cls, value: str | HttpUrl | int | None) -> str | HttpUrl | int:
        # These fields can be omitted from a patch but cannot be cleared.
        if value is None:
            raise ValueError("Field cannot be null")
        return value


class Resource(ResourceFields):
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
