r"""
Remembering that Jalen has read something somebody else wrote.

THE HOLE THIS FILLS, AND HOW LONG IT WAS OPEN
---------------------------------------------
`SafetyEngine.classify(tool, args, origin="content")` refuses RED and AMBER
tools outright. That check is correct, it is tested, and three modules'
docstrings cite it as "the hard protection" behind their untrusted-content
fences.

It had never once run in production.

The only caller that could set it was `jarvis/brain/agent.py`:

    origin = "content" if input_data.get("_from_content") else "user"

and nothing anywhere set `_from_content` — except a test, which injected the
flag into the payload itself before calling the hook. So the test proved the
classifier does the right thing *when told*, and could not prove that anything
ever told it. Nothing did. Every tool call in every real session classified as
`origin="user"`.

Found by an adversarial review, not by the suite, and worth stating plainly:
2,907 passing tests, and the single most important security control in the
project was unreachable code.

WHAT THIS DOES INSTEAD
----------------------
Untrusted text enters through a small number of doors — an email body, a
Telegram message, a web page, another model's answer. Each of those already
wraps what it returns in an UNTRUSTED CONTENT fence. So the fence is the
natural place to also raise a flag, and the PreToolUse hook is the natural
place to read it.

    gmail._fence(...)  ->  taint.mark("email from x@y.com")
                              |
                       every later tool call this turn
                              |
                       classify(..., origin="content")  ->  RED/AMBER refused

DELIBERATELY A PROCESS-WIDE FLAG, NOT A THREAD-LOCAL
-----------------------------------------------------
The brain runs each tool on whichever pool thread is free, so a thread-local
set inside `read_email` would be invisible to the `send_email` call that
follows it. A contextvar has the same problem across `asyncio.to_thread`.

The cost of a process-wide flag is that two overlapping turns can taint each
other. That is over-blocking: Jalen asks for a confirmation it might not have
needed. The failure mode in the other direction is an email talking Jalen into
sending mail on its author's behalf. Those are not comparable, so the flag is
sticky and shared, and it clears when a NEW utterance from him begins.

`he_asked_again()` is what clears it, and it is called from exactly one place —
the top of `process()`, the moment a real utterance from him arrives. That is
the only event that can honestly mean "this is his instruction, not a page's".
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()

# What was read, and when. Empty means nothing untrusted has been seen since
# he last spoke.
_SOURCES: list[tuple[float, str]] = []

# How long a taint lasts even if nothing clears it. A backstop, not the
# mechanism: the real clear is him speaking again. It exists so a crashed turn
# cannot leave the process permanently refusing to send an email.
MAX_AGE_S = 600.0


def mark(source: str) -> None:
    """
    Untrusted text just entered the turn. Called by the content fences.

    `source` is kept for the audit line - "refused because you were reading an
    email from x@y.com" is a sentence he can act on; "refused: content" is not.
    """
    with _LOCK:
        _SOURCES.append((time.monotonic(), str(source)[:120]))
        if len(_SOURCES) > 20:
            del _SOURCES[:-20]


def he_asked_again() -> None:
    """
    A fresh utterance from HIM. Everything read before it is no longer in play.

    Called from exactly one place, the top of process(). Widening that list is
    how this control quietly dies again: any other caller would be asserting
    "this is his instruction" about something that is not.
    """
    with _LOCK:
        _SOURCES.clear()


def is_tainted() -> bool:
    """Has Jalen read somebody else's text since he last spoke?"""
    with _LOCK:
        cutoff = time.monotonic() - MAX_AGE_S
        while _SOURCES and _SOURCES[0][0] < cutoff:
            _SOURCES.pop(0)
        return bool(_SOURCES)


def why() -> str:
    """What was read, for the audit line and for telling him."""
    with _LOCK:
        if not _SOURCES:
            return ""
        return ", ".join(dict.fromkeys(source for _at, source in _SOURCES))[:200]


def origin_now() -> str:
    """
    "content" or "user" — what SafetyEngine.classify should be told.

    One function so there is one answer, and so the next person wiring a new
    entry point does not have to rediscover the rule.
    """
    return "content" if is_tainted() else "user"
