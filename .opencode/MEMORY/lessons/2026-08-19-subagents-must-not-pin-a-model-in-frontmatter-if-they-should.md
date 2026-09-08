# Subagents must not pin a model in frontmatter if they should inherit the primary agent's model

- **Date**: 2026-08-19T04:42:55+0800
- **Type**: lesson

## What happened

Subagents (explorer/implementer/reviewer) had 'model: deepseek/deepseek-v4-flash' pinned in their frontmatter, so they always ran on v4-flash even when the primary build/plan agent was on a different model (e.g. qwen3.8-27b). The Task tool only falls back to the caller's live model when agent.model is undefined: 'const model = agent.model ?? { modelID: msg.info.modelID, providerID: msg.info.providerID }'.

## Root cause / fix

Remove the 'model:' line from subagent frontmatter so OpenCode inherits the calling session's model. Verify with: grep -Hn '^model:' agent/*.md (expect none for subagents).
