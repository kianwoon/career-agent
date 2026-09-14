# LinkedIn relaxed-pass relevance gate over-drops (gated against groups never searched)

**Date**: 2026-09-14
**Area**: backend/app/services/linkedin_people.py :: _gate_relaxed_rows / search_linkedin_people
**Detected by**: production Koyeb log line
`INFO app.services.linkedin_people: Relaxed-pass relevance gate dropped 12 irrelevant rows`

## The tell in the log
The word "irrelevant" exists at only ONE place in the codebase: line ~870, the
EXTENSION branch of `search_linkedin_people`. The CDP/Playwright fallback logs
the same counter WITHOUT "irrelevant" (line ~995). So that single word proves:
- the extension agent was connected and executed the plan (not the CDP fallback),
- therefore the extension was running the reloaded fixed code,
- and NO throttle pause occurred (the relaxed/broad passes are guarded by
  `not results["needs_human"]`, so a throttle abort makes line 870 unreachable).

Lesson: a log line's exact WORDING can identify which code path ran. Grep for
the precise string, not a paraphrase.

## The bug
The relaxed pass deliberately dispatches only the FIRST OR-group of each plan
query (`_first_or_group`), because a strict AND-chain returns zero:
```
group = _first_or_group(q)            # ("QC tech" OR "QC analyst")
relaxed.append(group)                 # searches group 1 ONLY
```
...but the rows it returned were then gated against the FULL plan's groups:
```
_gate_relaxed_rows(rows, queries)     # queries = the full AND plan
```
`_matches_plan_groups` requires hits >= 2 distinct groups when the plan has
>=2 groups. So the gate demanded vocabulary from group 2 — the group the search
had deliberately NOT asked for. Structurally self-defeating: a genuinely
relevant person matching only the searched group is dropped as "irrelevant".

## Impact (silent, NOT a pause)
If pass 1 returned rows, the gate merely trims results. If pass 1 was EMPTY and
the relaxed (and broad) passes were gated to zero, the result is
`raw_results=[]`, `needs_human=False` → `match_rank` sets status=completed
(nodes.py ~1310) → `check_human` sees needs_human False → NO pause. The task
COMPLETES WITH ZERO RESULTS and no reason string. That is why this class of bug
looks like "it still failed" with nothing to grep.

## The fix
- `_gate_relaxed_rows(rows, queries, gate_groups=None)` — new optional param.
  When `gate_groups` is provided, a row is kept if it matches ANY of those
  searched group-lists (a single group then needs only hits>=1). When `None`,
  behaviour is byte-identical to before, so the CDP fallback caller is unchanged.
- Extension relaxed branch and broad widen pass now pass
  `gate_groups=[grp for r in relaxed for grp in _or_groups(r)]` — the groups
  actually dispatched.
- `_matches_plan_groups`: added `"skills"` to the hay keys, and list/tuple
  values are now joined instead of `str(list)` leaking brackets/quotes into the
  haystack. Extension harvest rows have headline/location/current_role = null,
  so the haystack was collapsing to summary+experience and losing the skills signal.

## Verification (before -> after)
```
plan: ("QC tech" OR "QC analyst") AND (microarray OR GeneChip)
relaxed query dispatched: '"QC tech" OR "QC analyst"'
BEFORE (gate_groups=None): kept=0 dropped=4    # every relevant person dropped
AFTER  (searched groups) : kept=2 dropped=2    # Jane/Amy kept, John/Bob dropped
```
Backend suite: 12 failed/145 passed on clean HEAD -> 8 failed/149 passed with
the fix. The 4-test delta is exactly the new gate regression tests. The 8
remaining failures are PRE-EXISTING DB/DNS errors
(`OSError: Multiple exceptions` at `create_async_engine`) in
test_compat_routes/test_graph/test_security/test_sources_api — unrelated.

## Test-harness gotchas learned
- Tests must run with cwd = `backend/` (rootdir from backend/pyproject.toml,
  `testpaths=["app/tests"]`). Running pytest from the repo root gives
  `ModuleNotFoundError: No module named 'app'`.
- Do NOT run `git stash` (or any source-mutating command) in parallel with a
  pytest run while verifying: the stash swaps the module mid-run and produces
  phantom failures in the very tests you are checking. Run them sequentially.
- The full suite is slow (DB connection timeouts ~10-20s of the runtime), so
  give the command a generous timeout.
