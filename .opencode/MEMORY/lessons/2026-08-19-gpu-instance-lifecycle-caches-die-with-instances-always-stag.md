# GPU instance lifecycle: caches die with instances; always stage launch scripts + know your rebuild path

- **Date**: 2026-08-19T16:02:29+0800
- **Type**: lesson

## What happened

vast.ai GPU reaping ('previous GPU gone') forces new instance = fresh disk: all HF caches, pip installs (PR builds!), and scripts lost. On new host: (1) check what's already cached (du /root/hf/hub - sometimes prior tenant left weights: new instance had NVFP4 + DFlash + BF16 + FP8 = 104GB!), (2) recreate scripts from template/runbook, (3) pip PR builds must be reinstalled. RunPod alternative: network volume survives everything - keep authoritative copy of launch scripts in volume not just root. Also: kill+relaunch compound SSH commands self-destruct via pkill self-match - use [s] bracket trick AND separate ssh invocations per step (kill, clear-log, launch as 3 commands).

## Root cause / fix

On instance loss: ls the HF cache first, relaunch proven config before experiments, never chain pkill+launch in one ssh command
