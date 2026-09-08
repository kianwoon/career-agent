# DFlash 2 open-PR audit: watch #32052 (GDN state commit + extra_buffer radix)

- **Date**: 2026-08-19T19:17:12+0800
- **Type**: lesson

## What happened

Audited all 47 open SGLang DFlash PRs against our exact config (TP=1, flashinfer, GDN hybrid Qwen3.8-27B, extra_buffer_lazy radix, unquantized draft, no SWA layers). One fix is pending that matters to us: PR #32052 'Fix hybrid recurrent-state commit for radix cache'. Bug: DFlash V2 verify on GDN models can store a generated radix prefix with recurrent state from a different sequence position; reusing that prefix changes output vs a full prefill. Subtle quality/determinism drift, NOT a crash — which is why our server runs fine. Unmerged as of 2026-08-19, CI gated (author fork lacks run-ci label), author validation is compile-only, rebased onto main 2026-07-31 (pre-dates our pin 25c15d74 based on 2026-08-19 main). Do NOT cherry-pick now. All other open PRs are N/A: #35208 (SWA verify window — our target has sliding_window: None), #33531/#33869 (additive penalties — unused), #33614 (TP divergence — we're TP=1), #35209 (trtllm_mha — we use flashinfer), #30119 (modelopt_mixed draft crash — draft is unquantized).

## Root cause / fix

When #32052 merges: bump both templates (vast 567382, RunPod) SGLang pin from 25c15d74 to the new main head — one-line change to each onstart, we have the workflow. Check with: curl -s https://api.github.com/repos/sgl-project/sglang/pulls/32052 | jq .merged_at. If the quality drift ever matters (long important session needing determinism), temporary mitigation: add --disable-radix-cache (loses prefix-cache speedup, ~10-20s re-prefill per turn at 40-60k ctx). Full audit table is in RUNPOD_RUNBOOK.md 'Open PRs affecting this stack' section, committed to kianwoon/qwen38-27b-deploy.
