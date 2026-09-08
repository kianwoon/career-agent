# Fork release process (fork.6 pattern)

- **Date**: 2026-08-31T20:10:22+0800
- **Type**: decision

## Context

Publishing fork releases needs push + tag + release + assets, and past attempts hit 422 target errors and stale gh tokens.

## Decision / rationale

Recipe: (1) git push github main. (2) GH_TOKEN from `git credential fill` (osxkeychain; gh keyring token is stale). (3) gh release create v1.18.25-fork.N --repo kianwoon/opencode-app --target main --title 'OpenCode Desktop — <feature>' --notes-file <file> --latest — use --target main, NOT a full SHA (422). (4) gh release upload <tag> with both dist artifacts. A user-added CI workflow (.github/workflows/fork-release.yml) auto-creates a bare release when a v* tag is pushed; creating the release first (as above) makes CI skip. Verify with gh release view --json name,tagName,assets.
