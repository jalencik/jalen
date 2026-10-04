"""
Router rules for the Gmail / Calendar / Telegram / Claude Code tools.

Every phrase matched here is a Claude round-trip that never happens — the
whole reason jalen/brain/router.py exists. But a rule belongs here only
when the tool's own output is ALREADY the spoken answer, and two of the
tests below exist to keep that line from being crossed.
"""
from __future__ import annotations

import pytest

from jalen.brain.router import IntentRouter
from jalen.config import CONFIG


@pytest.fixture(scope="module")
def router():
    return IntentRouter(CONFIG)


def route(router, phrase):
    intent = router.route(phrase)
    return (intent.tool, intent.args) if intent else (None, None)


# ===========================================================================
# What SHOULD stay local
# ===========================================================================

@pytest.mark.parametrize("phrase,days", [
    ("what's on my calendar", 0),
    ("what is on my calendar", 0),
    ("what's on my calendar today", 0),
    ("whats on my calendar tomorrow", 1),
    ("what do i have on today", 0),
    ("what's on today", 0),
    ("what do i have on tomorrow", 1),
])
def test_calendar_questions_never_reach_claude(router, phrase, days):
    tool, args = route(router, phrase)
    assert tool == "read_calendar", f"{phrase!r} went to {tool}"
    assert args["days_ahead"] == days


@pytest.mark.parametrize("phrase", [
    "any new emails",
    "any new email",
    "check my email",
    "do i have any unread emails",
    "how many unread emails",
    "any unread mail",
])
def test_email_questions_never_reach_claude(router, phrase):
    tool, _ = route(router, phrase)
    assert tool == "unread_email_headline", f"{phrase!r} went to {tool}"


def test_the_email_rule_speaks_a_sentence_not_a_list():
    """
    The router's output IS what gets spoken, with no model in between. The
    list form carries "[id: 1a0212da312c7ebe]" per row, which read aloud is
    sixteen spoken hex characters, three times over. The rule must point at
    the headline form.
    """
    tool, _ = route(IntentRouter(CONFIG), "any new emails")
    assert tool == "unread_email_headline"
    assert tool != "unread_email_summary"


@pytest.mark.parametrize("phrase,tool", [
    ("is my google connected", "google_status"),
    ("is gmail connected", "google_status"),
    ("is my email working", "google_status"),
    ("is telegram connected", "telegram_status"),
    ("telegram status", "telegram_status"),
    ("is claude code installed", "claude_code_status"),
    ("claude code status", "claude_code_status"),
    ("my telegram chats", "list_telegram_chats"),
])
def test_status_questions_are_plain_facts(router, phrase, tool):
    got, _ = route(router, phrase)
    assert got == tool, f"{phrase!r} went to {got}"


def test_reading_one_chat_by_name(router):
    tool, args = route(router, "read my telegram from uluhbek")
    assert tool == "read_telegram"
    assert args["chat"] == "uluhbek"


# ===========================================================================
# Claude Code
# ===========================================================================

def test_a_plain_task_is_passed_straight_through(router):
    """
    The one place raw dictation is genuinely fine: Claude Code interprets
    the request itself, so a round-trip to reword a sentence its recipient
    was going to interpret anyway buys nothing.
    """
    tool, args = route(router, "ask claude to fix the failing tests")
    assert tool == "ask_claude_code"
    assert args["prompt"] == "fix the failing tests"
    assert "agent" not in args


def test_an_agent_is_picked_out_of_the_sentence(router):
    tool, args = route(router, "ask claude to use cowork on refactoring the auth flow")
    assert tool == "ask_claude_code"
    assert args["agent"] == "cowork"
    assert args["prompt"] == "refactoring the auth flow"


@pytest.mark.parametrize("phrase", [
    "tell claude code to use cowork on the eco pulse folder",
    "ask claude to fix the tests in the jarvis repo",
    "ask claude to clean up this project",
    "ask claude to look at the sat top directory",
])
def test_anything_naming_a_folder_goes_to_the_brain(router, phrase):
    """
    Without this guard the generic rule captures "the eco pulse folder" as
    the PROMPT — handing an autonomous coding agent a location as its task,
    started in whatever directory Jarvis happened to launch from. Resolving
    a spoken folder name is the brain's job, and it can ask which one was
    meant. Starting an agent in the wrong repository is the single mistake
    here worth spending a round-trip to avoid.
    """
    tool, _ = route(router, phrase)
    assert tool is None, f"{phrase!r} was routed to {tool} instead of the brain"


def test_the_folder_guard_is_a_real_word_boundary():
    """
    This guard shipped TWICE with a raw 0x08 byte where its word-boundary
    escape belonged — a shell heredoc ate the backslash both times. A
    control byte there matches nothing, so the lookahead silently never
    fires and every folder phrase routes locally again.
    """
    import inspect

    from jalen.brain import router as router_module

    source = inspect.getsource(router_module._rules)
    control = [hex(ord(c)) for c in source if ord(c) < 9 or 11 <= ord(c) < 32]
    assert not control, f"control characters in the rules table: {control}"


# ===========================================================================
# What must NOT be routed
# ===========================================================================

@pytest.mark.parametrize("phrase", [
    "research quantum computing for me",
    "look up the weather in tashkent",
    "find out about the horizon program deadline",
    "what do you know about graph neural networks",
])
def test_research_stays_on_the_brain_path_deliberately(router, phrase):
    """
    Routing these would be faster and WORSE. web_search returns fenced
    results with URLs and snippets; spoken directly that is a list of links
    read aloud, not an answer. Synthesising across sources is exactly what
    the brain is for, so research is left on the expensive path on purpose.
    """
    tool, _ = route(router, phrase)
    assert tool is None, (
        f"{phrase!r} routed to {tool} — Jarvis would read out a list of "
        "URLs instead of answering the question"
    )


@pytest.mark.parametrize("phrase", [
    "draft an email to rodion in my voice",
    "reply to jaguan wei apologising for the delay",
    "send uluhbek a message saying i'll be late",
])
def test_writing_as_him_stays_on_the_brain_path(router, phrase):
    """Writing needs voice_guide and actual composition. There is no
    canned output that could stand in for it."""
    tool, _ = route(router, phrase)
    assert tool is None, f"{phrase!r} routed to {tool} — nothing would be written"


def test_google_x_still_opens_a_page(router):
    """
    "google X" keeps meaning "put the results on screen": he named the
    engine. (This test used to pin "search the web for X" as well, on the
    argument that he had asked for a page. The live QA of 2026-10-01 marked
    that WRONG - the page opened and the whole address was read aloud, where
    he wanted an answer - so that phrase now reaches the brain, and
    test_research_phrasing_routing.py pins it.)
    """
    tool, args = route(router, "google silero vad")
    assert tool == "open_url"
    assert "silero" in args["url"]
