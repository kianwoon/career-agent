# Per-selector :not() chains explode generated CSS size

- **Date**: 2026-08-14T15:15:51+0800
- **Type**: lesson

## What happened

MyFont extension generated ~48KB selector lists because every text-tag selector carried a ~750-char exclusion chain (:not() icon clauses + 18-selector media :not(:is(...) *)). Cost showed up per-page (author style), per-origin (user CSS via SW), and per-shadow-root.

## Root cause / fix

Group selectors into :is() per exclusion tier — chain emitted once per tier instead of once per selector: 48KB → 3.7KB, identical semantics. Measure generated CSS size, not just source file size, when auditing extension weight.
