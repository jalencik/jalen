"""
Two tools whose return text is spoken, from the live QA of 2026-10-01.

open_url returned "Opening https://www.google.com/search?q=state+space+models
+versus+transformers+for+long+sequences." and in voice mode that whole address
was read aloud. It now says where it is going, not the address.

close_app returned "Closed calculator." the moment it had sent Alt+F4, without
looking: a window that asks "save changes?" is still there, and the audit log
records the close as done. It now looks, and says so when the window is
still open.

Nothing here opens or closes a real window: os.startfile and uiautomation are
replaced by fakes.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.tools import system  # noqa: E402


# ------------------------------------------------------------------ open_url
@pytest.fixture
def opened(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(system.os, "startfile", lambda target: seen.append(target), raising=False)
    return seen


@pytest.mark.parametrize("url, said", [
    ("https://www.google.com/search?q=state+space+models+versus+transformers+for+long+sequences",
     "Opening google.com."),
    ("https://youtube.com", "Opening youtube.com."),
    ("youtube.com", "Opening youtube.com."),
    ("https://mail.google.com", "Opening mail.google.com."),
    ("http://arxiv.org/abs/1706.03762", "Opening arxiv.org."),
    ("https://user:secret@example.org/private?token=abc", "Opening example.org."),
])
def test_open_url_says_the_site_not_the_address(opened, url, said):
    assert system.open_url(url) == said
    assert len(opened) == 1, "the page itself must still be opened"


def test_open_url_opens_the_full_address_whatever_it_says(opened):
    url = "https://www.google.com/search?q=a+b"
    system.open_url(url)
    assert opened == [url]


def test_nothing_spoken_carries_a_query_string_or_a_scheme(opened):
    said = system.open_url("https://www.example.com/path/to/page?x=1&y=2#frag")
    for piece in ("https", "://", "?", "&", "#", "/path"):
        assert piece not in said, said


def test_open_url_with_nothing_still_asks(opened):
    assert system.open_url("") == "Open what URL?"
    assert opened == []


def test_a_failed_open_is_still_a_sentence(monkeypatch):
    def boom(_target):
        raise OSError("no handler")

    monkeypatch.setattr(system.os, "startfile", boom, raising=False)
    said = system.open_url("https://example.org")
    assert said.startswith("Couldn't open")


# ------------------------------------------------------------------ close_app
class _Window:
    NativeWindowHandle = 4242

    def __init__(self, exists=True):
        self._exists = exists
        self.sent: list[str] = []

    def Exists(self, *_a, **_k):
        return self._exists

    def SendKeys(self, keys):
        self.sent.append(keys)


def _install_fake_uiautomation(monkeypatch, window):
    fake = types.ModuleType("uiautomation")
    fake.WindowControl = lambda **_k: window
    monkeypatch.setitem(sys.modules, "uiautomation", fake)


def test_close_app_says_closed_only_when_the_window_went(monkeypatch):
    window = _Window()
    _install_fake_uiautomation(monkeypatch, window)
    monkeypatch.setattr(system, "_window_still_open", lambda handle, wait_s=1.5: False)
    assert system.close_app("calculator") == "Closed calculator."
    assert window.sent == ["{Alt}{F4}"]


def test_close_app_does_not_claim_a_window_that_is_still_there(monkeypatch):
    """The window asked "save changes?" and is still on screen."""
    window = _Window()
    _install_fake_uiautomation(monkeypatch, window)
    monkeypatch.setattr(system, "_window_still_open", lambda handle, wait_s=1.5: True)
    said = system.close_app("notepad")
    assert not said.startswith("Closed")
    assert "still open" in said and "notepad" in said
    # a sentence for the ear: no handle, no function name, no code
    assert "4242" not in said and "_" not in said


def test_close_app_without_a_window_still_says_so(monkeypatch):
    _install_fake_uiautomation(monkeypatch, _Window(exists=False))
    assert system.close_app("calculator") == "I can't find a window called calculator."


def test_the_check_looks_at_the_window_it_closed_not_at_a_namesake(monkeypatch):
    """Two Notepad windows: closing one must not report "still open" because
    the other matches the same title. The check is by handle."""
    seen = {}

    def still_open(handle, wait_s=1.5):
        seen["handle"] = handle
        return False

    window = _Window()
    _install_fake_uiautomation(monkeypatch, window)
    monkeypatch.setattr(system, "_window_still_open", still_open)
    system.close_app("notepad")
    assert seen["handle"] == 4242


class _User32:
    """IsWindow / IsWindowVisible for a window that goes after `polls` looks."""

    def __init__(self, polls=None, visible=True):
        self.polls, self.visible, self.looks = polls, visible, 0

    def IsWindow(self, _hwnd):
        self.looks += 1
        return 0 if (self.polls is not None and self.looks > self.polls) else 1

    def IsWindowVisible(self, _hwnd):
        return 1 if self.visible else 0


def _fake_ctypes(monkeypatch, user32):
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(
        system, "ctypes",
        types.SimpleNamespace(c_void_p=lambda v: v, windll=types.SimpleNamespace(user32=user32)),
    )


def test_a_window_that_goes_within_the_wait_is_closed(monkeypatch):
    user32 = _User32(polls=2)
    _fake_ctypes(monkeypatch, user32)
    assert system._window_still_open(7, wait_s=5.0) is False
    assert user32.looks == 3, "it must stop looking the moment the window is gone"


def test_a_window_that_stays_is_still_open(monkeypatch):
    _fake_ctypes(monkeypatch, _User32())
    assert system._window_still_open(7, wait_s=0.25) is True


def test_a_window_hidden_to_the_tray_counts_as_closed(monkeypatch):
    """Telegram and Spotify "close" to the tray: the window exists, unseen."""
    _fake_ctypes(monkeypatch, _User32(visible=False))
    assert system._window_still_open(7, wait_s=0.25) is False


def test_a_control_with_no_handle_is_taken_at_its_word(monkeypatch):
    """No handle to look at: say what was done and no more."""
    window = _Window()
    window.NativeWindowHandle = 0
    _install_fake_uiautomation(monkeypatch, window)
    monkeypatch.setattr(system, "_window_still_open", lambda handle, wait_s=1.5: True)
    assert system.close_app("notepad") == "Closed notepad."
