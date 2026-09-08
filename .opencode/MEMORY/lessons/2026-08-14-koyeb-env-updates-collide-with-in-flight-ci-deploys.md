# Koyeb env updates collide with in-flight CI deploys

- **Date**: 2026-08-14T18:59:05+0800
- **Type**: lesson

## What happened

Ran 'koyeb service update' (env staging) on worker/arq 8 minutes before pushing a commit. CI's deploy queued behind the manual rollout; on a pinned single eco-nano instance the rollouts serialize, the new instance took 12 min to start, and CI's 300s 'Verify deploy landed' step timed out — deploy actually succeeded (false negative, red main).

## Root cause / fix

Stage Koyeb env changes BEFORE pushing the commit that triggers deploy, or wait until CI is fully green. If verify fails with 'did not become healthy within 300s', check koyeb deployments list for a chained rollout (parent/child) before assuming a bad image — the deploy may have landed after verify gave up.
