# vLLM tool-call parser for Qwen3.5-family models

- **Date**: 2026-08-17T00:48:09+0800
- **Type**: lesson

## What happened

AEON Qwen3.8-27B (qwen3_5 arch) emits XML-style tool calls: <tool_call><function=name><parameter=key>value</parameter></function></tool_call>. Using the common --tool-call-parser hermes (JSON format) does NOT error but silently leaves raw tool-call text in message.content — agents then can't use tools. Correct parser: qwen3_xml (aliases: qwen3_coder, mimo), defined in vllm/parser/qwen3.py. Also note vLLM nightly moved tool parsers from vllm/entrypoints/openai/tool_parsers/ to vllm/tool_parsers/.

## Root cause / fix

For any Qwen3.5/Qwen3-coder-style model: vllm serve ... --enable-auto-tool-choice --tool-call-parser qwen3_xml. Acceptance test: POST /v1/chat/completions with a tools array; response must contain message.tool_calls[] with parsed JSON arguments and finish_reason=tool_calls, NOT raw <tool_call> text in content. Debug tip: 'modal container exec <ta-id> -- bash -c ...' lets you grep the exact vLLM install.
