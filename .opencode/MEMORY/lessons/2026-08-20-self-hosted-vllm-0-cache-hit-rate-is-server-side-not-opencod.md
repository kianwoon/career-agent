# Self-hosted vLLM 0% cache hit rate is server-side, not opencode

- **Date**: 2026-08-20T15:03:48+0800
- **Type**: lesson

## What happened

Diagnosing low cache hit rate: opencode DB showed 0% cache reads on vast.ai vLLM (qwen3.8-27b NVFP4, localhost:30000) while all managed providers (zai 95%, deepseek 93-99%, fireworks 97%) cached fine — proving opencode's prefix is stable. The vast.ai instance either runs vLLM without --enable-prefix-caching (APC) or doesn't report prompt_tokens_details.cached_tokens in usage. RunPod sibling cached but evicted fast (single-GPU KV pressure). 161M input tokens re-prefilled = 9-22h wasted GPU prefill time.

## Root cause / fix

Diagnosis recipe: query opencode.db message table (json_extract data->tokens.cache.read vs tokens.input grouped by providerID), then turn-by-turn per session — consecutive tool-loop calls with input=full context and cacheR=0 means server-side APC off/unreported, not client prefix instability. Server checks: /v1/server_info prefix_caching field, /metrics vllm:prefix_cache_cache_hit_rate. NVFP4 quant + non-default kv-cache-dtype can silently disable APC in some vLLM builds. Verify fix by re-querying DB: healthy = read share >80%.
