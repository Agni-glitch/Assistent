# Fox Network Capture (Chrome extension)

Sends a tab's requests and responses to Fox, so you can ask Fox to analyse them. It's small on purpose: use it as is, or as the starting point for your own extension.

## Install

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and pick this folder.
3. In Fox, open **Settings → Browser capture** and copy the address and token into the extension's popup.
4. Click **Test connection**, then **Capture this tab**.

Full guide, including the API for your own extension: [docs/browser-capture.md](../../docs/browser-capture.md).

## Files

| File | What it does |
|---|---|
| `manifest.json` | Manifest V3. Needs `debugger` to read response bodies, and host access to `127.0.0.1` and `localhost` to reach Fox. |
| `background.js` | Attaches the debugger to a tab, collects `Network.*` events into entries, and posts them to `/api/captures` every two seconds. |
| `popup.html`, `popup.js`, `popup.css` | Address, token, the response-bodies option, and start/stop. |

Fox removes cookies, auth headers and passwords from what it receives, but the extension sends what Chrome reports. Capturing only runs while Chrome shows its "is debugging this browser" bar.
