# vast/RunPod template: never pip-install SGLang from a moving PR ref

- **Date**: 2026-08-19T18:30:31+0800
- **Type**: lesson

## What happened

The DFlash2 template onstart installed SGLang via 'sglang[all] @ git+...@refs/pull/35462/head'. refs/pull/N/head is a MOVING ref that GitHub deletes when the PR merges. The day PR #35462 merges, every new instance fails at pip install with a confusing 'bad ref' error, and the whole DFlash2 stack silently falls back to nothing. This is a time bomb: works today, breaks on merge with no warning.

## Root cause / fix

Always pin to the commit SHA, not the PR ref: '@25c15d748b8fa90e00be19595d325fcbf6e8511f#subdirectory=python'. To fetch the SHA: curl -s https://api.github.com/repos/sgl-project/sglang/pulls/35462 | jq .head.sha. When the PR merges, the SHA stays valid (merges don't delete commits), so a pinned build keeps working indefinitely. Also: robust onstart should (1) retry pip + hf download (transient network), (2) guard 'python -c import sglang' before launching, (3) wrap the server in a restart loop (crash-resilient), (4) log all steps to /root/sglang.log. All four are now in vast template 567382 (hash 6bf4ab8e) and RunPod template 5o7m0tna32.
