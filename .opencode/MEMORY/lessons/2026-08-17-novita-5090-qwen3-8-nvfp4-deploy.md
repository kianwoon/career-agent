---
title: Novita 5090 / SGLang Qwen3.8-27B deployment decision
context: Migrating Qwen3.8-27B serving from Modal/H200/FP8/vLLM to Novita Serverless; user explored 5090+NVFP4 and SGLang H200+FP8 (official cookbook).
decision: |
  - 5090 32GB path: gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090 (ModelOpt) + vLLM 0.27.1, util 0.97, fp8 KV, max-num-seqs 16 → native 262,144 fits. Unsloth NVFP4 caps ~77K on 32GB.
  - SGLang correction: sglang#22117 was Qwen3.5-specific. SGLang cookbook has VERIFIED cells for Qwen3.8-27B: H200+FP8/BF16 AND 5090+NVFP4 (RadixArk ckpt), image lmsysorg/sglang:qwen38-27b. MTP is in-checkpoint (EAGLE flags, no draft weights). Key SGLang sizing flag: --mamba-full-memory-ratio (default 0.9 over-provisions KV, clamps concurrency).
  - SGLang on Novita: port 30000, health /health, MUST set --api-key (default unauthenticated).
  - SGLang tool parser = qwen3_coder (same XML as vLLM qwen3_xml alias).
files: "qwen inference/NOVITA_RUNBOOK.md" + novita/Dockerfile
