# opencode model config: 3 conflicting layers silently fell back to glm-4.7

- **Date**: 2026-08-30T21:53:41+0800
- **Type**: lesson

## What happened

User set GLM-5.3-flash as intended model but sessions ran glm-4.7. Root cause: top-level model=glm-4.7 (stale), agent .md frontmatter referenced deepseek/deepseek-v4-flash while the deepseek provider had models:{} (unresolvable → silent fallback), and inline agent.build/agent.plan in opencode.json duplicated the .md files with yet another model (qwen38-vast localhost). opencode resolves per-layer: broken model refs don't error, they fall back.

## Root cause / fix

When a session runs an unexpected model: grep modelID from ~/.local/share/opencode/storage/message/*/*.json first, then audit ALL config layers (top-level model, each agent .md frontmatter, inline agent.* in opencode.json) and verify each referenced model exists in its provider's models list. Keep ONE agent definition (the .md file), set one top-level default, and validate with: opencode run --model <id> 'reply MODEL_OK'
