# Confounded A/B: different output lengths + wrong log file led to wrong 'steps 3 wins' conclusion

- **Date**: 2026-08-19T07:57:30+0800
- **Type**: lesson

## What happened

Testing SGLang EAGLE steps on qwen38-vast: first A/B compared steps3 (539 tokens, 52 tok/s) vs steps1 (850 tokens, 46 tok/s) — different outputs because the model's argmax drifts with cache/batch state — and read accept stats from the wrong log (sglang.log vs restart.out, depending on how restart.sh was launched). Concluded steps 3 wins. Redid with fixed BENCH_MAXT=400 (both hit the cap, identical workload) and correct log file: steps1/draft2 = 60.4 tok/s @ accept 0.57-0.90 vs steps3/draft4 = 52.3 tok/s. SGLang's MTP doc recipe was right; steps 1 is ~15% faster. Now running steps 1/draft 2.

## Root cause / fix

A/B testing inference configs: (1) pin output length with max_tokens so both configs do identical work; (2) check which log file the server actually writes to (restart.out vs sglang.log depends on how restart.sh was launched — runbook gotcha); (3) read accept stats only from after the last 'server is fired up' marker; (4) 3+ runs per config.
