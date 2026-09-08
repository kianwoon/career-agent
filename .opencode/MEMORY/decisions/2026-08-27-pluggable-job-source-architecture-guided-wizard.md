# Pluggable job-source architecture (guided wizard)

- **Date**: 2026-08-27T23:46:50+0800
- **Type**: decision

## Context

Career bot needed user-added job sites (e.g. fastjob.com) without hand-written Playwright scripts per site. Chosen design: guided wizard — user drives a headed browser (login + one demo search), recorder captures click/fill/submit events via injected capture-phase listeners (Alt-click marks result card), templatizer converts events to parameterized steps (first fill matching query_hint becomes param=query, next/› clicks become repeat=paginate), generic executor replays steps headless with encrypted storage_state and extracts cards.

## Decision / rationale

Tables: sources/source_flows/source_recordings in backend/app/models/orm.py. Services: backend/app/services/source_flows.py. Routes: backend/app/api/routes/sources.py. Search fan-out: _search_custom_sources in agent/nodes.py merges custom results with built-in adapters. Tables auto-create via Base.metadata.create_all on startup — no migration file needed. Tests: uv run pytest with DATABASE_URL pointing at reachable Postgres.
