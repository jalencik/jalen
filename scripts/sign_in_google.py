"""
Sign Jalen's browser in to your Google account. Once, for months.

WHY THIS EXISTS
---------------
Jalen drives a dedicated Chrome profile at data/browser_profile, kept
separate from your everyday Chrome so the two never fight over a profile
lock and so Jalen never touches your own tabs or cookies. webagent.py's
design note says how it is meant to be set up:

    So he signs in ONCE, in a real window, as his own account. It persists
    for months. Delegation then drives that same window.

That one-time sign-in had never been done. Checked on 2026-09-20:

    data/browser_profile/Default/Preferences -> account_info: 0 entries

which is why opening Google in Jalen's browser showed an account chooser
with nobody in it. That is an EMPTY PROFILE, not "ghost mode" - ghost mode
was removed in commit 4911915. Jalen launches a plain chrome.exe with no
automation flags and attaches over CDP afterwards, so navigator.webdriver
is false and Google will accept a sign-in done in this window.

WHAT THIS SCRIPT DOES, AND DOES NOT
-----------------------------------
It opens that exact window on the Google sign-in page and waits. YOU type
the email and the password. Nothing here reads, stores, types or forwards a
credential - it launches a browser and then watches a preferences file for
an account name to appear.

    .venv\\Scripts\\python.exe scripts\\sign_in_google.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.config import CONFIG                      # noqa: E402
from jarvis.tools.webagent import (                   # noqa: E402
    PROFILE_DIR, _chrome_exe, _kill_stale_profile_chrome,
)

PREFERENCES = PROFILE_DIR / "Default" / "Preferences"
SIGN_IN_URL = "https://accounts.google.com/"


def signed_in_as() -> list[str]:
    """Which accounts the profile currently holds. Names only, never tokens."""
    if not PREFERENCES.exists():
        return []
    try:
        data = json.loads(PREFERENCES.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    return [a.get("email", "") for a in (data.get("account_info") or []) if a.get("email")]


def main() -> int:
    wanted = str(CONFIG.get_path("web.google_account", "") or "").strip()

    print("Jalen's browser profile:")
    print("   ", PROFILE_DIR)
    already = signed_in_as()
    if already:
        print("    already signed in as:", ", ".join(already))
        if wanted and wanted in already:
            print()
            print("That is the account config/jarvis.yaml names. Nothing to do.")
            return 0
        print("    config/jarvis.yaml names:", wanted or "(nothing)")
        print("    Opening anyway so you can add or switch account.")
    else:
        print("    no Google account yet - this is why you saw an empty chooser")
    print()

    exe = _chrome_exe()
    if not exe:
        print("I can't find chrome.exe. Install Chrome, or check the paths in")
        print("jarvis/tools/webagent.py.")
        return 1

    # A crash can leave a chrome.exe holding this profile; a second launch
    # would hand off to it and this window would never appear. Only ever
    # matches processes whose command line names JALEN'S profile directory -
    # your everyday Chrome runs on a different one and is never touched.
    _kill_stale_profile_chrome()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print("Opening Jalen's Chrome on the Google sign-in page.")
    print()
    print("  1. Sign in as:", wanted or "the account you want Jalen to use")
    print("  2. Complete any 2FA. Take as long as you need.")
    print("  3. Leave the window open until this script says it is done.")
    print()
    print("Nothing here types for you, and nothing here reads your password.")
    print()

    try:
        subprocess.Popen(
            [exe,
             f"--user-data-dir={PROFILE_DIR}",
             "--no-first-run", "--no-default-browser-check",
             "--no-service-autorun", "--password-store=basic",
             SIGN_IN_URL],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Chrome wouldn't start: {type(exc).__name__}: {exc}")
        return 1

    # Chrome writes Preferences on a timer and on exit, so this can lag the
    # actual sign-in by a few seconds. Waiting is the whole job, so wait
    # generously and say what is happening rather than sitting silent.
    deadline = time.monotonic() + 600
    seen = set(already)
    print("Waiting (up to 10 minutes)", end="", flush=True)
    while time.monotonic() < deadline:
        time.sleep(5)
        now = set(signed_in_as())
        if now - seen:
            print()
            print()
            print("Signed in as:", ", ".join(sorted(now - seen)))
            if wanted and wanted not in now:
                print(f"NOTE: config/jarvis.yaml names {wanted}, which is not")
                print("in this profile. Jalen will look for that one.")
                return 1
            print()
            print("That persists. Jalen's delegation, sign-ins and form")
            print("filling all run in this profile from now on.")
            print()
            print("Check it any time with:")
            print(r"    .venv\Scripts\python.exe scripts\sign_in_google.py")
            return 0
        print(".", end="", flush=True)

    print()
    print()
    print("No new account appeared in the profile within ten minutes.")
    print("If you did sign in, close that Chrome window (Chrome flushes")
    print("Preferences on exit) and run this again to confirm.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
