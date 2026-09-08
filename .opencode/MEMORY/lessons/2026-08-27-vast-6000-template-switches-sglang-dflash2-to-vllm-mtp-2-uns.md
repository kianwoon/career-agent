# Vast 6000 template switches SGLang+DFlash2 to vLLM MTP-2 unsloth-NVFP4

- **Date**: 2026-08-27T05:09:30+0800
- **Type**: decision

## Context

Template 567382 (SGLang, FP8-target forced for DFlash2 block-5) was built when we believed quantized targets break drafters. MiaAI's DGX-Spark/RTX6000 recipe shows unsloth ships MTP weights inside the NVFP4 checkpoint (model_mtp.safetensors) with calibrated FP8 kv_cache_scheme — no DFlash needed, no #40914 patch needed. YaRN x4 via --hf-overrides gives 1M ctx; triton_attn is the portable backend since checkpoint FP8-KV needs FA3/FA4 per SM arch.

## Decision / rationale

Created vast template 623354 (qwen38-6000-vllm-mtp2, hash 13e96d9156d8923ddeebdb4ea4b40bd9). x86 adaptations vs aarch64 recipe: no CUTE_DSL_ARCH=sm_121a, venv vllm==0.27.1 instead of nightly-aarch64 image, port 30000 + api-key + dual served names for infra compat. Local mirror: vast-6000-vllm-onstart.sh
