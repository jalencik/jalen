"""
One Google login, shared by Gmail and Calendar.

WHY OAUTH AND NOT A PASSWORD
----------------------------
He asked, more than once and with some frustration, why Jalen can't just
type his Gmail address and password like he would. The honest answer is not
"I'm not allowed" — it's that the password route is both worse and weaker:

  * Google's sign-in actively detects automated form entry. The realistic
    outcome of scripting it is not access, it's a security lock on the
    account and a phone-verification loop.
  * It cannot survive two-factor auth, which he has on.
  * The credential would have to live somewhere Jalen can read it, which
    makes every future bug a credential-disclosure bug.

OAuth is the route Google actually built for this, and it is strictly MORE
capable: he approves once, in his own browser, and Jalen receives a refresh
token scoped to exactly the permissions granted — never the password, and
revocable at myaccount.google.com/permissions without changing it.

SCOPES ARE DELIBERATELY NARROW
------------------------------
Not `https://mail.google.com/` (full account control, including permanent
deletion). Read, compose drafts, send, and calendar events — nothing else.
The consent screen he sees lists exactly this, so a broad scope would be a
broad promise made in his name.

Changing SCOPES invalidates the stored token by design: a token issued for
yesterday's permissions must not silently keep working after the code starts
asking for more. Delete data/google_token.json and re-run the connect script.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN_PATH = ROOT / "data" / "google_token.json"

SCOPES = [
    # Read and search mail. Cannot modify or delete anything.
    "https://www.googleapis.com/auth/gmail.readonly",
    # Create drafts. A draft is not a send — safety.yaml classifies
    # draft_email GREEN and send_email RED precisely on that distinction.
    "https://www.googleapis.com/auth/gmail.compose",
    # Send. Gated behind the RED tier, so it always asks out loud first.
    "https://www.googleapis.com/auth/gmail.send",
    # Read and create calendar events. Not calendar settings, not sharing.
    "https://www.googleapis.com/auth/calendar.events",
]


class GoogleNotConnected(RuntimeError):
    """Raised when there is no usable token. Carries what to do about it."""


def _scopes_match(stored: list[str] | None) -> bool:
    return set(stored or []) == set(SCOPES)


def have_token() -> bool:
    """True if a token file exists for exactly the current scope set."""
    if not TOKEN_PATH.exists():
        return False
    try:
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return _scopes_match(data.get("scopes"))


def load_credentials(*, allow_refresh: bool = True):
    """
    Return usable Google credentials, or raise GoogleNotConnected.

    Never launches a browser. A voice assistant that pops a consent screen
    mid-sentence because a token quietly expired is worse than one that says
    "Google needs reconnecting" and carries on — so the interactive flow
    lives in connect() and is only ever reached from the connect script.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_PATH.exists():
        raise GoogleNotConnected(
            "Google isn't connected yet. Run: "
            ".venv\\Scripts\\python.exe scripts\\connect_google.py"
        )

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except ValueError as exc:
        raise GoogleNotConnected(
            f"The stored Google token is unreadable ({exc}). Delete "
            "data/google_token.json and reconnect."
        ) from exc

    if not _scopes_match(list(creds.scopes or [])):
        raise GoogleNotConnected(
            "The stored Google token was issued for different permissions "
            "than the code now needs. Delete data/google_token.json and "
            "reconnect."
        )

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token and allow_refresh:
        try:
            creds.refresh(Request())
        except Exception as exc:
            # The common cause is not a bug: an OAuth consent screen left in
            # "Testing" expires refresh tokens after 7 days. CREDENTIALS.md
            # says to publish the app; this message repeats it at the moment
            # it actually bites, which is the only moment it gets read.
            raise GoogleNotConnected(
                f"Google refused to refresh the login ({type(exc).__name__}). "
                "If the OAuth consent screen is still in Testing mode, "
                "tokens expire after 7 days — publish the app in Google "
                "Cloud Console, then reconnect."
            ) from exc
        _save(creds)
        return creds

    raise GoogleNotConnected(
        "The Google login has expired and can't be refreshed. Run: "
        ".venv\\Scripts\\python.exe scripts\\connect_google.py"
    )


def _save(creds) -> None:
    """
    Persist the token with owner-only permissions where the OS supports it.

    data/ is already git-ignored wholesale, and .gitignore also names
    token.json and client_secret*.json — but a refresh token is a standing
    grant on his mail, so it gets file-level protection too rather than
    relying on one .gitignore line staying correct forever.
    """
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass  # Windows ACLs don't map onto chmod; the directory is the guard


def connect(*, port: int = 0):
    """
    Run the interactive consent flow. Only ever called by connect_google.py.

    Opens his browser, waits for him to approve, and stores the result.
    Returns the account's email address so the caller can confirm WHICH
    account got connected — silently authorising the wrong Google account is
    a failure that looks exactly like success until it reads the wrong inbox.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_SECRET.exists():
        raise GoogleNotConnected(
            f"{CLIENT_SECRET.name} not found in {ROOT}. See CREDENTIALS.md — "
            "it's the OAuth *desktop app* client, downloaded from Google "
            "Cloud Console."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(
        port=port,
        # Without these two, Google may return an access token with no
        # refresh token, and the connection silently dies in an hour.
        access_type="offline",
        prompt="consent",
        open_browser=True,
        authorization_prompt_message=(
            "\nOpening your browser to sign in to Google.\n"
            "If it doesn't open, visit this URL yourself:\n\n    {url}\n"
        ),
        success_message=(
            "Jalen is connected to Google. You can close this tab."
        ),
    )
    _save(creds)
    return whoami(creds)


def whoami(creds=None) -> str:
    """The email address of the connected account."""
    from googleapiclient.discovery import build

    creds = creds or load_credentials()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "unknown")


def gmail_service():
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=load_credentials(), cache_discovery=False)


def calendar_service():
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=load_credentials(), cache_discovery=False)
