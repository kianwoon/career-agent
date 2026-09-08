# Wizard headed-browser 502 on Koyeb

- **Date**: 2026-08-28T00:07:23+0800
- **Type**: lesson

## What happened

Source wizard launched a headed Chromium inside the headless Koyeb container; launch always failed and the route returned 502 'Could not launch wizard browser'.

## Root cause / fix

Any feature where the USER must see/drive a browser must connect over CDP (BRAVE_CDP_URL, per services/session.py) instead of launching headless=False in the container. Koyeb CLI logs: koyeb service logs <svc> --app <app> (no 'logs' subcommand; tail flag is boolean).
