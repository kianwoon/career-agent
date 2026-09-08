# opencode.json custom model needs attachment:true for images

- **Date**: 2026-08-27T01:22:18+0800
- **Type**: lesson

## What happened

Added glm-5.3-flash with modalities.input=[text,image] but images still wouldn't attach. In OpenCode, modalities alone is insufficient - the 'attachment': true flag is required to enable image attachments in prompts. Also output limit was 131072 not 128000.

## Root cause / fix

When adding a custom model entry under provider.models, fetch canonical specs from https://models.dev/api.json instead of guessing from vendor docs, and always include attachment:true + full modalities together.
