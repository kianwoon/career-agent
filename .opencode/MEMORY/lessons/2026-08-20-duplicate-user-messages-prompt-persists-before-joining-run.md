# Duplicate user messages: prompt persists before joining run

- **Date**: 2026-08-20T15:19:25+0800
- **Type**: lesson

## What happened

User reported 1 message showing 2 entries. DB analysis found 31 duplicate pairs (same text, distinct message IDs); 19 first copies were totally unanswered, 5 hit abort/error right before. Mechanism: SessionPrompt.prompt() persists the user message BEFORE loop()/ensureRunning; ensureRunning joins an in-flight run instead of starting a new one. If that run fails/aborts/exits (loop-exit check-then-act race between message read and runner Idle), the message is stranded and the prompt call returns an error while the message IS stored. TUI shows 'Failed to send prompt', user re-sends, duplicate accumulates. TUI also never sends messageID so retries can't dedupe (server upserts by message ID in projector.ts, and PromptInput supports messageID).

## Root cause / fix

When diagnosing duplicate messages: classify pairs by whether first copy was answered (assistant reply between them) — unanswered+error-adjacent pairs indicate server stranding, not user double-submit. Fix order: (1) client sends messageID per submit for idempotent retry, (2) server re-drives stranded last user message if runner idle after ensureRunning returns, (3) accurate toast when message persisted but run failed. Files: packages/opencode/src/session/prompt.ts (prompt(), createUserMessage), src/effect/runner.ts (ensureRunning), packages/tui/src/component/prompt/index.ts (~line 1103 prompt call without messageID).
