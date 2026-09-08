# Fork release process and tag-creation retry

- **Date**: 2026-08-31T19:54:29+0800
- **Type**: decision

## Context

Publishing a fork release with --target <full-SHA> immediately after pushing can 422 ('Release.target_commitish is invalid') because the SHA may not be fully propagated on GitHub's side; a retry with --target main works. Also: local main can DIVERGE from fork remote when a CI workflow commit is pushed from elsewhere (GitHub UI) — rebase fails on upstream cherry-picked commits, use git merge instead.

## Decision / rationale

Fork release recipe: build prod (OPENCODE_CHANNEL=prod build + package:mac), verify-prod gate, then: gh release create v1.18.25-fork.N --repo kianwoon/opencode-app --target main --title 'OpenCode Desktop — <feature>' --notes-file notes.md dist/opencode-desktop-mac-arm64.{dmg,zip}; gh release edit --latest if needed. If push rejected non-FF from own CI commit: git pull --rebase fails, use git merge github/main. GH_TOKEN from git credential fill (osxkeychain).
