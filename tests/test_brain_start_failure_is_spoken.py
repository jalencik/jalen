"""
A brain that cannot START must say so. Silence is the worst possible answer.

The live incident, 20 September 2026: upgrading claude-agent-sdk to 0.2.157
installed a source-built wheel with no bundled claude.exe in it (152 KB rather
than the ~105 MB platform wheel). The SDK then fell through to PATH, found
npm's claude.CMD, and refused it -- Windows batch scripts are not spawnable.
Brain.start() raised CLIConnectionError.

What the user would have experienced: nothing at all. handle_with_brain()
called _start_brain() OUTSIDE its own try block, so the exception flew past
the handler that turns a brain failure into a spoken sentence, past process(),
and into dispatch_turn -- whose handler records the task as FAILED, re-raises
into a daemon thread nobody joins, and speaks nothing. The traceback reaches
data/crash.log and the room stays quiet.

That is exactly the complaint this repo's owner reported: "I tell it my task
and it becomes silent rather than executing it."

A failure to start is also a DIFFERENT KIND of failure from one mid-answer.
Mid-answer failures are usually transient -- a dropped CLI connection, worth
"say that again". A failure to start is configuration: the binary is missing,
or unauthenticated, or unspawnable. Saying "try again" to that invites an
infinite loop of asking, so it gets its own sentence pointing at the
diagnostic instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.app import Jalen  # noqa: E402


class _BrainThatCannotStart:
    """
    Stands in for jarvis.brain.agent.Brain with the exact failure the SDK
    produced: start() raises, so no client ever exists. ask() asserts rather
    than returning, because reaching it would mean the code carried on with a
    brain it never managed to build.
    """

    attempts = 0

    def __init__(self, cfg, safety, audit, *, confirm, announce, tools=None):
        pass

    async def start(self) -> None:
        type(self).attempts += 1
        raise RuntimeError(
            "Refusing to execute batch script "
            "'C:/Users/user/AppData/Roaming/npm/claude.CMD'"
        )

    async def ask(self, text: str, on_text=None) -> str:
        raise AssertionError("ask() reached despite start() having failed")

    async def stop(self) -> None:
        return None


@pytest.fixture
def jarvis(monkeypatch):
    monkeypatch.setattr("jarvis.brain.agent.Brain", _BrainThatCannotStart)
    _BrainThatCannotStart.attempts = 0
    app = Jalen()
    yield app
    app.shutdown()


def test_a_brain_that_cannot_start_answers_in_words(jarvis):
    """The whole point. Before the fix this call RAISED, and the exception
    ended up in a daemon thread that speaks nothing -- so the turn produced
    total silence with no error anywhere he could hear."""
    reply = jarvis._run_coro(jarvis.handle_with_brain("open chrome"))

    assert isinstance(reply, str)
    assert reply.strip(), "an empty string is still silence"


def test_the_sentence_says_it_could_not_start_and_where_to_look(jarvis):
    """A start failure is configuration, not bad luck. Telling him to repeat
    himself would loop forever against a missing binary, so the sentence has
    to distinguish itself from the mid-answer 'try again' case and point at
    the diagnostic."""
    reply = jarvis._run_coro(jarvis.handle_with_brain("open chrome")).lower()

    assert "start" in reply
    assert "check" in reply, "must point at a diagnostic he can actually run"


def test_the_next_turn_tries_to_start_again(jarvis):
    """The failure must not poison state. A genuinely transient cause -- the
    CLI still unpacking, a slow first launch -- has to get another chance on
    the next thing he says, rather than one bad turn disabling the brain for
    the rest of the session."""
    jarvis._run_coro(jarvis.handle_with_brain("open chrome"))
    second = jarvis._run_coro(jarvis.handle_with_brain("open chrome"))

    assert _BrainThatCannotStart.attempts == 2
    assert isinstance(second, str) and second.strip()


def test_no_half_built_brain_is_left_behind(jarvis):
    """self.brain must stay None, or the next turn calls ask() on an object
    whose start() never completed -- which is how a clear configuration error
    turns into a confusing one several turns later."""
    jarvis._run_coro(jarvis.handle_with_brain("open chrome"))

    assert jarvis.brain is None


def test_the_failure_is_written_to_the_audit_trail(jarvise_audit_spy):
    """He hears one sentence; the detail has to survive somewhere. Without
    this the only record was a traceback in a thread that died."""
    jarvis, errors = jarvise_audit_spy

    jarvis._run_coro(jarvis.handle_with_brain("open chrome"))

    assert errors, "a brain that could not start recorded nothing"


@pytest.fixture
def jarvise_audit_spy(monkeypatch):
    monkeypatch.setattr("jarvis.brain.agent.Brain", _BrainThatCannotStart)
    _BrainThatCannotStart.attempts = 0
    app = Jalen()
    errors: list = []
    original = app.audit.error
    monkeypatch.setattr(
        app.audit,
        "error",
        lambda where, exc, *a, **k: (errors.append((where, exc)), original(where, exc, *a, **k))[-1],
    )
    yield app, errors
    app.shutdown()
