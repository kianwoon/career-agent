# Modal + vLLM serving gotchas

- **Date**: 2026-08-17T00:23:29+0800
- **Type**: lesson

## What happened

Deploying vLLM model on Modal hit 3 non-obvious traps: (1) vllm/vllm-openai image ENTRYPOINT is 'vllm' which hijacks Modal container bootstrap and crash-loops — fix with .entrypoint([]) on the Modal image; (2) Modal Volume at-exit commit raced with a 49.8 GB shard write and silently dropped it — snapshot_download reported success but the file vanished; (3) GDN/gated-deltanet models (Qwen3.5 arch) cap max_num_seqs by available Mamba cache blocks (649 at 0.92 util on 96GB) — vLLM default 1024 crashes CUDA graph capture. Also: Modal proxy auth requires wk-/ws- proxy tokens (modal workspace proxy-tokens create), NOT the ak- CLI API token.

## Root cause / fix

For Modal volumes: download big files individually, verify sizes against the index, and call vol.commit() explicitly before exit. For vLLM on Modal: override entrypoint, and set --max-num-seqs below the Mamba block limit reported in engine logs. Acceptance gate: fresh-container listing shows all shards + a 200 from /v1/models and /v1/chat/completions.
