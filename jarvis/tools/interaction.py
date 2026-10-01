"""
Asking him something mid-task, and waiting.

His requirement, in his own words: "if something it needs it should be able
to ask that thing from me, so that I will answer for example my phone or
whatever, and after asking the question, it might take some time, and it
should not execute anything until I give my answer to it."

WHY THIS IS A BRIDGE AND NOT AN IMPLEMENTATION
----------------------------------------------
Actually asking means speaking through the live Speaker, opening the
microphone's listening window, and blocking the turn — all of which belong
to the running Jalen instance, not to a stateless tool module. So app.py
installs a callback here at startup and this module is the door the brain
knocks on.

The alternative, handing the whole app object to the tool layer, would let
any tool reach into any part of the assistant. This exposes exactly one
capability.

WHEN NOBODY IS LISTENING
------------------------
In text mode, in the Telegram bot, or in a test, there is no voice loop to
ask through. The tool says so plainly rather than pretending it asked and
returning an empty answer — a caller that believes it asked and got silence
will carry on with a blank field, which is the failure this whole mechanism
exists to prevent.
"""
from __future__ import annotations

from typing import Any, Callable, NamedTuple

# Installed by Jalen.__init__. Signature: (question, timeout_s) -> answer.
_ASK: Callable[[str, float], str] | None = None

# Installed beside it. Signature: (question) -> app.ConfirmAnswer, which is
# truthy ONLY on a real spoken yes. See confirm() below.
_CONFIRM: Callable[[str], Any] | None = None


class Answer(NamedTuple):
    """His yes or no, as the code that asked sees it."""

    yes: bool
    outcome: str        # yes | no | timeout | correction | unavailable | failed
    words: str = ""

# Long by design. A yes/no gets 20 seconds; looking up a passport number or
# a referee's email address does not, and a form filled with a guess is
# worse than one that waited.
DEFAULT_TIMEOUT_S = 180.0


def install(ask: Callable[[str, float], str]) -> None:
    """Called once by the app so the brain can reach the voice loop."""
    global _ASK
    _ASK = ask


def install_confirm(confirm_fn: Callable[[str], Any] | None) -> None:
    """Called once by the app: Jalen.confirm, the one a RED action uses."""
    global _CONFIRM
    _CONFIRM = confirm_fn


def uninstall() -> None:
    global _ASK, _CONFIRM
    _ASK = None
    _CONFIRM = None


def confirm(question: str) -> Answer:
    """
    Ask HIM a yes/no question and wait - for CODE, never for the model.

    Deliberately absent from REGISTRY. The point is that the question is
    written by the code that is about to act, naming what IT read - "type
    your gmail into accounts-google.evil.example?" - so his answer is to the
    thing that will actually happen. A flag the model sets after asking
    something in its own words is not that; it is how fill_credential's
    approved_once typed into a page nobody named.

    Not a second confirmation mechanism: this is Jalen.confirm(), the one a
    RED action gets - the echo defence that stops "...Confirm?" coming back
    through the speakers from approving itself, the correction handling, the
    timeout. Only a real yes counts. A string, a correction, a timeout, or
    nobody to ask are all no, and the caller must treat them as "do nothing".
    """
    text = (question or "").strip()
    if not text:
        return Answer(False, "failed")
    if _CONFIRM is None:
        return Answer(False, "unavailable")
    try:
        answer = _CONFIRM(text)
    except Exception:  # noqa: BLE001 - failing to ask is a no, never a crash
        return Answer(False, "failed")
    if answer is True:
        return Answer(True, "yes")
    outcome = getattr(answer, "outcome", None)
    words = str(getattr(answer, "words", "") or "")
    if outcome == "yes" and bool(answer):
        return Answer(True, "yes", words)
    return Answer(False, str(outcome or "no"), words)


def ask_user(question: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
    """
    Ask him something and WAIT. Blocks until he answers or the time runs out.

    Returns his answer, or a plain statement that nothing came back. The
    caller must treat no-answer as "stop", never as "carry on without it" —
    that distinction is the entire point of the tool.
    """
    text = (question or "").strip()
    if not text:
        return "I need an actual question to ask."
    if _ASK is None:
        return (
            "I can't ask you anything right now — there's no voice session "
            "running. Nothing was filled in or sent."
        )
    try:
        answer = _ASK(text, float(timeout_s))
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't ask you that ({type(exc).__name__}). Nothing was done."
    if not answer:
        return (
            "No answer came back, so I've stopped rather than guessing. "
            "Nothing was filled in or sent."
        )
    return f"He said: {answer}"


REGISTRY: dict[str, Any] = {
    "ask_user": ask_user,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
