# Shell: outer truncate-redirect clobbers inner appends to the same log file

- **Date**: 2026-08-19T19:36:04+0800
- **Type**: lesson

## What happened

The hardened vast onstart wrote diagnostic markers to /root/sglang.log via 'echo ... >> ' (append) while the whole bash -c was launched with an outer 'nohup bash -c '...' > /root/sglang.log 2>&1 &' (truncate). Both fds point at the same file: the outer fd at offset 0, the inner at EOF. When the outer fd writes (or even just advances), it clobbers bytes at offset 0, destroying the early [onstart] markers. Symptom: server boots fine, but the first diagnostic lines are missing from the log — defeating the purpose of the hardening. Caught on fresh-boot verification (instance 48105680, 2026-08-19).

## Root cause / fix

When a script self-logs with append (>>) AND is launched with an outer redirect to the same file, the outer must ALSO be append (>>), never truncate (>). One-character fix: ' > /root/sglang.log' -> ' >> /root/sglang.log'. Alternatively, drop the outer redirect entirely (let nohup's own stdout/stderr go where it will) and rely only on the inner appends. Applied to vast template 567382 (hash ad8ca71d). Note: 'sed s|...|...&|' also fails — & is special in sed replacements (inserts the whole match); use a different delimiter or the Edit tool.
