"""
Sending a file — to Telegram, or attached to an email draft.

His words: Jalen should be "professicient in copy some files from desktop or
the whole system itself and could be able to paste that into my telegram and
my gmail inbox and send it as well".

RESOLVING THE FILE IS HALF THE JOB
----------------------------------
He does not say "C:\\Users\\user\\Desktop\\Changes.pdf". He says "my CV" or
"the changes pdf". Every path here goes through launcher.find_files — the
same search open_target already uses — so the names he actually says
resolve, and a name that matches nothing is reported as not-found rather
than sent as a literal string.

TWO REFUSALS, BOTH DELIBERATE
-----------------------------
A file that does not exist is never "sent" — obvious, and worth stating
because a send that silently succeeds with nothing attached is the exact
shape of failure this project keeps producing.

A file matching safety.yaml's never_touch list is refused outright, before
any size check or upload. Those patterns exist to keep session files, tokens
and password stores away from the file tools, and an attachment is the
easiest possible way to exfiltrate one: "send my session file to this chat"
is a single sentence.

SIZE IS CHECKED FIRST, NOT DISCOVERED
-------------------------------------
Telegram rejects over 2GB and Gmail over 25MB. Finding that out from a
failed upload after ninety seconds is worse than saying so immediately.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from ..config import CONFIG
from ..safety import SafetyEngine

# The canonical gate, not a second implementation.
#
# The obvious call here was filesystem._is_under_never_touch, and it was
# wrong twice: it takes two arguments (so the first version raised
# TypeError instead of refusing — the protection CRASHED rather than
# working), and it only checks never_touch DIRECTORIES. The filename
# PATTERNS — "*.session", "*token*" — live in SafetyEngine, which is where
# the whole tier system already looks. A second implementation of "is this
# protected" is how the two drift apart and one of them stops matching.
_SAFETY = SafetyEngine(CONFIG)


def _is_protected(path: Path) -> str | None:
    """
    The reason this file may not be sent, or None. Asks the never-touch
    question directly: it used to go through classify() with the default
    origin, which CLAUDE.md lists as a hardcoded-origin call site - and
    WHERE the request came from is not part of "is this file protected".
    """
    return _SAFETY.protected_path(path)

# Telegram's own limit for a normal account is 2GB. Gmail's is 25MB for the
# whole encoded message, and base64 inflates by a third, so the real ceiling
# for a file is around 18MB.
TELEGRAM_MAX_BYTES = 2 * 1024**3
GMAIL_MAX_BYTES = 18 * 1024**2


def _resolve(name: str) -> tuple[Path | None, str]:
    """
    Turn what he said into a real path. Returns (path, problem).

    Exactly one of the two is meaningful, and the problem string is written
    to be spoken aloud rather than logged.
    """
    raw = (name or "").strip().strip('"').strip("'")
    if not raw:
        return None, "Tell me which file."

    direct = Path(os.path.expandvars(os.path.expanduser(raw)))
    candidate = direct if direct.is_file() else None

    if candidate is None:
        from .launcher import find_files

        hits = [Path(h) for h in find_files(raw) if Path(h).is_file()]
        if not hits:
            return None, f"I couldn't find a file called {raw!r}."
        if len(hits) > 1:
            names = ", ".join(h.name for h in hits[:5])
            return None, (
                f"There's more than one file matching {raw!r}: {names}. "
                "Which one?"
            )
        candidate = hits[0]

    if reason := _is_protected(candidate):
        return None, (
            f"I won't send {candidate.name} — {reason}. That isn't something "
            "you can override by asking."
        )
    return candidate, ""


def _too_big(path: Path, limit: int, where: str) -> str:
    size = path.stat().st_size
    if size <= limit:
        return ""
    return (
        f"{path.name} is {size / 1024**2:.0f} megabytes and {where} caps out "
        f"at {limit / 1024**2:.0f}. Nothing was sent."
    )


def send_telegram_file(to: str, file: str, caption: str = "") -> str:
    """
    Send a file to a Telegram chat. RED unless the destination is
    pre-approved — same rule as a text message, and for the same reason:
    it reaches a person and cannot be recalled.
    """
    from .messaging import (
        PREMIUM_DROPPED, _Attempt, _chat_name, _deliver, _enabled, _formatted,
        _run_send, _resolve as _resolve_chat, _unresolved,
    )

    _enabled()
    path, problem = _resolve(file)
    if problem:
        return problem
    if oversized := _too_big(path, TELEGRAM_MAX_BYTES, "Telegram"):
        return oversized

    async def work(client):
        entity = await _resolve_chat(client, to)
        if entity is None:
            return await _unresolved(client, to, "nothing sent")
        # The caption goes through the same per-call decision as a message
        # (messaging._formatted): Telegram HTML when it is HTML, plain text
        # otherwise, and never Telethon's markdown default. parse_mode=None
        # matters here more than in send_message: send_file treats an EMPTY
        # entity list as "parse it yourself", so without it a plain caption
        # like "**v2** final" would lose its asterisks to markdown.
        post = _formatted(caption or "")
        if post.refusal:
            return f"Nothing sent — the caption: {post.refusal}."

        async def send(words, entities):
            await client.send_file(
                entity, str(path), caption=words or None,
                formatting_entities=entities or None, parse_mode=None,
            )

        # The same single plain retry as a message, on a refusal only: the
        # first version had none, so a caption Telegram would not format
        # raised past the tool instead of going out plain.
        attempted.set()
        sent, note = await _deliver(send, post)
        if sent is None:
            return f"Nothing sent — {note}."
        reply = (
            f"Sent {path.name} ({path.stat().st_size / 1024**2:.1f} MB) "
            f"to {await _chat_name(client, entity)}."
        )
        if note.startswith(PREMIUM_DROPPED):
            reply += f" The caption's premium emoji went as ordinary emoji — {note}."
        elif note:
            reply += f" The caption went WITHOUT formatting — {note}."
        return reply

    # A timeout must not read as a failure: see messaging._run_send.
    attempted = _Attempt()
    return _run_send(work, to, attempted)


def draft_email_with_file(to: str, subject: str, body: str, file: str) -> str:
    """
    Save a Gmail draft with a file attached. GREEN — nothing leaves the
    account until he sends it, which is his click.
    """
    from email.message import EmailMessage
    import mimetypes

    from .gmail import _enabled, gmail_service

    _enabled()
    path, problem = _resolve(file)
    if problem:
        return problem
    if oversized := _too_big(path, GMAIL_MAX_BYTES, "Gmail"):
        return oversized

    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    guessed, _encoding = mimetypes.guess_type(str(path))
    maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
    message.add_attachment(
        path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name
    )

    service = gmail_service()
    draft = (
        service.users()
        .drafts()
        .create(
            userId="me",
            body={"message": {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}},
        )
        .execute()
    )
    return (
        f"Draft saved to {to} with {path.name} attached — subject {subject!r}. "
        "Nothing has been sent; it's in your Gmail drafts. "
        f"[draft id: {draft.get('id')}]"
    )


REGISTRY: dict[str, Any] = {
    "send_telegram_file": send_telegram_file,
    "draft_email_with_file": draft_email_with_file,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
