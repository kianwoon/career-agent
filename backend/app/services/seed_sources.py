"""Seed built-in sources as real Source rows.

LinkedIn, MyCareersFuture and FastJobs were originally hardcoded adapters that
always ran on every job search — invisible in the Sources UI and impossible to
disable or re-authenticate. Seeding them as regular Source rows gives them
cards in the UI (favicon, status pills, checkbox), stored sessions, and makes
the agent respect the enabled flag.

Idempotent: rows are matched by domain OR name, so an existing user-created
source for the same domain (or an earlier seed whose domain has since changed)
is adopted rather than duplicated. Never overwrites user-visible fields (name)
on subsequent boots. Concurrent restarts are additionally tolerated: each
insert runs in its own savepoint and an IntegrityError on the unique
``ix_sources_name`` constraint is caught and skipped rather than aborting the
whole seed (the unique index remains the last-resort backstop).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
    # match on either domain or name so a pre-existing row (e.g. an old seed or
    # a user-created source) is never duplicated.
    rows = (await db.execute(select(Source.domain, Source.name))).all()
    existing_domains = {domain for domain, _ in rows}
    existing_names = {name for _, name in rows}
    created = 0
    for spec in BUILTIN_SOURCES:
        if spec["domain"] in existing_domains or spec["name"] in existing_names:
            continue
        # Each insert in its own savepoint so a concurrent restart that beat us
        # to a row (unique ix_sources_name) only rolls back that one row.
        try:
            async with db.begin_nested():
                db.add(
                    Source(
                        name=spec["name"],
                        domain=spec["domain"],
                        base_url=spec["base_url"],
                        enabled=True,
                    )
                )
        except IntegrityError:
            logger.info("Source %r already present; skipping", spec["name"])
            continue
        existing_domains.add(spec["domain"])
        existing_names.add(spec["name"])
        created += 1
    if created:
        await db.commit()
        logger.info("Seeded %d built-in source(s)", created)
