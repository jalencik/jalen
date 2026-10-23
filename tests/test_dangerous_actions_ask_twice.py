"""
"If the consequence is dangerous enough it should this time dictate the
consequence, and it should basically ask for confirmation once more."
                                                    - his words, 2026-10-23

WHAT CHANGED
------------
A RED action listed under `consequences:` in config/safety.yaml is asked about
twice. First the ordinary "<action>. Confirm?"; after his yes, what the action
does that cannot be taken back, and "Still want it? Confirm?"; only a second
yes runs it. The list is the dangerous end of RED, read from the code that
does it: delete_file is os.unlink, NOT the Recycle Bin; emptying the bin and
moving a folder destroy the original for good; signing out closes everything.

On BOTH gate paths - the router (Jalen.handle_local) and the brain's hook
(Brain._make_hook) - because an action reaches the machine through either. A
limit question that named the action stands in for the first "Confirm?" and
never for this one: the second question is the one he asked for.
"""
from __future__ import annotations

import time

import pytest

from jalen.app import Jalen, _PLAIN_ANSWER_WORDS
from jalen.brain.router import Intent
from jalen.config import CONFIG
from jalen.safety import SafetyEngine, Tier

from test_limits_are_enforced_on_both_paths import (  # noqa: F401 - fixtures
    NO, YES, brain, denied, hook_call, jalen, the_app)
from test_stop_window_takes_answers import a_jalen

CONSEQUENCES = CONFIG.get("safety_tiers", {}).get("consequences") or {}
RED = set((CONFIG.get("safety_tiers", {}).get("red") or {}).get("tools") or [])


def engine():
    e = SafetyEngine(CONFIG)
    e.paranoid = False
    e.posture = "irreversible_only"
    return e


# --------------------------------------------------------------------------
# 1. THE POLICY
# --------------------------------------------------------------------------
def test_the_dangerous_actions_he_would_lose_something_to_are_listed():
    for tool in ("delete_file", "empty_recycle_bin", "move_folder", "sign_out"):
        assert tool in CONSEQUENCES, tool


@pytest.mark.parametrize("tool", sorted(CONSEQUENCES))
def test_every_listed_action_is_red_so_the_question_is_actually_asked(tool):
    assert tool in RED, f"{tool} has a consequence but is not RED, so nobody asks it"


@pytest.mark.parametrize("tool", sorted(CONSEQUENCES))
def test_no_consequence_contains_an_answer_word(tool):
    """
    Its echo comes back through the microphone. With "no", "sure" or "okay" in
    it, the echo could answer the question - or his real "no" would match a word
    of the question and be thrown away as the echo (_echoes_the_confirmation).
    """
    words = set(CONSEQUENCES[tool].lower().replace(".", " ").replace(",", " ").split())
    assert not words & _PLAIN_ANSWER_WORDS, (tool, words & _PLAIN_ANSWER_WORDS)


@pytest.mark.parametrize("tool,args", [
    ("delete_file", {"path": "C:/tmp/x.txt"}),
    ("empty_recycle_bin", {}),
    ("sign_out", {}),
])
def test_a_listed_red_action_carries_its_second_question(tool, args):
    verdict = engine().classify(tool, args)
    assert verdict.tier is Tier.RED
    assert verdict.second_question == f"{CONSEQUENCES[tool]} Still want it? Confirm?"


@pytest.mark.parametrize("tool,args", [
    ("send_email", {"to": "someone@example.com", "subject": "hi", "body": "hi"}),
    ("clear_temp_files", {}),
])
def test_an_ordinary_red_action_is_asked_once(tool, args):
    verdict = engine().classify(tool, args)
    assert verdict.tier is Tier.RED and verdict.second_question == ""


def test_a_blocked_action_asks_nothing_at_all():
    verdict = engine().classify("delete_file", {"path": "C:/Windows/System32/kernel32.dll"})
    assert verdict.tier is Tier.BLACK and verdict.second_question == ""


def test_nothing_read_can_reach_the_second_question():
    """After a read the action is refused outright; there is no question to answer."""
    verdict = engine().classify("delete_file", {"path": "C:/tmp/x.txt"}, origin="content")
    assert verdict.tier is Tier.BLACK and verdict.second_question == ""


def test_a_name_in_braces_is_filled_from_the_actions_own_arguments():
    e = engine()
    e._consequences = {"delete_file": "{path} is deleted for good."}
    assert e._consequence("delete_file", {"path": "C:/tmp/x.txt"}) == "C:/tmp/x.txt is deleted for good."
    assert e._consequence("delete_file", {}) == "it is deleted for good."
    e._consequences = {"delete_file": "Deleted {for good."}
    assert e._consequence("delete_file", {}) == "Deleted for good."


def test_a_secret_argument_is_never_spoken_in_it():
    e = engine()
    e._consequences = {"delete_file": "This uses {password}."}
    assert "hunter2" not in e._consequence("delete_file", {"password": "hunter2"})


# --------------------------------------------------------------------------
# 2. THE ROUTER PATH
# --------------------------------------------------------------------------
def delete_it(jalen):
    return jalen.handle_local(Intent(tool="delete_file", args={"path": "C:/tmp/doesnotmatter.txt"}),
                              his_own_words=True)


def test_the_router_asks_the_consequence_after_his_yes_and_runs_on_the_second(jalen):
    jalen.answers = [YES, YES]
    delete_it(jalen)
    first, second = jalen.asked
    assert first == "delete file: C:/tmp/doesnotmatter.txt. Confirm?"
    assert second == f"{CONSEQUENCES['delete_file']} Still want it? Confirm?"
    assert [t for t, _ in jalen.ran] == ["delete_file"]


def test_the_router_cancels_on_a_no_to_the_second_question(jalen):
    jalen.answers = [YES, NO]
    assert delete_it(jalen) == "Cancelled."
    assert len(jalen.asked) == 2 and jalen.ran == []


def test_the_router_never_asks_the_second_after_a_no_to_the_first(jalen):
    jalen.answers = [NO]
    assert delete_it(jalen) == "Cancelled."
    assert len(jalen.asked) == 1 and jalen.ran == []


def test_a_cancelled_second_question_uses_up_no_limit(jalen):
    jalen.answers = [YES, NO]
    delete_it(jalen)
    deletions = [u for u in jalen.safety.governor.usage() if u["name"] == "deletions"][0]
    assert deletions["used"] == 0


# --------------------------------------------------------------------------
# 3. THE BRAIN'S HOOK
# --------------------------------------------------------------------------
def test_the_hook_asks_the_consequence_after_his_yes_and_allows_on_the_second(brain):
    brain.script.answers = [YES, YES]
    assert hook_call(brain, "delete_file", {"path": "C:/tmp/x.txt"}) == {}
    first, second = brain.script.questions
    assert first == "delete file: C:/tmp/x.txt. Confirm?"
    assert second == f"{CONSEQUENCES['delete_file']} Still want it? Confirm?"


def test_the_hook_denies_on_a_no_to_the_second_question(brain):
    brain.script.answers = [YES, NO]
    result = hook_call(brain, "delete_file", {"path": "C:/tmp/x.txt"})
    assert denied(result) and len(brain.script.questions) == 2


def test_the_hook_never_asks_the_second_after_a_no_to_the_first(brain):
    brain.script.answers = [NO]
    assert denied(hook_call(brain, "delete_file", {"path": "C:/tmp/x.txt"}))
    assert len(brain.script.questions) == 1


def test_the_hook_asks_an_ordinary_red_action_once(brain):
    brain.script.answers = [YES]
    assert hook_call(brain, "send_email",
                     {"to": "someone@example.com", "subject": "hi", "body": "hi"}) == {}
    assert len(brain.script.questions) == 1


def test_both_paths_ask_it_in_the_same_place():
    import inspect

    from jalen.brain.agent import Brain

    for source in (inspect.getsource(Jalen.handle_local), inspect.getsource(Brain._make_hook)):
        first = source.index("Tier.RED")
        second = source.index('getattr(verdict, "second_question", "")')
        audited = source.index('"executed" if approved else "cancelled"', first)
        assert first < second < audited


# --------------------------------------------------------------------------
# 4. ITS OWN ECHO IS NOT HIS ANSWER, AND HIS ANSWER IS NOT ITS ECHO
# --------------------------------------------------------------------------
def test_the_second_question_coming_back_answers_nothing_and_his_yes_still_counts():
    j = a_jalen()
    j.mode = "voice"
    j._awaiting_confirmation = True
    j._confirm_question = engine().classify("delete_file", {"path": "C:/tmp/x.txt"}).second_question
    j._confirm_asked_at = time.monotonic()
    for echo in ("so it can't be brought back", "Still want it? Confirm?",
                 "It does not go to the Recycle Bin"):
        j.process(echo)
        assert j._answer_q.empty(), f"its own question coming back, {echo!r}, answered it"
    j.process("yes")
    assert j._answer_q.get_nowait().outcome == "yes"
