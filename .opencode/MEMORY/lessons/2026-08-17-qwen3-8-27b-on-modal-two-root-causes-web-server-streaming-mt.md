# Qwen3.8-27B on Modal: two root causes (web_server streaming + MTP-3 crash)

- **Date**: 2026-08-17T21:53:24+0800
- **Type**: lesson

## What happened

To serve Qwen3.8-27B-NVFP4 via vLLM on Modal for OpenCode: (1) @modal.web_server drops streaming responses with 'Not enough data to satisfy transfer length header' (Content-Length mismatch in aiohttp proxy) -> use @app.server (flash endpoint, .modal.direct URL) instead. (2) MTP-3 speculative decoding crashes with 'CUDA error: illegal memory access' on stock vLLM nightly (needs PR #40914 patch not applied) -> remove --speculative-config. (3) Set min_containers=1 so it stays warm (cold start is 5+ min). (4) Disable thinking via --default-chat-template-kwargs '{"enable_thinking": false}'. (5) unauthenticated=True for .modal.direct so OpenCode apiKey=not-needed works.

## Root cause / fix

For Modal+vLLM Qwen NVFP4 serving: use @app.server (not web_server), drop MTP-3, min_containers=1, thinking off, unauthenticated=True. Test streaming with curl -N before wiring OpenCode.
