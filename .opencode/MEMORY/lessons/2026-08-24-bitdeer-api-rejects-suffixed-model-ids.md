# Bitdeer API rejects suffixed model IDs

- **Date**: 2026-08-24T23:06:37+0800
- **Type**: lesson

## What happened

Bitdeer config in opencode.json used model key 'deepseek-ai/DeepSeek-V4-Flash(0731)' but API returns 400 invalid_request_error for that ID. The API only accepts the plain ID 'deepseek-ai/DeepSeek-V4-Flash' (exact match with sample curl). Also the bitdeer provider entry lacked tool_call/limit/modalities so it was unusable as an agent model.

## Root cause / fix

For opencode provider configs, the model map key must be the EXACT model ID the API accepts (copy it from the provider's sample curl, not from a display label or versioned suffix). Test with a raw curl before wiring into opencode.json. Verified: tool_call works on deepseek-ai/DeepSeek-V4-Flash via api-inference.bitdeer.ai/v1.
