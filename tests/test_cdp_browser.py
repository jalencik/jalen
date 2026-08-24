"""
The browser Jalen drives is a PLAIN Chrome it attaches to, not one it
launched - and that distinction is the whole fix.

    "it is still going through ghost mode ... all of them should happen on
     normal browser jaloliddin2009applicant@gmail.com even chatgpt and
     gemini as well"

TWO WALLS, MEASURED ON HIS MACHINE, BOTH DISSOLVED BY ONE CHANGE
---------------------------------------------------------------
  1. Chrome 136+ refuses to be automated on the profile he browses in.
     launch_persistent_context on his real User Data directory timed out at
     150 seconds; on a dedicated directory it took 0.8s. A deliberate
     anti-cookie-theft control, unpassable.

  2. Google refuses OAuth from a browser carrying navigator.webdriver, which
     Playwright sets when it LAUNCHES Chrome. Verified last session: the
     sign-in reached accounts.google.com/v3/signin/REJECTED, "this browser
     or app may not be secure".

Launch a plain chrome.exe (no automation flags) on a dedicated directory
with a debug port, and ATTACH over CDP. navigator.webdriver is then false,
so Google accepts a sign-in; and the directory is separate, so his everyday
Chrome is untouched. Verified this session: the same sign-in reached
accounts.google.com/v3/signin/challenge/pwd - the PASSWORD page, not the
rejection - in his real account.

These tests drive a real Chrome and skip where one is not installed, because
a green suite on a CI box with no Chrome must not be read as evidence about
his laptop.
"""
from __future__ import annotations

import pytest

from jarvis.tools import webagent as wa

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture()
def session():
    """A live CDP session, torn down no matter what the test does."""
    if not wa._chrome_exe():
        pytest.skip("Chrome not installed here")
    try:
        wa._Session.get().start()
    except wa.BrowserUnavailable as exc:
        pytest.skip(f"Chrome would not start here: {exc}")
    yield wa._Session.get()
    wa.close_browser()


class TestTheMechanismItself:

    def test_the_port_helpers_are_sane(self):
        port = wa._free_port()
        assert 1024 < port < 65536
        # Nothing is listening on a just-freed ephemeral port.
        assert wa._wait_for_port(port, timeout=0.5) is False

    def test_a_chrome_executable_is_found(self):
        if not wa._chrome_exe():
            pytest.skip("Chrome not installed here")
        assert wa._chrome_exe().lower().endswith("chrome.exe")


class TestTheAttachedBrowserIsNotFlagged:
    """
    The single fact the whole fix rests on. If this is ever true, Google
    goes back to refusing the sign-in and he is back in ghost mode.
    """

    def test_navigator_webdriver_is_false(self, session):
        flag = session.do(lambda page: page.evaluate("navigator.webdriver"))
        assert not flag, (
            "navigator.webdriver is set - Google will refuse the sign-in, "
            "which is exactly the wall this design exists to get around"
        )

    def test_it_can_actually_drive_a_page(self, session):
        """Attaching is worthless if it cannot then control the page."""
        title = session.do(lambda page: (
            page.goto("https://example.com/", timeout=30000,
                      wait_until="domcontentloaded"),
            page.title(),
        )[1])
        assert "example" in title.lower()

    def test_it_lands_on_a_dedicated_profile_not_his_everyday_one(self, session):
        """
        His real Chrome must be untouched. The automated one lives in the
        project's own data directory; if these two were ever the same folder
        Chrome would refuse to open it and his browsing would be at risk.
        """
        import os
        everyday = str(os.environ.get("LOCALAPPDATA", "")).lower()
        assert str(wa.PROFILE_DIR).lower() != everyday
        assert "browser_profile" in str(wa.PROFILE_DIR)


class TestChromeProcessLifecycle:

    def test_the_launched_chrome_is_ended_on_stop(self, session):
        """
        Jalen owns the chrome.exe it launched, so closing the session must
        end it - a leaked headed Chrome on every delegation would pile up.
        """
        proc = session._proc
        assert proc is not None and proc.poll() is None, "no live chrome"
        wa.close_browser()
        # Give terminate a beat to take effect.
        import time
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        assert proc.poll() is not None, "the chrome we launched outlived us"
