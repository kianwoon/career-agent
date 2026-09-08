# Runbook lagged live config on chunked-prefill

- **Date**: 2026-08-19T06:58:24+0800
- **Type**: lesson

## What happened

Advised restarting the vast sglang server to 'apply' --chunked-prefill-size 2048 based on RUNPOD_RUNBOOK.md, but the user had already switched to 8192 and the runbook was stale — the restart would have reverted the working config.

## Root cause / fix

Before recommending a restart to 'apply' a documented flag, diff the RUNNING process (ps aux | grep sglang) against the docs; the live process is the source of truth. Also check the restart script itself, which can lag both.
