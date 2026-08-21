"""
Connect Jalen to Gmail + Google Calendar. Run once.

    .venv\\Scripts\\python.exe scripts\\connect_google.py

What happens: your browser opens, you sign in to Google and press Allow,
and Jalen stores a refresh token in data/google_token.json.

Jalen never sees your password. The token is scoped to exactly four
permissions — read mail, create drafts, send mail, calendar events — and
you can revoke it any time at myaccount.google.com/permissions without
changing your password.

    --status   check the current connection without changing anything
    --logout   delete the stored token
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.integrations import google_auth  # noqa: E402

BAR = "=" * 68


def status() -> int:
    if not google_auth.have_token():
        print("Not connected. Run this script with no arguments to connect.")
        return 1
    try:
        print(f"Connected as {google_auth.whoami()}")
        print(f"Token: {google_auth.TOKEN_PATH}")
        return 0
    except google_auth.GoogleNotConnected as exc:
        print(f"Stored token is not usable: {exc}")
        return 1


def logout() -> int:
    if google_auth.TOKEN_PATH.exists():
        google_auth.TOKEN_PATH.unlink()
        print("Stored Google token deleted.")
    else:
        print("There was no stored token.")
    print("Also revoke access at https://myaccount.google.com/permissions")
    return 0


def connect() -> int:
    print(BAR)
    print("  Connecting Jalen to Gmail and Google Calendar")
    print(BAR)
    print()
    print("  Jalen is asking for exactly these permissions:")
    for scope in google_auth.SCOPES:
        print(f"    - {scope.rsplit('/', 1)[-1]}")
    print()
    print("  It will NOT ask for your password, and cannot permanently")
    print("  delete mail. Revoke any time at:")
    print("    https://myaccount.google.com/permissions")
    print()

    if google_auth.have_token():
        print("  A connection already exists.")
        try:
            print(f"  Currently connected as: {google_auth.whoami()}")
        except google_auth.GoogleNotConnected:
            pass
        answer = input("  Reconnect anyway? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("  Left as it was.")
            return 0

    print("  Opening your browser...")
    print()
    try:
        email = google_auth.connect()
    except google_auth.GoogleNotConnected as exc:
        print(f"  FAILED: {exc}")
        return 1
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        print()
        print("  Common causes:")
        print("   - You closed the browser tab before pressing Allow.")
        print("   - This Google account is not listed as a Test user on the")
        print("     OAuth consent screen in Google Cloud Console.")
        print("   - The consent screen is still in Testing mode, which is")
        print("     fine to connect but expires the login after 7 days.")
        return 1

    print()
    print(BAR)
    print(f"  Connected as {email}")
    print(f"  Token stored at {google_auth.TOKEN_PATH}")
    print(BAR)
    print()
    print("  If that is not the account you meant, run:")
    print("    .venv\\Scripts\\python.exe scripts\\connect_google.py --logout")
    print("  and connect again.")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--status":
        raise SystemExit(status())
    if arg == "--logout":
        raise SystemExit(logout())
    raise SystemExit(connect())
