"""
Reading the one-time login code from HIS email, and typing it into the box.

    "for 2FA if it asks for code, it already has an access to my email, it
     sees it reads it and copy pastes it, is that so hard?"

Not hard, and legitimate: it is his inbox, his code, completing his own 2FA.
This is what his phone does when it autofills a texted code. It is NOT a
bypass of the security step - it IS the security step, done by the person it
was sent to.

BUT A SIX-DIGIT NUMBER IN AN EMAIL IS NOT AUTOMATICALLY A LOGIN CODE, and
treating it as one is how this feature turns dangerous. So four gates, all
required, none optional:

  RECENT. Only a code from the last few minutes. A login code is a live
  thing; an old one in the inbox is not the one this login is waiting for,
  and reading "your code from last Tuesday" into a box is a bug wearing a
  helpful face.

  FROM THE RIGHT SENDER. Only from the service actually being signed into -
  OpenAI for ChatGPT, Google for Gemini. A code-shaped number in a
  newsletter is not his login code, and the sender is what tells them apart.
  The sender is the parsed From ADDRESS, not a substring of the header.

  INTO THE RIGHT PAGE. Only into that service's own sign-in page, checked
  on the page's host when the box is found and again at the typing. A page
  that asks for "the code we just sent you" and is not Google is the whole
  of a relay phishing attack, and a code typed there is his account.

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

# Where a code may be TYPED, per service: that service's own sign-in, matched
# as the host or a subdomain of it, never as a substring. The sender gate
# above says whose code it is; nothing said where it may go, so a Google
# code read from his inbox was typed into whatever page was open - which is
# the whole of a relay phishing page: "enter the code we just sent you".
# Reproduced in tests/test_a_secret_goes_only_where_he_said.py.
#
# Narrower than the senders on purpose. Google's code box is on
# accounts.google.com, and "google.com" would admit sites.google.com, where
# anyone can publish a page. ChatGPT's is on auth.openai.com (auth0 on older
# flows) or chatgpt.com itself.
_CODE_PAGES = {
    "chatgpt": ("auth.openai.com", "auth0.openai.com", "chatgpt.com"),
    "openai": ("auth.openai.com", "auth0.openai.com", "chatgpt.com"),
    "gemini": ("accounts.google.com",),
    "google": ("accounts.google.com",),
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
    """
    Is the From ADDRESS on one of the service's domains, or a subdomain?

    It was `domain in sender`, a substring of the whole header, so
    "openai.com <attacker@evil.example>" - a display name anyone can type -
    and help@openai.com.evil.example and x@notopenai.com were all OpenAI.
    """
    from email.utils import parseaddr

    _name, address = parseaddr(sender or "")
    if address.count("@") != 1:
        return False
    domain = address.rsplit("@", 1)[1].strip().rstrip(".").lower()
    return any(domain == d or domain.endswith("." + d)
               for d in _CODE_SENDERS.get(service, ()))


def _on_code_page(url: str, service: str) -> bool:
    """Is this page the service's own sign-in, where its code belongs?"""
    from .webagent import _on_site

    return any(_on_site(url or "", site) for site in _CODE_PAGES.get(service, ()))


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

    from .webagent import BrowserUnavailable, _host, _Session, _first_visible, _never_touch

    def job(page):
        refusal = _never_touch(page.url, "type a code")
        if refusal:
            return "REFUSED:" + refusal
        if not _on_code_page(page.url, key):
            return "ELSEWHERE:" + (_host(page.url or "") or "this page")
        box = _first_visible(page, _OTP_FIELD, timeout=5.0)
        if box is None:
            return "NOBOX"
        try:
            box.click()
            # AT THE TYPING: finding the box waited up to five seconds and
            # the click itself can navigate.
            if not _on_code_page(page.url, key):
                return "ELSEWHERE:" + (_host(page.url or "") or "this page")
            box.fill(code)
            return "OK"
        except Exception as exc:  # noqa: BLE001
            return f"ERR:{type(exc).__name__}"

    # tab="same": the code belongs to the sign-in he is in the middle of. If
    # that tab was closed, the session would hand this job whichever tab is
    # still open, and a login code would be typed into a site nobody chose.
    try:
        outcome = _Session.get().do(job, timeout=60, tab="same")
    except BrowserUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"The browser failed: {type(exc).__name__}: {exc}"

    if outcome.startswith("REFUSED:"):
        return outcome[8:]
    if outcome.startswith("ELSEWHERE:"):
        return (f"This page is on {outcome[10:]}, not {key}'s own sign-in, so I "
                f"didn't type your code into it. A {key} code only goes into "
                f"{key}'s sign-in page - if a site you didn't expect is asking "
                f"for it, that's the classic way codes get stolen.")
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
