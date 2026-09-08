# Cache-efficiency key reads 0% with self-hosted models

- **Date**: 2026-08-18T20:33:10+0800
- **Type**: lesson

## What happened

Self-hosted qwen3.8 (vLLM on RunPod/Modal) showed 0% cache efficiency on the opencode cache-efficiency key. Root cause: the vLLM endpoint returns usage.prompt_tokens_details=null on every request (verified with 3x identical 3.5k-token probes), so opencode's openai-compatible provider records tokens.cache.read=0 for all turns. The key computes cache/(input+output+reasoning) from opencode.db session columns — other providers (deepseek/glm) showed large cacheR values, proving the key + opencode mapping work fine.

## Root cause / fix

When a self-hosted model shows 0% cache efficiency: (1) check opencode.db session table tokens_cache_read/write for that provider's sessions; (2) probe the endpoint directly with 2-3 identical long prompts and inspect usage.prompt_tokens_details.cached_tokens; (3) fix is on the server side — enable vLLM prefix caching (--enable-prefix-caching) / use a build that reports cached_tokens. Do not 'fix' the key — 0% is an honest reading of what the provider reports.
