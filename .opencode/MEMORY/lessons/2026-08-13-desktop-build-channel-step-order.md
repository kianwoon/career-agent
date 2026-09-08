# Desktop build: channel + step order

- **Date**: 2026-08-13T16:53:57+0800
- **Type**: lesson

## What happened

Tried packaging with OPENCODE_CHANNEL=dev and package-before-build, which failed verify. The desktop AGENTS.md runbook is unambiguous: package:mac does NOT run build/prebuild, and channel is baked into the server bundle. Correct sequence is (1) OPENCODE_CHANNEL=prod bun run build, (2) OPENCODE_CHANNEL=prod bun run package:mac; verify-prod.ts must pass. For a distributable mac app the channel is prod (app name OpenCode.app), not dev.

## Root cause / fix

Before any packaging/deploy task, read the package AGENTS.md runbook and quote the exact steps+channel back before running. On a documented script failing, re-read the runbook first instead of patching the failure. If channel is ambiguous for a distributable, ask the user instead of defaulting.
