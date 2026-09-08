# DeepInfra LLM calls need longer timeout than Cerebras

- **Date**: 2026-08-14T23:32:33+0800
- **Type**: lesson

## What happened

When evaluating a non-Cerebras model (DeepInfra deepseek-v4-flash) through the V6 rewrite harness, hardcoded timeout=120 in direct_rewrite.py and production.py caused read-timeout failures; DeepInfra returns in ~30s for large paragraphs but can exceed 120s under load. Also best_of_n=2 x 2 lanes = 4 rewrite attempts makes slow providers ~4x slower per run.

## Root cause / fix

Use DRAFTPROOF_V6_WRITER_TIMEOUT env (added _int_env_seconds helper) for non-Cerebras providers; reduce DRAFTPROOF_V6_DIRECT_BEST_OF_N=1 and DRAFTPROOF_V6_AUTHOR_PROXY_DIVERSITY=0 when comparing models to keep run time tractable.
