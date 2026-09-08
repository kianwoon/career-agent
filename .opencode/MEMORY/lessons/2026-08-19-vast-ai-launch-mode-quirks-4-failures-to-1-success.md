# vast.ai launch-mode quirks (4 failures to 1 success)

- **Date**: 2026-08-19T02:00:49+0800
- **Type**: lesson

## What happened

vast.ai Docker ENTRYPOINT mode (runtype args) does NOT exec onstart strings through a shell - JSON-wrapped, bash -c prefixed, and bare command strings ALL fail as single-argv exec. Working recipe: SSH launch mode + onstart 'nohup <cmd> > log 2>&1 &' + literal secrets (no shell expansion). Port -p mappings are silently ignored on SSH instances - access via SSH tunnel instead. vastai attach ssh <id> <pubkey> fixes the 'bad ownership or modes' auth failure. Every vastai update template creates a NEW hash and clears unspecified fields - always send ALL fields.

## Root cause / fix

vast working config: template 567382 (ssh mode, nohup onstart, disk 60); instance access = SSH tunnel -L 30000:127.0.0.1:30000; verify via 'vastai logs' + curl through tunnel
