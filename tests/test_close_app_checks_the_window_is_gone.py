"""
close_app said "Closed X." after sending Alt+F4, without looking.

Found by the live QA of 2026-10-01 ("close the calculator"): the reply was
"I can't find a window called the calculator" while the Calculator was open,
and data/audit.jsonl still filed the action as executed. Two separate faults:

  1. The window search kept the word "the" ("close the calculator" reaches the
     tool as name="the calculator"), so a window called Calculator never
     matched. "close calculator" worked, which is why it looked intermittent.
  2. Alt+F4 is a request, not a result. A window with unsaved work answers it
     with a "do you want to save?" box and stays open, and the reply was still
     "Closed X." - the lie this project's own comments on open_app already warn
     about ("claimed success while nothing opened").

And the audit: handle_local logs a tool "executed" whenever it returns without
raising, and these tools return errors as sentences, so every failed close was
logged as a close that happened.

WHAT IT DOES NOW
  - the window is looked up without a leading "the" / "my" / "a";
  - after Alt+F4 the window handle is checked; "Closed X." is said only once the
    window is gone (destroyed, or hidden - an app that closes to the tray);
  - a window that is still there is reported as still there, with the likely
    reason, and the result is marked as not having worked, which handle_local
    files as "failed".
"""
from __future__ import annotations

import re
import sys
import types

import pytest

from jarvis.tools import system


class _FakeWindow:
    def __init__(self, found=True, hwnd=4242):
        self._found = found
        self.NativeWindowHandle = hwnd
        self.keys: list[str] = []

    def Exists(self, *args, **kwargs):
        return self._found

    def SendKeys(self, keys, *args, **kwargs):
        self.keys.append(keys)


@pytest.fixture
def uia(monkeypatch):
    """A fake uiautomation module that records the pattern it was asked for."""
    seen = {}
    win = _FakeWindow()
    mod = types.ModuleType("uiautomation")

    def WindowControl(searchDepth=1, RegexName=None, **kw):
        seen["pattern"] = RegexName
        return win

    mod.WindowControl = WindowControl
    monkeypatch.setitem(sys.modules, "uiautomation", mod)
    seen["window"] = win
    return seen


def test_a_window_that_goes_away_is_reported_closed(uia, monkeypatch):
    monkeypatch.setattr(system, "_wait_until_gone", lambda hwnd, timeout_s=2.5: True)
    reply = system.close_app("calculator")
    assert reply == "Closed calculator."
    assert uia["window"].keys == ["{Alt}{F4}"]
    assert not getattr(reply, "failed", False)


def test_a_window_that_is_still_there_is_not_reported_closed(uia, monkeypatch):
    """THE LIE: Alt+F4 sent, a save prompt came up, the window stayed."""
    monkeypatch.setattr(system, "_wait_until_gone", lambda hwnd, timeout_s=2.5: False)
    reply = system.close_app("notepad")
    assert not reply.startswith("Closed")
    assert "still open" in reply
    assert "save" in reply.lower()                 # the likely reason, in his words
    assert getattr(reply, "failed", False) is True


def test_the_handle_checked_is_the_windows_own(uia, monkeypatch):
    """Not a fresh title search: with six Chrome windows open, closing one leaves
    five that still match the name, and the check would say it never closed."""
    uia["window"].NativeWindowHandle = 31337
    asked = []
    monkeypatch.setattr(system, "_wait_until_gone",
                        lambda hwnd, timeout_s=2.5: asked.append(hwnd) or True)
    system.close_app("chrome")
    assert asked == [31337]


def test_a_missing_window_is_a_failure_he_can_hear(uia):
    uia["window"]._found = False
    reply = system.close_app("calculator")
    assert reply == "I can't find a window called calculator."
    assert reply.failed is True


@pytest.mark.parametrize("spoken", ["the calculator", "my calculator", "a calculator",
                                    "The Calculator", "calculator"])
def test_a_leading_article_does_not_stop_the_window_being_found(uia, monkeypatch, spoken):
    monkeypatch.setattr(system, "_wait_until_gone", lambda hwnd, timeout_s=2.5: True)
    system.close_app(spoken)
    pattern = uia["pattern"]
    assert pattern.match("Calculator"), f"{spoken!r} must find a window titled Calculator"
    assert not pattern.match("Notepad")


def test_a_name_that_is_only_an_article_is_left_alone(uia, monkeypatch):
    monkeypatch.setattr(system, "_wait_until_gone", lambda hwnd, timeout_s=2.5: True)
    uia["window"]._found = False
    assert "called the" in system.close_app("the")


def test_the_failure_marker_is_a_plain_string():
    marked = system.DidNotWork("I can't find a window called x.")
    assert isinstance(marked, str)
    assert marked == "I can't find a window called x."
    assert f"{marked}" == "I can't find a window called x."
    assert re.sub("x", "y", marked) == "I can't find a window called y."
    assert str(marked) == marked and type(str(marked)) is str


def test_uiautomation_missing_is_a_sentence(monkeypatch):
    monkeypatch.setitem(sys.modules, "uiautomation", None)
    assert "isn't installed" in system.close_app("calculator")


# ---------------------------------------------------------------------------
# the window check itself (Win32 calls faked)
# ---------------------------------------------------------------------------
class _FakeUser32:
    def __init__(self, states):
        self._states = list(states)   # (is_window, is_visible) per poll, last repeats

    def _now(self):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]

    def IsWindow(self, hwnd):
        self._cur = self._now()
        return 1 if self._cur[0] else 0

    def IsWindowVisible(self, hwnd):
        return 1 if self._cur[1] else 0


def _with_user32(monkeypatch, states, clock=None):
    """Only this module's own view of ctypes and time is replaced, so nothing
    else running in the test process sees a fake clock."""
    fake_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(user32=_FakeUser32(states)))
    monkeypatch.setattr(system, "ctypes", fake_ctypes)
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system, "time", types.SimpleNamespace(
        monotonic=clock or (lambda: 0.0), sleep=lambda s: None))


def test_wait_until_gone_sees_a_destroyed_window(monkeypatch):
    _with_user32(monkeypatch, [(True, True), (True, True), (False, False)])
    assert system._wait_until_gone(1, timeout_s=5) is True


def test_wait_until_gone_counts_a_window_hidden_in_the_tray_as_gone(monkeypatch):
    """Telegram, Spotify and others close to the tray: window alive, not visible."""
    _with_user32(monkeypatch, [(True, True), (True, False)])
    assert system._wait_until_gone(1, timeout_s=5) is True


def test_wait_until_gone_gives_up_on_a_window_that_stays(monkeypatch):
    ticks = iter(range(0, 1000))
    _with_user32(monkeypatch, [(True, True)], clock=lambda: next(ticks) * 0.5)
    assert system._wait_until_gone(1, timeout_s=2.0) is False


# ---------------------------------------------------------------------------
# the audit: a failed close is not an executed one
# ---------------------------------------------------------------------------
@pytest.fixture
def jalen(monkeypatch):
    """A real Jalen, without his Chrome-extension bridge: Jalen() binds a socket and
    publishes data/bridge.json, and a test that does that over the live file steals the
    extension out of his running Chrome (see jarvis/bridge/server.py)."""
    from jarvis.app import Jalen
    from jarvis.bridge import server

    inert = types.SimpleNamespace(on_event=lambda callback: None, start=lambda: None, stop=lambda: None)
    monkeypatch.setattr(server, "get_server", lambda: inert)
    j = Jalen()
    j.muted = True
    try:
        yield j
    finally:
        j.shutdown()


def _outcomes(jalen, monkeypatch, tool_result):
    from jarvis import tools as systools
    from jarvis.brain.router import Intent

    rows = []
    monkeypatch.setattr(jalen.audit, "action",
                        lambda verdict, outcome, error=None: rows.append((verdict.tool, outcome, error)))
    monkeypatch.setattr(systools, "call", lambda tool, args: tool_result)
    reply = jalen.handle_local(Intent(tool="close_app", args={"name": "the calculator"}), his_own_words=True)
    return reply, rows


def test_a_failed_close_is_filed_as_failed_not_executed(jalen, monkeypatch):
    sentence = system.DidNotWork("I can't find a window called calculator.")
    reply, rows = _outcomes(jalen, monkeypatch, sentence)
    assert rows == [("close_app", "failed", "I can't find a window called calculator.")]
    assert reply == "I can't find a window called calculator."


def test_a_close_that_worked_is_still_filed_as_executed(jalen, monkeypatch):
    reply, rows = _outcomes(jalen, monkeypatch, "Closed calculator.")
    assert rows == [("close_app", "executed", None)]
    assert reply == "Closed calculator."


def test_a_failure_is_spoken_even_when_the_rule_has_a_canned_reply(jalen, monkeypatch):
    """intent.reply is what a rule says when its tool WORKED. A tool that did not
    work must not be covered over by it."""
    from jarvis import tools as systools
    from jarvis.brain.router import Intent

    monkeypatch.setattr(jalen.audit, "action", lambda *a, **k: None)
    monkeypatch.setattr(systools, "call",
                        lambda tool, args: system.DidNotWork("Notepad is still open."))
    reply = jalen.handle_local(Intent(tool="close_app", args={"name": "notepad"}, reply="Closing it."),
                               his_own_words=True)
    assert reply == "Notepad is still open."
