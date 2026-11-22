"""Script to provision a PostgreSQL-backed EduPulse user or bootstrap an administrator."""

import argparse
import asyncio
import getpass
import os

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .auth import EmailAddress, hasher
from .registry_models import UserRow


async def provision(email: str, password_hash: str, is_admin: bool) -> str:
    # Create an account once without changing existing credentials or privileges.
    database_url = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql+asyncpg")
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine)
    try:
        async with sessions.begin() as session:
            created = await session.scalar(
                insert(UserRow).values(email=email, password_hash=password_hash, is_admin=is_admin)
                .on_conflict_do_nothing(index_elements=[UserRow.email]).returning(UserRow.id)
            )
            if created is None:
                existing = await session.scalar(select(UserRow).where(UserRow.email == email))
                if existing is None or existing.is_admin != is_admin or not existing.is_active:
                    raise SystemExit("An account with this name exists with different privileges or status.")
                return "Account already exists."
            return "Account created."
    finally:
        await engine.dispose()


def main() -> None:
    # Read credentials interactively to keep passwords out of shell history.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--admin", action="store_true")
    args = parser.parse_args()
    try:
        email = TypeAdapter(EmailAddress).validate_python(args.email)
    except ValidationError:
        parser.error("enter a valid email address")
    password = getpass.getpass("Password: ")
    if not 12 <= len(password) <= 256:
        parser.error("use a password with 12 to 256 characters")
    print(asyncio.run(provision(email, hasher.hash(password), args.admin)))


if __name__ == "__main__":
    main()
