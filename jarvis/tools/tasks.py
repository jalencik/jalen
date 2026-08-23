r"""
Answering "what are you doing?"

    "it should work without becoming idle and asking the question on the way
     while it is doind its work"
    "a task must not randomly disappear"

Before this there was no way to ask. The only evidence that a turn was still
working was the orb being yellow, which is not an answer to "how far are
you" — and a task that failed inside its own thread left nothing behind at
all.

WHY THIS IS NOT IN selfcontrol.py
---------------------------------
It was, briefly, and a test caught it: `selfcontrol` is enumerated and
asserted to be read-only with respect to Jalen's own SOURCE. `cancel_task`
changes task state, which is a different boundary — the test's own docstring
says "a new tool added here without thinking about the boundary should fail
this rather than slip through", and that is exactly what happened.

The state itself lives in jarvis/conversation.py. This is only the door.
"""
from __future__ import annotations

from typing import Any


def what_are_you_doing(text: str = "") -> str:
    """Everything currently running, and how far along — GREEN."""
    from .. import conversation

    return conversation.describe_progress(text)


def cancel_task(text: str = "") -> str:
    """
    Stop a running task — GREEN.

    Asks WHICH when more than one is live rather than guessing. Guessing
    between two tasks is how "cancel that" cancels the wrong one, and his
    specification is explicit about it.
    """
    from .. import conversation

    chosen = conversation.which_task(text)
    if isinstance(chosen, str):
        return chosen
    conversation.update_task(chosen.id, state=conversation.CANCELLED)
    return f"Stopped {chosen.objective}."


REGISTRY: dict[str, Any] = {
    "what_are_you_doing": what_are_you_doing,
    "cancel_task": cancel_task,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
