# LinkedIn "empty results" is NOT always throttling

**Date**: 2026-09-11 (updated 2026-09-14)
**Area**: extension/background.js :: cmdLinkedinPeoplePlan
**Symptom**: Candidate search pauses with "LinkedIn returned empty results pages repeatedly — likely throttling". Tasks 183549c2 and 30bf7f5c paused this way.

## Root cause (two independent bugs)
1. **Code**: the plan aborted after `consecutiveZeroes >= 2`, treating ANY two
   back-to-back zero-row queries as a throttle. The backend sends narrow boolean
   queries that LEGITIMATELY match nothing — LinkedIn's own "No results found"
   page. Indistinguishable from a throttled page because the agent tab is opened
   `active:false` (backgrounded) and the SPA may render an empty shell.
2. **Deployment**: unpacked MV3 extensions are CACHED IN MEMORY and do NOT
   reload when files on disk change. The manifest version was unchanged
   (1.7.6), so Brave had no signal. Brave had last read the extension at
   2026-09-08 18:41; the fix landed on disk 2026-09-14 19:29. The re-run at
   2026-09-14 ~19:46 still executed the 09-08 pre-fix code and emitted the
   EXACT old string. THE OLD STRING EXISTED NOWHERE ON DISK — that was the
   tell. Always `grep` the source for the exact reported string first: if it
   is absent from disk, the browser is running stale code, not new logic.

## Fix (extension/background.js)
- Added pure `classifyLinkedinResultsPage(s)` (DOM-free, unit-testable) and
  `linkedinResultsPageState()` probe → `genuineEmpty`, `loaded`, `scaffold`, `inLinks`.
- On zero rows after extract+harvest+retry: `genuineEmpty` → NOT a strike
  (reset counter, continue). Else → strike. Threshold raised 2 → 3.
- Blocker string KEEPS the token "throttl" — backend
  (`backend/app/services/linkedin_people.py`) keys its 45s retry on
  `"throttl" in human_reason.lower()`. Removing that word silently disables the retry.
- Added `pageDiag` into blocker + plan_detail for future diagnosability.

## Operational lessons (the expensive ones)
- **Unpacked extension changes require an explicit reload** at
  `brave://extensions` (or `chrome://extensions`). Bump the manifest version so
  the reload is observable, and confirm the card shows the new version.
- **Diagnose from the reported string, not from assumption.** `grep -rn
  "<exact user-visible message>"` decides instantly whether the running code is
  stale. Do this BEFORE debugging logic.
- `Secure Preferences` (last_update_time / service_worker_registration_info) is
  only flushed to disk when the browser exits — it is NOT a reliable live signal.
  The extensions-page card version IS reliable.
- The packaged `dist/career-agent-extension.zip` is stale (v1.2.0) and is NOT
  what is loaded; the loaded copy is the unpacked `extension/` dir. Rebuild with
  `./package.sh` if distributing.
- Cross-process contract: the backend greps pause reasons for the substring
  "throttl". Any rewording of extension pause messages must preserve tokens the
  backend matches on.
