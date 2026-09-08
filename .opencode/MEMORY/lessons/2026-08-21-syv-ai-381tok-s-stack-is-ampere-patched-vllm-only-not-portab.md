# syv-ai 381tok/s stack is Ampere+patched-vLLM only, not portable to sm120

- **Date**: 2026-08-21T12:34:49+0800
- **Type**: lesson

## What happened

Investigated the viral 381 tok/s Qwen3.8-27B claim (syv-ai/qwen38-27b-rtx3090). Root cause: lookup-augmented drafting + DFLASH_TOKENS=15 adaptive verify block + prefix caching, all in vLLM 0.27.1 with 9 custom patches. These are sm86-tested. On Blackwell sm120 the fused GDN decode kernel is unreachable in 0.27.1 (HF discussion #51: two gates; one fixed on main, one still open) making MTP/spec a net 3.6x loss. So the 5090 template stays SGLang+no-spec. Portable lessons: mamba-ssm-dtype float32->float16 halves state traffic with no quality loss; chunked-prefill 2048 for single-stream; SGLang radix extra_buffer IS the prefix-cache (no --enable-prefix-caching flag in SGLang). Applied fp16+2048 to vast template 573236 (new hash 694e2c4a).

## Root cause / fix

Before porting a viral speed-optimization repo to a different GPU arch, check the arch-specific kernel gates in the pinned engine version; sm86 vs sm120 fused GDN is a hard blocker.
