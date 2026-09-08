# Fork releases use 'OpenCode Desktop — <feature>' titles

- **Date**: 2026-08-31T19:47:42+0800
- **Type**: decision

## Context

User dislikes 'Fork Release N' numbered titles on the fork's GitHub releases (kianwoon/opencode-app); the established house style across all releases is 'OpenCode Desktop — <short feature description>' with an em dash.

## Decision / rationale

Name every new release '<feature summary>' in the house style, never 'Fork Release N'. Tags like v1.18.25-fork.N are fine and unchanged. Rename done 2026-08-31 for fork.1-4 via gh release edit --title plus matching '# ' H1 fix in the body.
