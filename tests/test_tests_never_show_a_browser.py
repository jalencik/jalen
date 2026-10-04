"""
Every test run opened a real Chrome window on his screen and slammed it shut.

tests/test_cdp_browser.py (and three other files) start Jalen's REAL Chrome
through webagent._Session - deliberately, because faking the browser cannot
prove the CDP attach works. But _launch always starts Chrome HEADED (it has
to: a headless window cannot be signed into by a human), so every full test
run popped a visible window that loaded example.com and was killed a second
later. On 2026-10-01 several agents ran the suite in parallel, dozens of
times, and from his chair that looked exactly like "the browser keeps
failing".

Tests now run that real Chrome HEADLESS, through one test-only switch
(JALEN_TEST_HEADLESS_CHROME, set by tests/conftest.py for the whole run).
Jalen itself is unchanged: with the switch unset, Chrome opens visible so he
can sign in.
"""
from __future__ import annotations

import os

import pytest

from jalen.tools import webagent as wa


@pytest.fixture
def launched(monkeypatch, tmp_path):
    """Capture the chrome.exe command line without starting anything."""
    seen = []

    class _Proc:
        def poll(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(wa, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(wa, "_chrome_exe", lambda: "chrome.exe")
    monkeypatch.setattr(wa, "_kill_stale_profile_chrome", lambda: None)
    monkeypatch.setattr(wa, "_wait_for_port", lambda port, timeout=30.0: False)
    monkeypatch.setattr(wa.subprocess, "Popen", lambda args, **kw: seen.append(list(args)) or _Proc())
    return seen


def test_the_suite_runs_with_the_switch_on():
    assert os.environ.get("JALEN_TEST_HEADLESS_CHROME") == "1", (
        "tests/conftest.py must set it, or every run shows him a browser")


def test_under_the_suite_jalens_chrome_starts_headless(launched):
    wa._Session()._launch()
    assert launched, "Chrome was never launched"
    assert "--headless=new" in launched[0]


def test_for_him_jalens_chrome_is_visible(launched, monkeypatch):
    monkeypatch.delenv("JALEN_TEST_HEADLESS_CHROME", raising=False)
    wa._Session()._launch()
    assert launched and not any(a.startswith("--headless") for a in launched[0])
