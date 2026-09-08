# vast.ai template onstart 16384-char cap breaks base64 scripts

- **Date**: 2026-08-27T05:04:07+0800
- **Type**: lesson

## What happened

Pushing our MiaAI-derived vLLM onstart (20KB base64) to vast.ai failed with 'Param onstart greater than max length 16384 chars' — only surfaced on direct REST PUT, CLI masked it as 'response is not valid JSON'. Also: POST /api/v0/templates/ = 404, correct endpoint is PUT /api/v0/template/ with hash_id INSIDE the body (field name 'onstart', tag field is 'tag' not 'image_tag'). Fix: tar+gzip both scripts then base64 (6.4KB), outer onstart decodes + extracts + nohups the inner script.

## Root cause / fix

When embedding scripts in vast templates: gzip+b64 to fit the 16K cap; verify with search templates + decode payload roundtrip; expect hash_id rotation after every update; test flags inside extracted inner script, not the outer wrapper
