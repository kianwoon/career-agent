#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Package the Career Agent extension for Chrome Web Store upload.
#
# Single artifact: dist/career-agent-extension.zip
# Publish once (unlisted) → share the link → users click "Add to Chrome".
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

# Validate manifest + required files BEFORE zipping
python3 - <<'PY'
import json, os, sys
m = json.load(open("extension/manifest.json"))
required = ["background.js", "popup.html", "popup.js"] + list(m.get("icons", {}).values())
missing = [f for f in required if not os.path.exists(os.path.join("extension", f))]
if missing:
    sys.exit(f"❌ missing files: {missing}")
print(f"✓ manifest OK — {m['name']} v{m['version']}")
PY

mkdir -p dist
rm -f dist/career-agent-extension.zip
(cd extension && zip -qr ../dist/career-agent-extension.zip . -x '*.DS_Store')

echo "✅ Built dist/career-agent-extension.zip"
echo "   Upload at: https://chrome.google.com/webstore/devconsole"
