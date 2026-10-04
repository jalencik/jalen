"""
Regression test for HANDOFFPROMPT §3.

The bug: app.py used to call asyncio.run(self.handle_with_brain(text)) on
every turn. Each call created and destroyed its own event loop, but Brain's
ClaudeSDKClient is a long-lived object created inside whichever loop was
running the first time handle_with_brain() ran. From the second turn
onward, that client was bound to a loop that no longer existed.

The fix: Jarvis now runs one persistent event loop on a dedicated thread for
the whole process lifetime (Jarvis._start_loop) and submits every async call
to it with asyncio.run_coroutine_threadsafe (Jarvis._run_coro), instead of
asyncio.run(). This drives two consecutive brain turns through the real
Jarvis.process()/_run_coro()/handle_with_brain() path and asserts the
second one succeeds exactly like the first.

We substitute jalen.brain.agent.Brain with a fake that reproduces the
precise shape of the hazard — a resource that only works when start() and
later calls run on the same loop — rather than driving the real Claude
Agent SDK, which needs live `claude setup-token` auth (Phase B, not this
bug fix) and would make this test flaky/networked for no extra coverage of
the thing actually being regression-tested: loop lifecycle, not Claude's
answers.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.app import Jalen  # noqa: E402


class _LoopBoundFakeBrain:
    """
    Stands in for jalen.brain.agent.Brain. start() records which event loop
    it ran on — exactly like ClaudeSDKClient binding its internal transport
    to the loop it was opened in. ask() raises if it's ever invoked from a
    different loop. Under the old asyncio.run()-per-turn code, turn 2 runs on
    a fresh loop while turn 1's loop is already closed, so this fires. On the
    persistent loop both turns now share, it never does.
    """

    def __init__(self, cfg, safety, audit, *, confirm, announce, tools=None):
        self._loop = None
        self.turns = 0

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def ask(self, text: str, on_text=None) -> str:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError(
                "Brain used from a different event loop than it started on "
                "— this is exactly the §3 bug."
            )
        self.turns += 1
        reply = f"reply {self.turns}"
        # Mirror the real Brain: when a streaming callback is supplied, the
        # text is delivered through it as well as returned.
        if on_text is not None:
            on_text(reply)
        return reply

    async def stop(self) -> None:
        pass


@pytest.fixture
def jalen(monkeypatch):
    monkeypatch.setattr("jalen.brain.agent.Brain", _LoopBoundFakeBrain)
    return Jalen()


def test_two_consecutive_brain_turns_both_succeed(jalen):
    """The exact scenario from §3: drive two turns, the second must succeed."""
    try:
        first = jalen._run_coro(jalen.handle_with_brain("what's the weather"))
        second = jalen._run_coro(jalen.handle_with_brain("and tomorrow"))
    finally:
        jalen.shutdown()

    assert first == "reply 1"
    assert second == "reply 2"


def test_persistent_loop_is_reused_across_calls(jalen):
    """The loop itself — not just the outcome — must be the same object."""
    async def _current_loop():
        return asyncio.get_running_loop()

    try:
        loop_during_first = jalen._run_coro(_current_loop())
        loop_during_second = jalen._run_coro(_current_loop())
    finally:
        jalen.shutdown()

    assert loop_during_first is loop_during_second
    assert loop_during_first is not None


def test_shutdown_stops_the_loop_and_is_idempotent(jalen):
    """shutdown() must tear the loop down cleanly and tolerate being called twice."""
    jalen._run_coro(jalen.handle_with_brain("hello"))
    jalen.shutdown()
    assert jalen._loop is None

    jalen.shutdown()  # must not raise
