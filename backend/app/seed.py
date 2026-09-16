"""Create database tables. Run with: python -m app.seed"""

import asyncio

from app.db import Base, async_session, engine

# Import ORM models FIRST so Base.metadata is populated before create_all().
from app.models import orm  # noqa: F401


async def create_all() -> None:
    from app.db import ensure_schema

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # create_all is idempotent for new tables but never adds a column to an
    # existing one — apply the explicit column upgrades too.
    await ensure_schema()
    print("Tables created.")


async def seed() -> None:
    from app.models.orm import User

    async with async_session() as session:
        existing = await session.execute(__import__("sqlalchemy").select(User).where(User.email == "kian@example.com"))
        if existing.scalar_one_or_none() is None:
            session.add(User(email="kian@example.com"))
            await session.commit()
            print("Seeded demo user.")
        else:
            print("Demo user already exists.")


async def main() -> None:
    await create_all()
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
