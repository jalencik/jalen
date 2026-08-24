"""
Reading the one-time login code from HIS email, and typing it into the box.

    "for 2FA if it asks for code, it already has an access to my email, it
     sees it reads it and copy pastes it, is that so hard?"

Not hard, and legitimate: it is his inbox, his code, completing his own 2FA.
This is what his phone does when it autofills a texted code. It is NOT a
bypass of the security step - it IS the security step, done by the person it
was sent to.

BUT A SIX-DIGIT NUMBER IN AN EMAIL IS NOT AUTOMATICALLY A LOGIN CODE, and
treating it as one is how this feature turns dangerous. So three gates, all
required, none optional:

  RECENT. Only a code from the last few minutes. A login code is a live
  thing; an old one in the inbox is not the one this login is waiting for,
  and reading "your code from last Tuesday" into a box is a bug wearing a
  helpful face.

  FROM THE RIGHT SENDER. Only from the service actually being signed into -
  OpenAI for ChatGPT, Google for Gemini. A code-shaped number in a
  newsletter is not his login code, and the sender is what tells them apart.

  NEVER SURFACED. The code is extracted by code and typed by code, straight
  into the page. It never becomes a tool result the model can see, never
  reaches the transcript, the audit log, TTS, or any other AI - exactly the
  discipline the vault holds for passwords. The only thing that comes back
  is "I filled it", redacted.

If any gate fails, nothing is typed and he is told why. A wrong code typed
confidently is worse than "I couldn't find your code".
"""
from __future__ import annotations

import re
import time
from typing import Any

# Which sender a code is trusted from, per service. A code is only his login
# code if it came from the thing he is logging into.
_CODE_SENDERS = {
    "chatgpt": ("openai.com", "chatgpt.com"),
    "openai": ("openai.com", "chatgpt.com"),
    "gemini": ("google.com", "accounts.google.com"),
    "google": ("google.com", "accounts.google.com"),
}

# Words that mark a message as being ABOUT a login code, so a random number
# in ordinary mail is not mistaken for one.
_CODE_WORDS = ("code", "verification", "verify", "one-time", "one time",
               "otp", "passcode", "security code", "2-step", "two-factor",
               "authenticate", "sign-in", "sign in", "log in", "login")

# A verification code: 4 to 8 digits, optionally the Google "G-" prefix.
# Bounded deliberately - three digits is too loose, nine is a phone number.
_CODE_NEAR = re.compile(
    r"(?:code|otp|passcode|pin|g)[^\d]{0,20}(\d{4,8})", re.I)
_CODE_BEFORE = re.compile(
    r"(\d{4,8})[^\d]{0,20}(?:is your|is the)", re.I)
_CODE_GOOGLE = re.compile(r"\bG-(\d{4,8})\b")
_CODE_BARE = re.compile(r"(?<!\d)(\d{6})(?!\d)")   # last resort: a lone 6-digit

# Numbers that are never a login code even when they are 4-8 digits.
_NOT_A_CODE = {"2024", "2025", "2026", "2027", "0000", "00000", "000000"}


def _extract_code(text: str) -> str:
    """
    The login code in this text, or "" if there isn't a convincing one.

    Ordered by confidence, not by position: a number sitting right after the
    word "code" beats a lone six-digit number elsewhere in the mail, because
    the second one might be an order number or a year. The bare-six-digit
    fallback is last and still refuses obvious non-codes (years, all-zeros).
    """
    if not text:
        return ""
    for pattern in (_CODE_GOOGLE, _CODE_NEAR, _CODE_BEFORE):
        for match in pattern.finditer(text):
            code = match.group(1)
            if code not in _NOT_A_CODE:
                return code
    for match in _CODE_BARE.finditer(text):
        code = match.group(1)
        if code not in _NOT_A_CODE:
            return code
    return ""


def _looks_like_code_mail(subject: str, snippet: str) -> bool:
    hay = f"{subject} {snippet}".lower()
    return any(word in hay for word in _CODE_WORDS)


def _from_trusted_sender(sender: str, service: str) -> bool:
    domains = _CODE_SENDERS.get(service, ())
    low = (sender or "").lower()
    return any(domain in low for domain in domains)


def _recent_code(service: str, within_minutes: float) -> tuple[str, str]:
    """
    (code, reason). code is "" when none passes every gate; reason explains.

    Never returns or logs anything but the code itself, and the code goes
    only to the one caller that types it. This is the internal half of the
    vault discipline: the secret is fetched here and handed straight to the
    code that uses it, nowhere else.
    """
    from . import gmail

    try:
        gmail._enabled()
        service_api = gmail.gmail_service()
    except Exception as exc:  # noqa: BLE001
        return "", f"I can't reach your email right now: {type(exc).__name__}"

    domains = _CODE_SENDERS.get(service, ())
    if not domains:
        return "", (f"I don't know which sender a {service} code comes from, "
                    f"so I won't guess at a number in your inbox.")

    # from: the trusted senders, newer_than a day (the finest Gmail's query
    # allows), narrowed to the last few minutes by internalDate below.
    from_clause = " OR ".join(f"from:{d}" for d in domains)
    query = f"({from_clause}) newer_than:1d"
    try:
        ids = gmail._messages(service_api, query, 10)
    except Exception as exc:  # noqa: BLE001
        return "", f"I couldn't search your email: {type(exc).__name__}"
    if not ids:
        return "", (f"No recent {service} email. If the code was just sent, "
                    f"give it a few seconds and ask me again.")

    cutoff_ms = (time.time() - within_minutes * 60.0) * 1000.0
    for entry in ids:
        try:
            msg = (service_api.users().messages()
                   .get(userId="me", id=entry["id"], format="full").execute())
        except Exception:  # noqa: BLE001
            continue
        internal = float(msg.get("internalDate", 0))
        if internal < cutoff_ms:
            continue                      # too old to be THIS login's code
        payload = msg.get("payload") or {}
        sender = gmail._header(payload, "From")
        if not _from_trusted_sender(sender, service):
            continue
        subject = gmail._header(payload, "Subject")
        snippet = msg.get("snippet") or ""
        body = gmail._extract_body(payload)
        if not _looks_like_code_mail(subject, f"{snippet} {body}"):
            continue
        code = _extract_code(f"{subject}\n{snippet}\n{body}")
        if code:
            return code, ""
    return "", (f"I found recent {service} mail but no fresh code in it - it "
                f"may have expired, or the code wasn't sent yet.")


# Where the code goes on the page: strongest signal first. autocomplete=
# one-time-code is the browser's own "this is an OTP box" and beats anything
# heuristic.
_OTP_FIELD = (
    'input[autocomplete="one-time-code"]',
    'input[name="idvPin" i]',
    'input[name*="otp" i]',
    'input[name*="pin" i]',
    'input[aria-label*="code" i]',
    'input[id*="code" i]',
    'input[type="tel"]',
)


def fill_login_code(service: str = "", within_minutes: float = 10.0) -> str:
    """
    Read his latest login code from email and type it into the box - AMBER.

    Returns only a redacted confirmation. The code is never in the return
    value, the transcript, the audit log, or any AI's context.
    """
    key = (service or "").strip().lower().replace(" ", "")
    if key not in _CODE_SENDERS:
        return ("Which login is this - ChatGPT or Gemini? I only read a code "
                "for a service I can match to a sender.")

    code, reason = _recent_code(key, within_minutes)
    if not code:
        return reason

    from .webagent import BrowserUnavailable, _Session, _first_visible

    def job(page):
        box = _first_visible(page, _OTP_FIELD, timeout=5.0)
        if box is None:
            return "NOBOX"
        try:
            box.click()
            box.fill(code)
            return "OK"
        except Exception as exc:  # noqa: BLE001
            return f"ERR:{type(exc).__name__}"

    try:
        outcome = _Session.get().do(job, timeout=60)
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"The browser failed: {type(exc).__name__}: {exc}"

    if outcome == "OK":
        return ("I filled in the code from your email. Check the box and "
                "submit it - or say 'submit' and I will.")
    if outcome == "NOBOX":
        return ("I found your code but couldn't see the code box on the "
                "page. Click it once and say 'type the code'.")
    return f"I read the code but couldn't type it: {outcome[4:]}"


REGISTRY: dict[str, Any] = {
    "fill_login_code": fill_login_code,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
