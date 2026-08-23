"""
Start the orb. An actual one, on a real Tk root.

WHY THIS FILE HAD TO EXIST
--------------------------
Removing the resize methods also removed the `@staticmethod` decorator on the
line above `_clamp_size`, which turned it into an instance method. The very
first thing `_run` does is call it, so the orb died on startup, every time,
with `TypeError: _clamp_size() takes 2 positional arguments but 3 were given`.

**2,886 tests passed.** Not one of them started an orb. Everything about it
was tested by reading its source or calling its methods in isolation — which
proves the pieces are the right shape and proves nothing about whether the
thing runs.

It was found by a throwaway probe script, by accident, while looking at
something else. That is not a process.

The orb runs on a daemon thread, so its exceptions print a traceback and
vanish; nothing propagates to whoever started it. So this file installs an
excepthook and fails on anything that thread raises.

Kept deliberately small and slow-ish: it opens a real window. One test that
runs the real thing beats twenty that read its source.
"""
from __future__ import annotations

import sys
import threading
import time

import pytest

from jarvis.config import CONFIG
from jarvis.ui.orb import Orb, TranscriptWindow

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="opens a real Tk window"
)


@pytest.fixture()
def caught():
    """Anything raised on any thread, since the orb's own thread swallows it."""
    failures: list[str] = []
    previous = threading.excepthook

    def hook(args):
        failures.append(f"{args.exc_type.__name__}: {args.exc_value}")

    threading.excepthook = hook
    yield failures
    threading.excepthook = previous


def test_the_orb_starts_and_survives_every_state(caught):
    """
    The test that was missing. Starts a real orb, walks it through every
    state it can be in, and fails on anything its thread raised.
    """
    orb = Orb(CONFIG)
    orb.start()
    try:
        time.sleep(2.5)                      # let Tk realize the window
        assert not caught, f"the orb died on startup: {caught}"

        for state in ("idle", "listening", "thinking", "speaking",
                      "muted", "blocked", "idle"):
            orb.set_state(state)
            orb.set_level(0.4)
            time.sleep(0.35)
            assert not caught, f"the orb died in state {state!r}: {caught}"

        assert orb._hwnd, (
            "the window never resolved a top-level handle - it is not "
            "actually on screen"
        )
    finally:
        orb.stop()
        time.sleep(0.5)
    assert not caught, f"the orb died on shutdown: {caught}"


def test_the_transcript_window_appears_alongside_the_orb(caught):
    """
    Both own a Tk root, on different threads, in one interpreter — which is
    the arrangement most likely to break. He reported the on-screen text not
    arriving; this is the only way to know whether it does.
    """
    orb = Orb(CONFIG)
    orb.start()
    transcript = TranscriptWindow(CONFIG)
    try:
        time.sleep(2.0)
        body = "\n".join(f"{i}. a line of the answer" for i in range(1, 30))
        for n in range(3):
            transcript.show(f"Full answer {n}", body)
            time.sleep(1.2)
        assert transcript.shown_count == 3, (
            f"only {transcript.shown_count} of 3 answers reached the screen"
        )
        assert not caught, f"something raised: {caught}"
    finally:
        orb.stop()
        time.sleep(0.5)
