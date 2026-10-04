# Jalen browser extension (Manifest V3)

Runs inside the user's own Chrome and lets the Jalen app read and act on the
tabs he's actually looking at. Setup: `../docs/CHROME_EXTENSION_SETUP.md`.

## Files

- `manifest.json` — MV3. Minimal permissions: `nativeMessaging`, `tabs`,
  `scripting`, `storage`. Host access is limited to ChatGPT, Gemini and
  Google sign-in by default; any other site is `optional_host_permissions`,
  granted by the user per-site.
- `service_worker.js` — holds the Native Messaging port to the app, receives
  commands the app has already authorised, and carries them out. Tab/window
  commands use `chrome.tabs`; page commands inject `pageOp` into the target
  tab via `chrome.scripting`. It never originates a command and never decides
  an action is safe on its own.
- `popup/` — read-only status (connected? current domain?).

## The contract

Messages match `jalen/bridge/protocol.py` exactly — version 1, four shapes
(command, response, error, event). The command allowlist is fixed; there is
deliberately **no** "run arbitrary JS" command, because a brain that could
run any script in a logged-in Chrome is one prompt-injection away from being
asked to.

## What's tested, and how

- `tests/test_bridge.py` — the protocol, Chrome's stdio framing, and the
  app↔host socket loop, without a browser.
- `tests/test_extension_page_ops.py` — lifts `pageOp` out of
  `service_worker.js` and runs it against a real Chrome DOM: reading fields,
  filling by index, refusing a password field, selecting, checking, clicking.
- `tests/test_browser_ext.py` — the Python tools on top, with a fake server.

What no automated test can cover is the Native Messaging round trip through
Chrome itself — that needs the unpacked extension loaded, which is the user's
one-time setup step.
