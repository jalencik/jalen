"""
Long writing, and a lot of it at once.

Two things he asked for that nothing else covered.

ESSAYS NEED SOMEWHERE TO GO. "Write essays for me using my voice skill" —
voice_guide tells the model HOW to write as him, and then the essay existed
only inside a turn. Speaking a 600-word personal statement aloud is absurd,
and a reply that long gets truncated anyway. It goes to a file he can open
and to the clipboard he can paste from.

FIFTY POSTS IS ONE CALL, NOT FIFTY. "It should be able to handle that kinda
combo and complex tasks like he owns it" — writing fifty posts and sending
each through its own tool call would exhaust the turn budget somewhere
around the eighth. This takes a list.

WHY EACH POST IS REPORTED INDIVIDUALLY
--------------------------------------
"Sent 50 posts" is not a report, it is a hope. If eleven of them failed
because a chat name did not resolve, he needs to know which eleven — and
the failure mode this project keeps producing is precisely the summary that
rounds a partial success up to a whole one. Every item gets its own line.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DRAFTS_DIR = ROOT / "data" / "drafts"

# Beyond this, one call is doing too much to report on usefully — and a
# mistake repeated fifty times is worse than a mistake made once.
MAX_BATCH = 50


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "draft")).strip("-").lower()
    return (cleaned or "draft")[:60]


def save_draft_text(name: str, text: str) -> str:
    """
    Write a long piece — an essay, a post, an application answer — to a file
    and the clipboard. GREEN.

    Timestamped rather than overwritten. He iterates on these, and a second
    attempt silently replacing the first is how good drafts disappear.
    """
    body = (text or "").strip()
    if not body:
        return "There's nothing to save."

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = DRAFTS_DIR / f"{stamp}-{_slug(name)}.md"
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        return f"I couldn't write the draft ({type(exc).__name__}: {exc})."

    from .handoff import set_clipboard

    on_clipboard = set_clipboard(body)
    words = len(body.split())
    reply = f"Saved {words} words to {path.name}."
    reply += (
        " It's on your clipboard too — paste it straight into the form."
        if on_clipboard else
        " I couldn't reach the clipboard, so open the file to copy it."
    )
    return reply + f"\n[full path: {path}]"


def list_drafts(limit: int = 10) -> str:
    """The drafts written so far, newest first — GREEN."""
    if not DRAFTS_DIR.exists():
        return "No drafts yet."
    files = sorted(DRAFTS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return "No drafts yet."
    lines = [f"{len(files)} draft(s), newest first:"]
    for path in files[:limit]:
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d %b %H:%M")
        words = len(path.read_text(encoding="utf-8", errors="replace").split())
        lines.append(f"  - {path.name}  ({words} words, {when})")
    if len(files) > limit:
        lines.append(f"  ...and {len(files) - limit} older.")
    return "\n".join(lines)


def send_posts(to: str, posts: list, as_draft: bool = False) -> str:
    """
    Send (or draft) SEVERAL posts to one Telegram chat in one call.

    Each one is reported separately. The safety gate still applies to the
    destination exactly as it would for a single message — this batches the
    work, not the permission.
    """
    if not isinstance(posts, list):
        return "posts has to be a list of strings, one per post."
    items = [str(p).strip() for p in posts if str(p).strip()]
    if not items:
        return "No posts to send."
    if len(items) > MAX_BATCH:
        return (
            f"{len(items)} posts is more than I'll do in one go (max {MAX_BATCH}). "
            "Split it — a mistake repeated fifty times is worse than one mistake."
        )

    from .messaging import save_telegram_draft, send_telegram_message

    action = save_telegram_draft if as_draft else send_telegram_message

    # A draft is stored ONE PER CHAT by Telegram, so a batch of drafts to the
    # same chat would leave only the last. Saying so beats appearing to save
    # fifty and leaving one.
    if as_draft and len(items) > 1:
        return (
            f"Telegram keeps one draft per chat, so saving {len(items)} drafts to "
            f"{to} would leave only the last one. Send them, or save them as "
            "files with save_draft_text and post them yourself."
        )

    done, failed, unconfirmed = [], [], []
    for index, post in enumerate(items, start=1):
        try:
            result = action(to=to, text=post)
        except Exception as exc:  # noqa: BLE001
            line = f"  {index}. {type(exc).__name__}: {exc}"
            (failed if _never_sent(exc) else unconfirmed).append(line)
            continue
        outcome = _outcome(result)
        if outcome == "sent":
            first_line = post.splitlines()[0][:60]
            done.append(f"  {index}. {first_line}")
        elif outcome == "failed":
            failed.append(f"  {index}. {result}")
        else:
            unconfirmed.append(f"  {index}. {result}")

    lines = [f"{len(done)} of {len(items)} sent to {to}."]
    if done:
        lines += ["Sent:"] + done
    if failed:
        # Never rounded away. "Sent 50 posts" when eleven failed is the
        # silent-omission failure in its purest form.
        lines += [f"FAILED ({len(failed)}):"] + failed
    if unconfirmed:
        # Neither rounded up nor down. FAILED means "send it again", and for
        # a post that may already be in the channel that is a double post.
        lines += [
            f"NOT CONFIRMED ({len(unconfirmed)}) — these may have gone out; "
            f"check {to} before sending any of them again:"
        ] + unconfirmed
    return "\n".join(lines)


# HOW A REPLY SAYS WHAT HAPPENED: by its OPENING words - the sentence Jalen
# writes - never by a word anywhere in it. A delivered reply quotes the post,
# so the first version, which searched the whole reply for "couldn't", "could
# not" and "nothing sent", counted a post that said "couldn't" as FAILED after
# it had gone out, and a failed post is one he sends again. messaging and
# attachments open every delivered reply with "Sent" (drafts: "Saved"), and
# every reply where nothing went with one of _NOT_DELIVERED. Anything else is
# not a reply either module writes, so it is reported unconfirmed, not guessed.
_DELIVERED = ("Sent", "Saved")
_NOT_DELIVERED = ("Nothing sent", "Nothing saved", "I couldn't", "Couldn't")


def _outcome(reply: Any) -> str:
    """'sent', 'failed' or 'unconfirmed', from how the reply begins."""
    opening = str(reply or "").lstrip()
    if opening.startswith(_DELIVERED):
        return "sent"
    if opening.startswith(_NOT_DELIVERED):
        return "failed"
    return "unconfirmed"


def _never_sent(exc: Exception) -> bool:
    """
    An exception that proves nothing went out: no Telegram session, or
    Telegram refusing the request (a 400). Anything else - a timeout, a
    dropped connection - may have delivered first.
    """
    from ..integrations.telegram_user import TelegramNotConnected
    from .messaging import _rejected

    return isinstance(exc, TelegramNotConnected) or _rejected(exc)


REGISTRY: dict[str, Any] = {
    "save_draft_text": save_draft_text,
    "list_drafts": list_drafts,
    "send_posts": send_posts,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
