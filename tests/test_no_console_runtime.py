"""
Jalen runs under pythonw. Everything must work there, not just in a terminal.

THE BUG THIS FILE EXISTS FOR
----------------------------
He asked, four times, for a task to be handed to ChatGPT. Four times he was
told the browser layer was down:

    "the browser automation itself won't launch, it's not me being stubborn.
     That's a broken Playwright environment on this machine."

It was not a broken Playwright environment. `crashlog._StderrToCrashLog` —
the stand-in for the stderr that does not exist under pythonw — implemented
`fileno()` as:

    raise OSError("crash-log stderr has no file descriptor")

on the reasoning that an honest refusal beats an AttributeError. Honest, and
it broke browser delegation completely: Playwright launches its driver as a
SUBPROCESS, and `subprocess.Popen` asks a stream for `.fileno()` so the child
can inherit it.

WHY NOTHING CAUGHT IT
---------------------
It only happens when `sys.stderr` IS the wrapper, and `sys.stderr` is only
the wrapper when there is no console — which is to say, only when HE runs it.
Every test, and every probe I ran by hand, had a real terminal stderr with a
real descriptor. The environment difference was the bug.

So these tests do the one thing the whole suite was not doing: they put the
process into the state his machine is actually in, and then use it.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from jalen import crashlog


@pytest.fixture()
def no_console(monkeypatch):
    """sys.stderr as pythonw leaves it: the crash-log wrapper, no console."""
    wrapper = crashlog._StderrToCrashLog()
    monkeypatch.setattr(sys, "stderr", wrapper)
    yield wrapper


def test_the_stand_in_stderr_has_a_real_descriptor(no_console):
    """
    Something WILL ask for one. It used to raise, and the raise surfaced to
    him as "the browser automation layer is down".
    """
    fd = no_console.fileno()
    assert isinstance(fd, int) and fd >= 0


def test_a_subprocess_can_inherit_it(no_console):
    """
    The actual mechanism, exercised rather than described. This is what
    Playwright does to start its driver, and it is where it failed.
    """
    completed = subprocess.run(
        [sys.executable, "-c", "print('child ran')"],
        stderr=no_console, stdout=subprocess.PIPE, timeout=60,
    )
    assert completed.returncode == 0
    assert b"child ran" in completed.stdout


def test_it_still_captures_what_is_written_to_it(no_console, tmp_path, monkeypatch):
    """
    The descriptor must not have cost the class its actual job. A traceback
    written here still has to reach the crash log.
    """
    log = tmp_path / "crash.log"
    monkeypatch.setattr(crashlog, "CRASH_LOG", log)
    wrapper = crashlog._StderrToCrashLog()
    wrapper.write("Traceback (most recent call last):\n")
    wrapper.write("ValueError: something went wrong\n")
    wrapper.flush()
    body = log.read_text(encoding="utf-8", errors="replace")
    assert "something went wrong" in body


def test_the_descriptor_points_at_the_crash_log(tmp_path, monkeypatch):
    """
    Where a child's stderr lands matters. It should land where this class was
    always trying to put things, not into a void.
    """
    log = tmp_path / "crash.log"
    monkeypatch.setattr(crashlog, "CRASH_LOG", log)
    wrapper = crashlog._StderrToCrashLog()
    subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.stderr.write('child stderr line\\n')"],
        stderr=wrapper, timeout=60,
    )
    body = log.read_text(encoding="utf-8", errors="replace")
    assert "child stderr line" in body, (
        "a subprocess's stderr vanished - the descriptor is not the crash log"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="needs a real browser")
def test_the_browser_launches_with_no_console(no_console):
    """
    THE REGRESSION TEST. Not a mock: a real Chrome, started while sys.stderr
    is the wrapper, which is the exact state his machine is in.

    Verified in both directions before being trusted - with the old raise
    restored this fails with the literal message he was shown:

        BrowserUnavailable: Playwright wouldn't start:
        OSError: crash-log stderr has no file descriptor
    """
    pytest.importorskip("playwright.sync_api")
    from jalen.tools import webagent as wa

    session = wa._Session.get()
    try:
        session.start()
        title = session.do(
            lambda page: (page.goto("about:blank"), page.title())[-1])
        assert title is not None
    except wa.BrowserUnavailable as exc:
        if "file descriptor" in str(exc):
            pytest.fail(
                "the crash-log stderr has lost its descriptor again - browser "
                "delegation is broken for him and works for everyone testing "
                "it from a terminal"
            )
        pytest.skip(f"browser not launchable here: {exc}")
    finally:
        try:
            session.stop()
        except Exception:
            pass
