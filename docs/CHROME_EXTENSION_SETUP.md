# Jalen's Chrome extension — setup

This lets Jalen read and act on pages **inside your own everyday Chrome** —
the tabs you actually browse in — instead of the separate window it drives
otherwise. It's a one-time setup, about five minutes.

## Why this exists (the short version)

Chrome 136+ deliberately refuses to let an outside program automate the
profile you browse in, and Google refuses sign-ins from a program-driven
browser. An **extension is the other side of that wall**: it runs *inside*
Chrome, which already trusts it. This is the same reason the Claude
extension works. Jalen's extension talks to the Jalen app on your machine
through Chrome's official "Native Messaging" channel — nothing is scraped,
nothing external is contacted.

## What you need

- Google Chrome (you have 151).
- The Jalen project at `C:\Users\user\Desktop\Jarvis-setup\jarvis` with its
  `.venv` set up.

## Steps

### 1. Register the bridge

In a terminal at the project root — **no extension ID needed**, the ID is
pinned:

```powershell
.venv\Scripts\python.exe scripts\install_extension.py
```

It prints where it wrote the launcher, the host manifest, and the registry
entry. All of it is **current-user only** — no admin, nothing machine-wide.

### 2. Load the extension

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked**.
4. Choose the folder:
   `C:\Users\user\Desktop\Jarvis-setup\jarvis\browser_extension`
5. It appears as **Jalen** with its blue orb icon. Because the ID is pinned
   in the manifest, it always matches what step 1 registered — nothing to
   copy or paste.

### 3. Connect — it's automatic

1. **Fully quit and reopen Chrome** (Native Messaging hosts are read at
   startup).
2. Start Jalen as usual — the bridge comes up with it.
3. That's it. The extension connects on its own and **stays** connected: a
   live link keeps it awake while Jalen runs, and a wake-timer reconnects it
   within ~30s if Chrome or Jalen restarted. You never reconnect it by hand.

Click the **Jalen** orb in the toolbar to open the **chat panel** — a place
to type to Jalen right inside Chrome, like a side conversation. The panel
shows **connected / not connected** at the top. When you say (or type) "use
my own Chrome", "what's on this page", or "fill this form in", Jalen acts on
the tab you're looking at.

## What it can and can't do

**Can**, on a page it has access to:
- read the page (title, headings, text, form fields);
- fill ordinary form fields from `data/personal_info/profile.md`;
- click, select, check, scroll, navigate, manage tabs.

**Won't**, by design — the same boundaries as everywhere else in Jalen:
- **passwords** are never filled from the page; they come from the vault;
- **payment fields** (card, CVV, IBAN…) are recognised and left for you;
- **CAPTCHA / "prove you're human"** stops and hands to you, then resumes;
- **2FA email codes** are read from your own inbox and typed in, never
  spoken or logged (see the OTP feature).

**Can't**, because Chrome won't allow it:
- act on `chrome://` pages, the Web Store, or other extensions' pages;
- act on a site you haven't granted it access to. The extension ships with
  access to ChatGPT, Gemini and Google sign-in; for a new site, Chrome will
  ask you to allow it, or you can grant it in the extension's details.

If Jalen says the extension "isn't connected", it means Chrome is closed, the
extension was removed, or step 2 hasn't been run — it will **not** quietly
use a different browser instead.

## The security model, plainly

- The **app decides** every action. The web page can answer Jalen's
  questions and can tell it a tab changed, but it can **never** tell the app
  "here is a secret, type it". Only the app originates actions, and every
  action is classified by the same safety tiers as the rest of Jalen.
- The app and the extension are linked by a **random token** written to
  `data/bridge.json`, readable only by your account, on a loopback
  (127.0.0.1) socket that never leaves the machine. A program that can't
  read that file can't drive your browser.
- Secrets — passwords, one-time codes, the token — are never written to
  logs, never spoken, never sent to any AI.

## Removing it

- `chrome://extensions` → remove **Jalen**.
- Delete the registry key `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.jalen.bridge`
  (or run `reg delete` on it).
- Delete `native_host_manifest.json` and `jalen_bridge_host.bat` from the
  project root if you like.
