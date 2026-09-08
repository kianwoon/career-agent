# Upstream opencode sync: cherry-pick workflow + fork-local quirks

- **Date**: 2026-08-21T19:56:57+0800
- **Type**: lesson

## What happened

Syncing upstream releases into the fork: (1) fork's local v1.18.x tags are FORK commits, not upstream — always fetch origin refs/tags/vX.Y.Z as upstream-* tags; real upstream official tags are what matter. (2) check-upstream.ts patch-id detection is lossy for conflict-resolved cherry-picks — verify content presence with grep, not the tool. (3) When cherry-picking upstream commits that overlap fork-custom work (e.g. provider.ts deepseek npm mapping, llm.ts promptCacheKey openrouter), resolve conflicts by KEEPING fork custom bits + ADDING upstream changes. (4) ai-sdk.ts got a duplicate ProviderError import from a cherry-pick — always run typecheck after cherry-picks. (5) Pre-existing fork test failures (compaction BUG tests, run-process permission tests, task permission test) fail on main too — check against main baseline before blaming the cherry-pick. (6) Version bumps: upstream syncs ALL package.json + bun.lock + sdks/vscode to the release version; fork desktop was at 1.19.x while libs were 1.18.16 — align all to upstream version. (7) cherry-pick --continue can hang on editor; use GIT_EDITOR=true. (8) Skip formatting-only generate commits that conflict (no functional change).

## Root cause / fix

Use: fetch upstream tags to upstream-*, verify scope (official release ranges), cherry-pick product fixes in order, resolve conflicts keeping fork custom work, typecheck + test, adapt upstream tests to fork behavior when fork intentionally diverges.
