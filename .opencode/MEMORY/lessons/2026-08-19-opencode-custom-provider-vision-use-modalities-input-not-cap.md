# OpenCode custom-provider vision: use modalities.input not capabilities.input

- **Date**: 2026-08-19T06:35:49+0800
- **Type**: lesson

## What happened

OpenCode custom openai-compatible providers need models.<id>.modalities.input = ["text","image"] to enable image paste. The capabilities:{input:[...]} block (from v2 docs) is SILENTLY IGNORED — the client gate blocks pasted images with 'model does not support image input' and worse, image-to-text bridge plugins + advertised MCP vision tools (zai) make models CALL a vision MCP instead of seeing natively (even multimodal ones - tool habit overrides native ability). Full working chain requires ALL of: modalities declaration + image bridge plugin allowlist (or disable) + vision MCP disabled for that agent + server-side multimodal active (sglang: 'Multimodal data loading enabled' in logs; verify via image_tokens in usage response).

## Root cause / fix

Diagnosis order for 'model cannot see images' in OpenCode: (1) check modalities.input key exists, (2) check no bridge plugin rewrites FileParts, (3) check MCP vision tools not advertised to the model, (4) server log multimodal lines + image_tokens in response
