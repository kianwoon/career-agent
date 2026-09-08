# Chrome policy force-install is not a novice install path

- **Date**: 2026-08-28T22:10:39+0800
- **Type**: lesson

## What happened

Spent many iterations trying ExtensionInstallForcelist via /Library/Preferences and Managed Preferences on macOS; Chrome silently ignored it in test setups, each test launched Chrome and repeated admin-password prompts annoyed the user.

## Root cause / fix

For novice distribution of a Chrome extension, use the Chrome Web Store unlisted link (Add to Chrome, zero-config). Never prototype policy/MDM-style installs on the user's live machine; reserve enterprise policy for real MDM fleets.
