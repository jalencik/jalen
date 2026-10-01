"""
Dropping "the / that / my" from close commands made generic words close the
wrong window.

The conversation-quality fix taught the close rule to strip articles, so "close
the calculator" works. The independent re-check found the side effect: "close
that window" became close_app(name="window"), "close that tab" became
close_app("tab"), "close the app" close_app("app"), "close the program"
close_app("program"). close_app matches by SUBSTRING on the window title and is
GREEN (no announcement), so those closed "Windows PowerShell", "WhatsApp",
"Program Files" or a Chrome window called "Create React App" - whichever
matched first.

A generic noun is not the name of anything. It now yields no app name, so the
sentence falls through to the rules that close what is in front of him
(Alt+F4, Ctrl+W) or to the brain.
"""
from __future__ import annotations

import pytest

from jarvis.brain.router import IntentRouter
from jarvis.config import CONFIG


@pytest.fixture(scope="module")
def router():
    return IntentRouter(CONFIG)


@pytest.mark.parametrize("said", [
    "close that window", "close the window", "close this window", "close that tab",
    "close the tab", "close the app", "close that app", "close the program",
    "close the application", "close the software", "close the browser", "close it",
    "close that", "close this", "quit the app", "exit the program",
    "switch to the window", "switch to that tab", "go to the app",
])
def test_a_generic_noun_is_never_taken_for_an_apps_name(router, said):
    intent = router.route(said)
    named = (intent.args.get("name") or "").lower() if intent else ""
    assert named not in {"window", "tab", "app", "application", "program", "software",
                         "browser", "it", "this", "that", "one", "thing"}, (
        f"{said!r} became {intent.tool}({named!r})")


@pytest.mark.parametrize("said, tool, name", [
    ("close the calculator", "close_app", "calculator"),
    ("close calculator", "close_app", "calculator"),
    ("close notepad", "close_app", "notepad"),
    ("quit the spotify app", "close_app", "spotify"),
    ("close my chrome", "close_app", "chrome"),
    ("close the telegram app", "close_app", "telegram"),
])
def test_a_real_app_name_still_closes_that_app(router, said, tool, name):
    intent = router.route(said)
    assert intent is not None and intent.tool == tool, said
    assert (intent.args.get("name") or "").lower() == name, said
