# Candidate platform alias resolution: FastJobs → fastjob

**Date:** 2026-09-15 · **Repo:** `~/Downloads/career bot` (deployed as Koyeb `career-agent`)

## Symptom
External candidate searches returning display/plural platform names (e.g. `FastJobs`) were rejected with 422 even though a matching flow existed — exact-only matching could not find the DB flow whose `Source.name` was singular/different (e.g. `fastjob`).

## Root cause
Platform matching was exact-only: the caller-supplied name was compared verbatim against `Source.name`, so any case/punctuation/plurality difference failed to resolve.

## Fix
Shared `resolve_candidate_platform` helper in `backend/app/agent/nodes.py`:
1. exact match wins,
2. then non-alphanumeric normalization,
3. then trailing-`s` singular/plural — ONLY when unambiguous.

Used by both `POST /search/candidates` validation and agent `run_search` canonicalization before the flow lookup.

## Lesson
Any new source whose display name differs from its DB `Source.name` needs no per-case patch as long as it differs only by case/punctuation/plurality; anything beyond that needs an explicit alias.
