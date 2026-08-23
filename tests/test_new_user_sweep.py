"""
Act like a new user and try everything.

He asked for this directly: "I would like you to act like a new user and I
would like you to test every possible key word quit word or whatever with
Jalen, I want you to look every possible case a user might pronounce jalen,
with udareniya or whatever, and I would like you to make all of them
acceptable."

This is the breadth sweep. tests/test_name_pronunciations.py owns the NAME
(how it is spelled and stressed); this file owns the COMMANDS — the phrases
someone who has just installed it would actually say, in the shapes they
would actually say them.

WHAT COUNTS AS PASSING. Not "reaches the right tool" for everything: plenty
of requests are supposed to go to the brain, and a router rule for every
sentence would be a worse assistant, not a better one. What is asserted is:

  * the SELF-COMMANDS (quit, mute, pause...) must route locally, always.
    These are the ones with no fallback — if "quit" reaches the brain and
    the brain is down, there is no way to stop it.
  * the DOCUMENTED commands (README, config) must route, or the docs lie.
  * nothing must route to the WRONG tool, which is worse than not routing.

Run it as a report:  pytest tests/test_new_user_sweep.py -q -s
"""
from __future__ import annotations

import pytest

from jarvis.brain.router import IntentRouter
from jarvis.config import CONFIG


@pytest.fixture(scope="module")
def router() -> IntentRouter:
    return IntentRouter(CONFIG)


# ---------------------------------------------------------------------------
# 1. Stopping it. The one thing a new user must never fail at.
# ---------------------------------------------------------------------------
# Every way someone reaches for "make it stop", including the panicky ones.
STOP_PHRASES = [
    "quit", "exit", "close", "kill", "shutdown", "shut down", "turn off",
    "quit jalen", "jalen quit", "hey jalen quit", "jalen, quit",
    "quit, jalen", "exit jalen", "shut down jalen", "turn off jalen",
    "kill jalen", "close jalen", "quit yourself", "shut down yourself",
    "hey jarvis quit", "jarvis, quit", "quit jarvis",
    "jalen. quit.", "jalen! quit", "hey jalen. exit.",
]


@pytest.mark.parametrize("phrase", STOP_PHRASES)
def test_a_new_user_can_always_stop_it(router, phrase):
    """
    If this fails the only remaining exit is Task Manager. It is the single
    most important routing guarantee in the project.
    """
    hit = router.route(phrase)
    assert hit is not None, f"{phrase!r} did not route — it would reach the LLM"
    assert hit.tool == "jalen_quit", f"{phrase!r} -> {hit.tool}"


# ---------------------------------------------------------------------------
# 2. The rest of the self-commands, in the shapes people say them.
# ---------------------------------------------------------------------------
SELF_COMMAND_PHRASES = [
    # silence
    ("mute", "jalen_mute"), ("be quiet", "jalen_mute"), ("shut up", "jalen_mute"),
    ("silence", "jalen_mute"), ("mute yourself", "jalen_mute"),
    ("shut up jalen", "jalen_mute"), ("be quiet, jalen", "jalen_mute"),
    # speech back on
    ("unmute", "jalen_unmute"), ("speak", "jalen_unmute"),
    ("you can talk", "jalen_unmute"), ("speak now", "jalen_unmute"),
    ("unmute jalen", "jalen_unmute"),
    # stop listening
    ("go to sleep", "jalen_sleep"), ("sleep", "jalen_sleep"),
    ("stand by", "jalen_sleep"), ("stop listening", "jalen_sleep"),
    # pause / resume
    ("pause", "jalen_pause"), ("hold on", "jalen_pause"),
    ("take a break", "jalen_pause"), ("pause jalen", "jalen_pause"),
    ("hold on, jalen", "jalen_pause"),
    ("resume", "jalen_resume"), ("carry on", "jalen_resume"),
    ("wake up", "jalen_resume"), ("i'm back", "jalen_resume"),
    ("start listening", "jalen_resume"), ("carry on, jalen", "jalen_resume"),
    # restart
    ("restart", "jalen_restart"), ("reboot", "jalen_restart"),
    ("reload", "jalen_restart"), ("restart yourself", "jalen_restart"),
    # summons
    ("jalen", "jalen_ack"), ("hey jalen", "jalen_ack"), ("jarvis", "jalen_ack"),
]


@pytest.mark.parametrize("phrase, tool", SELF_COMMAND_PHRASES)
def test_the_self_commands_route_locally(router, phrase, tool):
    """
    Locally, for zero tokens, and without the network. An assistant whose
    "be quiet" needs an API call cannot be made quiet when the API is down.
    """
    hit = router.route(phrase)
    assert hit is not None, f"{phrase!r} did not route"
    assert hit.tool == tool, f"{phrase!r} -> {hit.tool}, expected {tool}"


# ---------------------------------------------------------------------------
# 3. Everything the documentation promises.
# ---------------------------------------------------------------------------
# From README.md's shortcut table and the config comments. If one of these
# stops routing, the documentation has become a lie — a different and worse
# failure than a test changing.
DOCUMENTED = [
    ("read it all", "jalen_read_all"),
    ("what can't you do yet", "review_weaknesses"),
    ("how fast was that", "jalen_timing"),
    ("play we are the people", "play_media"),
    ("what time is it", "get_time"),
    ("open chrome", "open_target"),
    ("what did i miss", "telegram_unread"),
    ("my wifi keeps dropping", "diagnose_wifi"),
    ("what's wrong with my computer", "diagnose"),
    ("clear the temp files", "clear_temp_files"),
    ("what tabs are open", "list_browser_tabs"),
    ("test yourself", "run_own_tests"),
    ("are you ok", "own_health"),
    ("diagnose yourself", "self_diagnose"),
    ("list coding jobs", "list_coding_jobs"),
]


@pytest.mark.parametrize("phrase, tool", DOCUMENTED)
def test_every_documented_command_still_works(router, phrase, tool):
    hit = router.route(phrase)
    assert hit is not None, f"README promises {phrase!r} and it does not route"
    assert hit.tool == tool, f"{phrase!r} -> {hit.tool}, expected {tool}"


# ---------------------------------------------------------------------------
# 4. Politeness, hesitation and throat-clearing.
#
# Nobody speaks in imperatives. A new user says "um, could you please open
# chrome for me" and expects the same result as "open chrome".
# ---------------------------------------------------------------------------
POLITE_WRAPPERS = [
    "open chrome",
    "please open chrome",
    "can you open chrome",
    "could you open chrome",
    "could you please open chrome",
    "hey jalen, could you please open chrome",
    "um, open chrome",
    "so, open chrome",
    "open chrome please",
    "open chrome for me",
    "open chrome now",
    "open chrome real quick",
    "hey jalen, open chrome for me please",
    "okay jalen, can you open chrome right now",
]


@pytest.mark.parametrize("phrase", POLITE_WRAPPERS)
def test_politeness_does_not_break_a_command(router, phrase):
    hit = router.route(phrase)
    assert hit is not None, f"{phrase!r} did not route"
    assert hit.tool == "open_target", f"{phrase!r} -> {hit.tool}"
    assert hit.args.get("name") == "chrome", (
        f"{phrase!r} passed name={hit.args.get('name')!r} — the courtesy leaked "
        "into the argument"
    )


# ---------------------------------------------------------------------------
# 5. Things that must NOT route. Routing them would be worse than not.
# ---------------------------------------------------------------------------
MUST_REACH_THE_BRAIN = [
    "what do you think about this",
    "write me an essay about machine learning",
    "explain how transformers work",
    "hand this task off to claude code",
    "draft a reply to that email in my voice",
]


@pytest.mark.parametrize("phrase", MUST_REACH_THE_BRAIN)
def test_open_ended_requests_are_left_to_the_brain(router, phrase):
    """
    A router rule for these would answer with a canned string, or launch a
    coding agent on the word "this". Falling through is correct.
    """
    hit = router.route(phrase)
    assert hit is None or hit.tool not in {
        "jalen_quit", "jalen_mute", "jalen_sleep", "jalen_pause",
    }, f"{phrase!r} was mistaken for a self-command ({hit.tool})"


NOT_FOR_JALEN = [
    "tell alan i'm late",
    "message julian about tomorrow",
    "email helen the report",
    "search reddit for jarvis",
    "google jalen",
]


@pytest.mark.parametrize("phrase", NOT_FOR_JALEN)
def test_a_real_person_is_never_stripped_out(router, phrase):
    """
    The name shape is wide. It must not be so wide that a message to a human
    being loses the human being — a message going to the wrong person, or
    with the name missing, is not recoverable by apologising afterwards.
    """
    hit = router.route(phrase)
    if hit is None:
        return                       # reached the brain, which has the context
    blob = " ".join(str(v) for v in (hit.args or {}).values()).lower()
    for person in ("alan", "julian", "helen", "jarvis", "jalen"):
        if person in phrase:
            assert person in blob or hit.tool in ("open_url", "search_site"), (
                f"{phrase!r} lost {person!r}: {hit.tool} {hit.args}"
            )
            break


# ---------------------------------------------------------------------------
# 6. The report. Not an assertion — a printed summary for a human.
# ---------------------------------------------------------------------------
def test_print_the_sweep_summary(router, capsys):
    """
    Runs every phrase above and prints what each resolved to. Assertion-free
    on purpose: it exists so `pytest -q -s` gives a readable answer to "what
    does it understand", which is the question a new user actually has.
    """
    groups = {
        "stop": [(p, "jalen_quit") for p in STOP_PHRASES],
        "self-commands": SELF_COMMAND_PHRASES,
        "documented": DOCUMENTED,
        "polite": [(p, "open_target") for p in POLITE_WRAPPERS],
    }
    lines = []
    routed = total = 0
    for label, cases in groups.items():
        hits = 0
        for phrase, expected in cases:
            hit = router.route(phrase)
            ok = hit is not None and hit.tool == expected
            hits += ok
            if not ok:
                lines.append(f"    MISS  {phrase!r} -> "
                             f"{getattr(hit, 'tool', None)} (wanted {expected})")
        routed += hits
        total += len(cases)
        lines.insert(0, f"  {label:16s} {hits}/{len(cases)}")

    with capsys.disabled():
        print("\n\nNew-user sweep")
        print("\n".join(lines))
        print(f"  {'TOTAL':16s} {routed}/{total} routed locally, "
              f"for zero tokens\n")
    assert routed == total, "see the MISS lines above"
