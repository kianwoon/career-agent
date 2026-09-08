# opencode config: model vision declared with 'capabilities' key is silently ignored

- **Date**: 2026-08-18T20:02:49+0800
- **Type**: lesson

## What happened

User's Qwen3.8-27B RunPod model behaved text-only when pasting screenshots. Root cause: config used 'capabilities: { input: [text, image] }' on the provider model entry, but opencode's config schema only reads 'modalities: { input, output }' (provider.ts:1477). Unknown keys are dropped silently; custom providers have no models.dev fallback so input.image fell back to false and transform.ts unsupportedParts() replaced every image with a text note before the provider call.

## Root cause / fix

Use 'modalities' (not 'capabilities') for provider model input/output modalities in opencode.json. 'attachment: true' is a separate legacy boolean and does NOT enable image input. Verify with bun -e JSON.parse after editing.
