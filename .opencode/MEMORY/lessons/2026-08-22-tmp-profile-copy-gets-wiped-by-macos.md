# tmp profile copy gets wiped by macOS

- **Date**: 2026-08-22T16:41:06+0800
- **Type**: lesson

## What happened

The Brave profile copy in /tmp (used for CDP automation) was wiped by macOS cleanup, losing the LinkedIn login. After a reboot/time, /tmp clears and the automation silently loses auth.

## Root cause / fix

Store the profile copy outside /tmp (e.g. ~/.cache/career-agent/brave-profile) or re-copy from the live profile before launching. Check login state (feed URL vs /login) before assuming the session works.
