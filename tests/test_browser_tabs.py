"""
Telling one browser window from another.

His words: Jalen "should recognize youtube window of a chrome, or common app
window of a chrome, when closing chrome, it should be able to remove
according pages or windows".

close_app already matched a window by title, which sounds like it covered
this and did not: it takes the FIRST window whose title contains the word.
On a machine with six Chrome windows that is a coin toss, and the one it
picks might be an hour of research.
"""
from __future__ import annotations

import pytest

from jarvis.tools import browsertabs


def fake_windows(*pages):
    """(handle, page title, browser) tuples, as _windows() returns."""
    return [(1000 + i, page, "Chrome") for i, page in enumerate(pages)]


# --------------------------------------------------------------- matching
def test_an_exact_title_beats_a_partial_one():
    windows = fake_windows("YouTube", "YouTube Music", "Common App")
    matched = browsertabs._match(windows, "YouTube")
    assert len(matched) == 1
    assert matched[0][1] == "YouTube"


def test_a_partial_match_works_when_nothing_is_exact():
    windows = fake_windows("Common App - Apply", "Chess.com")
    matched = browsertabs._match(windows, "common app")
    assert len(matched) == 1


def test_matching_is_case_insensitive():
    windows = fake_windows("YouTube")
    assert browsertabs._match(windows, "youtube")
    assert browsertabs._match(windows, "YOUTUBE")


def test_an_empty_query_matches_nothing():
    """Otherwise "close the  window" would match everything open."""
    assert browsertabs._match(fake_windows("YouTube"), "") == []


# ------------------------------------------------------ the Electron trap
def test_electron_apps_are_not_browsers():
    """
    THE BUG THE FIRST LIVE RUN CAUGHT. "Chrome_WidgetWin_1" is Chromium's
    window class, and every Electron app is Chromium — Visual Studio Code,
    Slack, Discord and the Claude desktop app were all listed as browser
    windows. "Close the jarvis window" could have closed his editor.
    """
    assert "code.exe" not in browsertabs.BROWSER_EXES
    assert "slack.exe" not in browsertabs.BROWSER_EXES
    assert "discord.exe" not in browsertabs.BROWSER_EXES
    assert "claude.exe" not in browsertabs.BROWSER_EXES
    assert "chrome.exe" in browsertabs.BROWSER_EXES


def test_an_unidentifiable_window_is_left_alone(monkeypatch):
    """A lookup failure must fail toward not touching the window."""
    monkeypatch.setattr(browsertabs, "_user32", None)
    assert browsertabs._browser_for(12345) is None


# ---------------------------------------------------------------- closing
def test_an_ambiguous_close_refuses_rather_than_guessing(monkeypatch):
    """
    THE WHOLE POINT. Three windows containing "google", closing one and
    reporting success, is the coin toss this module exists to remove.
    """
    monkeypatch.setattr(
        browsertabs, "_windows",
        lambda: fake_windows("Google Search", "Google Docs", "Google Drive"),
    )
    reply = browsertabs.close_browser_tab("google")
    assert "3 windows match" in reply
    assert "haven't closed any" in reply


def test_closing_something_that_is_not_open_lists_what_is(monkeypatch):
    monkeypatch.setattr(
        browsertabs, "_windows", lambda: fake_windows("Chess.com", "Gmail")
    )
    reply = browsertabs.close_browser_tab("YouTube")
    assert "closed nothing" in reply
    assert "Chess.com" in reply, "he isn't told what IS open"


def test_a_window_that_refuses_to_close_is_reported_honestly(monkeypatch):
    """
    A page with unsaved work shows "leave site?" and stays. Reporting that
    as closed is a lie he only discovers later.
    """
    monkeypatch.setattr(browsertabs, "_windows", lambda: fake_windows("Common App"))

    class FakeUser32:
        def PostMessageW(self, *a):
            return 1

    monkeypatch.setattr(browsertabs, "_user32", FakeUser32())
    monkeypatch.setattr(browsertabs.time, "sleep", lambda s: None)

    reply = browsertabs.close_browser_tab("Common App")
    assert "still there" in reply
    assert "leave site" in reply


def test_a_window_that_does_close_is_confirmed(monkeypatch):
    state = {"open": True}

    def windows():
        return fake_windows("YouTube") if state["open"] else []

    class FakeUser32:
        def PostMessageW(self, *a):
            state["open"] = False
            return 1

    monkeypatch.setattr(browsertabs, "_windows", windows)
    monkeypatch.setattr(browsertabs, "_user32", FakeUser32())
    monkeypatch.setattr(browsertabs.time, "sleep", lambda s: None)

    assert "Closed the YouTube window" in browsertabs.close_browser_tab("YouTube")


def test_the_browser_name_is_stripped_from_titles():
    """
    He says "the YouTube window", not "the YouTube - Google Chrome window".
    Matching has to work on what he says.
    """
    assert browsertabs._BROWSER_SUFFIX.sub("", "YouTube - Google Chrome").strip() == "YouTube"
    assert browsertabs._BROWSER_SUFFIX.sub("", "Gmail - Mozilla Firefox").strip() == "Gmail"


def test_listing_admits_background_tabs_are_invisible(monkeypatch):
    """
    Chrome does not publish its tab strip. Saying so beats a tool that
    silently fails to find a tab he can see on screen.
    """
    monkeypatch.setattr(browsertabs, "_windows", lambda: fake_windows("YouTube"))
    assert "Background tabs aren't visible" in browsertabs.list_browser_tabs()


def test_no_browser_windows_says_so(monkeypatch):
    monkeypatch.setattr(browsertabs, "_windows", list)
    assert "No browser windows are open" in browsertabs.list_browser_tabs()


# ----------------------------------------------------------- reachability
def test_tiers_and_grammar():
    from jarvis import tools
    from jarvis.brain.router import IntentRouter
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    for name, tier in (
        ("list_browser_tabs", Tier.GREEN),
        ("focus_browser_tab", Tier.GREEN),
        ("close_browser_tab", Tier.AMBER),
    ):
        assert name in tools.REGISTRY and name in TOOL_SPECS
        assert engine.classify(name, {}).tier is tier

    router = IntentRouter(CONFIG)
    assert router.route("close the youtube window").args["page"] == "youtube"
    assert router.route("close the common app tab").args["page"] == "common app"
    assert router.route("switch to the youtube window").tool == "focus_browser_tab"
    assert router.route("what tabs are open").tool == "list_browser_tabs"


@pytest.mark.parametrize(
    "phrase, expected",
    [
        # Generic words are not page names — these are Alt+F4 and Alt+Tab.
        ("close the window", "keyboard_shortcut"),
        ("close this window", "keyboard_shortcut"),
        ("close current window", "keyboard_shortcut"),
        ("switch to last window", "keyboard_shortcut"),
        ("switch to the last window", "keyboard_shortcut"),
        # Real page names still reach the browser tools.
        ("close the youtube window", "close_browser_tab"),
        ("switch to the youtube window", "focus_browser_tab"),
    ],
)
def test_generic_window_words_are_not_page_names(phrase, expected):
    """
    A regression I caused adding these rules. The page-name capture
    swallowed "close the window" as page="the" and "switch to last window"
    as page="last", so two commands that had worked for months stopped —
    silently, because both still returned something plausible.
    """
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    hit = IntentRouter(CONFIG).route(phrase)
    assert hit is not None and hit.tool == expected, (
        f"{phrase!r} -> {hit.tool if hit else None}"
    )


def test_closing_a_whole_app_is_still_a_different_thing():
    """
    "Close Chrome" means quit the browser. "Close the YouTube window" means
    one window. Collapsing them would make the coarse command unreachable.
    """
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    router = IntentRouter(CONFIG)
    assert router.route("close chrome").tool == "close_app"
    assert router.route("close spotify").tool == "close_app"
