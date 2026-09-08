# Runbook: Package a production OpenCode desktop app

## Context
The Mac app lives at /Applications/OpenCode.app (Electron, prod channel, appId ai.opencode.desktop).
`package:mac` does NOT build anything — it packages whatever `out/` already contains.

## Correct chain (both steps, in order, same channel)

```bash
# 1. Build (prebuild runs build-node.ts which bakes OPENCODE_CHANNEL into the server chunk)
OPENCODE_CHANNEL=prod bun run build        # from packages/desktop

# 2. Package
OPENCODE_CHANNEL=prod bun run package:mac  # from packages/desktop

# 3. Acceptance gate (must pass; aborts on mismatch)
APP_DIR=dist/mac-arm64/OpenCode.app OPENCODE_CHANNEL=prod bun scripts/verify-prod.ts
```

Channel must be dev|beta|prod (resolveChannel in scripts/utils.ts falls back to "dev").
Prod = productName "OpenCode", appId ai.opencode.desktop, DB opencode.db.

## Extra content check (feature verification)
```bash
bunx asar extract dist/mac-arm64/OpenCode.app/Contents/Resources/app.asar /tmp/x
grep -c "<feature-string>" /tmp/x/out/main/chunks/node-*.js
```

## Install / relaunch
Replacing /Applications/OpenCode.app requires quitting the running app — user's call,
never agent-triggered (AGENTS.md: "NEVER try to restart the app").
Unsigned build note: electron-builder skips Developer ID signing if no valid identity.
