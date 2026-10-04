# Jalen's Chrome extension — setup

This lets Jalen see the tab you are looking at in your own everyday Chrome (its title, address, headings and form fields) and fill an ordinary form there from your personal-info folder. Otherwise it uses the separate Chrome it drives itself. The extension also gives you a chat panel inside Chrome. It's a one-time setup, about five minutes.

## Why this exists (the short version)

Chrome 136 and later refuse to let an outside program automate the profile you browse in, so Jalen's own browser is a separate Chrome profile (`data\browser_profile`) with its own sign-ins. An extension is the way into the profile you actually use: it runs inside Chrome, which already trusts it. Jalen's extension talks to the Jalen app on this machine through Chrome's official "Native Messaging" channel, and neither the extension nor the bridge contacts anything outside the machine. What Jalen reads from the page (title, address, headings, field labels) becomes part of its conversation with Claude, like anything else it reads.

## What you need

- Google Chrome 116 or newer (this laptop has 154).
- The Jalen project's **main checkout**, `C:\path\to\jalen`, with its `.venv` set up (not a `.claude\worktrees\...` copy).

## Steps

### 1. Register the bridge

In a terminal at the root of the main checkout (no extension ID needed; the ID is pinned):

```powershell
.venv\Scripts\python.exe scripts\install_extension.py
```

It prints where it wrote the launcher, the host manifest and the registry entry. Everything is **current-user only**: no admin rights, nothing machine-wide. The launcher always starts the bridge from this folder, so this is also the folder Jalen must run from (step 3).

### 2. Load the extension

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked**.
4. Choose the folder:
   `C:\path\to\jalen\browser_extension`
5. It appears as **Jalen** with its blue orb icon. Because the ID is pinned
   in the manifest, it always matches what step 1 registered — nothing to
   copy or paste.

### 3. Connect — it's automatic

1. **Fully quit and reopen Chrome** (Native Messaging hosts are read at
   startup).
2. Start Jalen from the main checkout (`.\jalen.ps1 start` or `.\jalen.ps1 text`). The bridge comes up whenever Jalen is running, in any mode. A Jalen started from any other folder (a worktree or a copy) publishes its bridge in that folder's `data\`, and the extension will never find it.
3. That's it. The extension connects on its own and **stays** connected: a
   live link keeps it awake while Jalen runs, and a wake-timer reconnects it
   within ~30s if Chrome or Jalen restarted. You never reconnect it by hand.

Click the **Jalen** orb in the toolbar to open the **chat panel**, a place to type to Jalen inside Chrome. If the orb is hidden, pin it from the puzzle-piece menu. The panel shows **connected / not connected** at the top. Whatever you type there goes straight to Claude, like any typed request.

To act on the tab you are looking at, say so: "in my own Chrome, what's on this page", "fill this form in my Chrome". Jalen currently decides per request whether to use your Chrome or its own separate Chrome, so a bare "this page" can end up in its own.

## What it can and can't do

**Can**, on the tab you are looking at:
- say whether the extension is connected;
- read the page's title, address and headings, how many form fields it has, and whether one of them is a password field;
- list the visible form fields (label, type, required);
- fill ordinary fields from the `Label: value` lines in the `.md` files in `data\personal_info\` (`profile.md` is the usual one), then tell you what it filled and what it still needs. Jalen announces the fill before doing it, and refuses it on a turn where it has just read something someone else wrote.

It does not yet read the page's body text, click, submit, scroll, navigate or manage tabs through the extension. For those, Jalen uses its own separate Chrome.

**Won't**, through the extension, by design:
- **passwords**: password fields are skipped and named, and it never types one here. (Typing a vault password into a sign-in page, and typing an emailed one-time code, happen only in Jalen's own Chrome.)
- **payment fields** (card, CVV, IBAN…) are recognised and left for you.

The extension never clicks or submits, so a CAPTCHA or "prove you're human" check is always yours to do.

**Can't**, because Chrome won't allow it:
- act on pages Chrome keeps extensions out of (`chrome://` pages, the Web Store, other extensions' pages), or on local `file://` pages.

The manifest asks for access to every http and https site, plus the permissions nativeMessaging, tabs, scripting, storage, alarms and sidePanel. You can narrow site access in `chrome://extensions` → Jalen → Details → Site access.

## When it says "not connected"

If Jalen says the extension isn't connected, the usual causes are:
- Jalen isn't running, or is running from a folder other than the one you registered;
- Chrome is closed, or hasn't been fully restarted since step 1;
- the extension isn't loaded (step 2).

Run `.\jalen.ps1 extension` (the same as `.\jalen.ps1 browser`; it runs `scripts\diagnose_extension.py`). It checks each link (the app, `data\bridge.json`, the registry entry, the host manifest, the launcher, the extension) and says which one is broken and what to do. Jalen does not switch to its own Chrome by itself; it tells you and offers to.

Two things that can look odd:
- Only one Chrome connects at a time. If a second Chrome window connects, it replaces the first.
- The panel can show "connected" for a moment before the bridge finds out that Jalen is not running.

## The security model, plainly

- The **app decides** every action. The web page can answer Jalen's
  questions and can tell it a tab changed, but it can **never** tell the app
  "here is a secret, type it". Only the app originates actions, and every
  action is classified by the same safety tiers as the rest of Jalen.
- The app and the extension are linked by a **new random token each time Jalen starts**. The token is written with the port to `data\bridge.json` in the project folder and deleted when Jalen stops. The code asks for owner-only permissions on that file; on Windows that is best effort, so the protection is that the project lives inside your own user folder. The link is a loopback (127.0.0.1) socket that never leaves the machine, and the bridge hands over the token only while the Jalen that wrote it is still running. A program that can't read that file can't drive your browser.
- Secrets — passwords, one-time codes, the token — are never written to
  logs, never spoken, never sent to any AI.

## Removing it

- `chrome://extensions` → remove **Jalen**.
- Delete the registry key `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.jalen.bridge`
  (or run `reg delete` on it).
- Delete `native_host_manifest.json` and `jalen_bridge_host.bat` from the
  project root if you like.
