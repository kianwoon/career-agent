# Installing the Career Agent Browser Extension

The extension lets the Career Agent cloud run job & candidate searches in
**your own logged-in Chrome** — no tunnels, no server browsers, no blocks.

**It connects automatically to:**
`https://career-agent-kianwoon-88223cd5.koyeb.app` — zero configuration,
works on Windows, macOS, and Linux.

---

## ✅ How users install (browser-only, one click)

1. Publish the extension on the Chrome Web Store as **unlisted**:
   - Go to the [Developer Dashboard](https://chrome.google.com/webstore/devconsole)
     (one-time $5 fee), upload `dist/career-agent-extension.zip`,
     visibility = **Unlisted**, submit.
   - Review usually takes 1–3 days; you get a shareable link.
2. Share the link with users. They click it, then click **"Add to Chrome"** → **Add extension**.
3. Done — updates are delivered automatically by the store. Nothing else to do.

---

## 🛠️ Fallback: load unpacked (no store, ~60 seconds, any OS)

Use this while the store listing is pending, or if a user can't access it:

1. Send the user `dist/career-agent-extension.zip`; have them unzip it.
2. In Chrome, open `chrome://extensions`.
3. Toggle **Developer mode** (top-right).
4. Click **Load unpacked** → select the unzipped folder.

## ✔️ Verify it works

Click the **Career Agent** icon (puzzle piece → pin it). It should show
**● Connected to Career Agent**. If not, wait a few seconds — it retries
automatically.

## ⚙️ Optional: point at a different server

Click the extension icon → enter a different API base URL → **Save & connect**.

---

## For the maintainer

Rebuild the package after changes (bump `version` in `extension/manifest.json` first):

```bash
./package.sh   # → dist/career-agent-extension.zip
```

The script validates the manifest, then zips `extension/` for direct
upload to the Developer Dashboard.
