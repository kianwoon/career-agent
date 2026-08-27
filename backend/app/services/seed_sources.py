"""Seed built-in sources as real Source rows.

LinkedIn, MyCareersFuture and FastJobs were originally hardcoded adapters that
always ran on every job search — invisible in the Sources UI and impossible to
disable or re-authenticate. Seeding them as regular Source rows gives them
cards in the UI (favicon, status pills, checkbox), stored sessions, and makes
the agent respect the enabled flag.

Idempotent: rows are matched by domain, so an existing user-created source for
the same domain is adopted rather than duplicated. Never overwrites
user-visible fields (name) on subsequent boots.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import Source

logger = logging.getLogger(__name__)

# Built-in adapters keyed by the canonical domain their adapter targets.
BUILTIN_SOURCES: list[dict[str, str]] = [
    {
        "name": "LinkedIn",
        "domain": "linkedin.com",
        "base_url": "https://www.linkedin.com/jobs/",
    },
    {
        "name": "MyCareersFuture",
        "domain": "mycareersfuture.gov.sg",
        "base_url": "https://www.mycareersfuture.gov.sg/",
    },
    {
        "name": "FastJobs",
        "domain": "fastjobs.io",
        "base_url": "https://www.fastjobs.io/",
    },
]


async def seed_builtin_sources(db: AsyncSession) -> None:
    """Insert missing built-in sources; leave existing rows untouched.

    Committing is left to the caller so this can share a transaction with
    other startup work if needed.
    """
    existing_domains = set(
        (await db.execute(select(Source.domain))).scalars().all()
    )
    created = 0
    for spec in BUILTIN_SOURCES:
        if spec["domain"] in existing_domains:
            continue
        db.add(
            Source(
                name=spec["name"],
                domain=spec["domain"],
                base_url=spec["base_url"],
                enabled=True,
            )
        )
        created += 1
    if created:
        await db.commit()
        logger.info("Seeded %d built-in source(s)", created)
