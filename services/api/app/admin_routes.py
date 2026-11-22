"""Global admin actions for courses and course memberships."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .api_models import Course
from .auth import EmailAddress, require_access
from .registry_models import CourseMemberRow, CourseRow, UserRow

router = APIRouter(prefix="/api/admin")


class CourseCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class RoleAssignment(BaseModel):
    email: EmailAddress
    role: Literal["admin", "staff", "student"]
    course_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")

    @model_validator(mode="after")
    def validate_scope(self) -> "RoleAssignment":
        # Global admin grants have no course and membership grants require one.
        if self.role == "admin" and self.course_id is not None:
            raise ValueError("An admin grant cannot specify a course")
        if self.role != "admin" and self.course_id is None:
            raise ValueError("Select a course for a staff or student role")
        return self


async def admin_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    # Restrict these operations to active global admins in database auth mode.
    account = await require_access(request)
    if not account.is_admin:
        raise HTTPException(403, "Administrator access required")
    return request.app.state.auth.sessions


@router.get("/courses")
async def list_admin_courses(request: Request) -> list[Course]:
    # Let administrators assign users to any course, including courses they do not teach.
    await admin_sessions(request)
    return await request.app.state.registry.list_courses()


@router.post("/courses", status_code=201)
async def create_course(data: CourseCreate, request: Request) -> Course:
    # Create a course and report duplicate IDs as conflicts.
    sessions = await admin_sessions(request)
    async with sessions.begin() as session:
        course_id = await session.scalar(
            insert(CourseRow).values(**data.model_dump()).on_conflict_do_nothing(
                index_elements=[CourseRow.id],
            ).returning(CourseRow.id)
        )
        if course_id is None:
            raise HTTPException(409, "Course ID already exists")
    return Course(id=course_id, name=data.name)


@router.put("/users/role", status_code=204)
async def assign_user_role(assignment: RoleAssignment, request: Request) -> None:
    # Find an active user by email and grant a global or course role.
    sessions = await admin_sessions(request)
    async with sessions.begin() as session:
        user = await session.scalar(
            select(UserRow).where(UserRow.email == assignment.email, UserRow.is_active).with_for_update()
        )
        if user is None:
            raise HTTPException(404, "Active user not found")
        if assignment.role == "admin":
            user.is_admin = True
            return
        if await session.get(CourseRow, assignment.course_id) is None:
            raise HTTPException(404, "Course not found")
        await session.execute(
            insert(CourseMemberRow).values(user_id=user.id, course_id=assignment.course_id, role=assignment.role)
            .on_conflict_do_update(
                index_elements=[CourseMemberRow.user_id, CourseMemberRow.course_id],
                set_={"role": assignment.role},
            )
        )
