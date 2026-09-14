# LinkedIn "empty results" is NOT always throttling

**Date**: 2026-09-11
**Area**: extension/background.js :: cmdLinkedinPeoplePlan
**Symptom**: Candidate search pauses with "LinkedIn returned empty results pages repeatedly — likely throttling". Search task 183549c2-0bc3-4af0-928c-066e13d910fc paused this way.

## Root cause
The plan aborted after `consecutiveZeroes >= 2`, treating ANY two back-to-back
zero-row queries as a throttle. But the backend sends narrow boolean queries
(`compact_boolean_query` + `_apply_excludes`) that LEGITIMATELY return zero
matches — LinkedIn's own "No results found" page. That page is indistinguishable
from a throttled/unrendered page in the old logic, especially because the agent
tab is opened `active:false` (backgrounded) and LinkedIn's SPA may render an
empty shell.

The strike counter also reset on ANY non-zero n, so a single good query masked
the issue only until the next empty pair.

## Fix (extension/background.js)
- Added pure `classifyLinkedinResultsPage(s)` (DOM-free, unit-testable) and
  `linkedinResultsPageState()` probe. Classifies: `genuineEmpty`, `loaded`,
  `scaffold`, `inLinks`.
- On zero rows after extract+harvest+retry: if `genuineEmpty` -> NOT a strike
  (reset counter, continue). Else -> strike.
- Raised abort threshold 2 -> 3.
- Blocker string kept the token "throttl" because the backend
  (`backend/app/services/linkedin_people.py`) keys its 45s retry on
  `"throttl" in human_reason.lower()`. Removing that word silently disables the
  retry. DO NOT rename it.
- Added `pageDiag` (host+path, title, inLinks, scaffold, bodyLen) into the
  blocker + plan_detail so a future pause is diagnosable.

## Lesson
- Zero-row != throttled. Always detect the site's explicit empty-state marker
  before declaring a throttle.
- The agent tab is backgrounded (`active:false`); never assume a background tab
  rendered an SPA results list.
- Cross-process contract: the backend matches on the substring "throttl" in the
  pause reason. Any rewording of extension pause messages must preserve tokens
  the backend greps for.
