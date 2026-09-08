# Modal web_server hangs on vLLM streaming — use @app.server instead

- **Date**: 2026-08-17T21:20:49+0800
- **Type**: lesson

## What happened

Serving vLLM/SGLang via @modal.web_server on Modal caused streaming responses to hang (server generated tokens fine, but client received 0 bytes). Non-streaming worked; streaming timed out. Root cause: @modal.web_server buffers responses and doesn't flush SSE. Fix: use @app.server (Modal's flash endpoint) with @modal.enter()/@modal.exit() lifecycle to Popen the vLLM subprocess. The .modal.direct endpoint streams SSE correctly. Also: NVFP4 Qwen3.8-27B needs --kv-cache-dtype turboquant_4bit_nc and MTP-3 speculative config per MiaAI recipe.

## Root cause / fix

For Modal + vLLM/SGLang streaming (OpenCode), use @app.server not @modal.web_server. Deploy prints a .modal.direct URL. Test streaming with curl -N before wiring into OpenCode.
