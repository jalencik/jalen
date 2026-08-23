"""
Getting faster at repeated work — and what it must never learn.

    "it should identify the repetitive work that the user has making Jalen
     complete, and it should get faster on that task over time"

THE SAVING, MEASURED RATHER THAN HOPED FOR
------------------------------------------
From his own data/audit.jsonl:

    router  n=30  p50 3.29 s   p95  9.56 s
    brain   n=92  p50 4.49 s   p95 11.71 s

About 1.2 s at the median. Most of a turn is speech recognition and speech
synthesis, and no amount of remembering touches either. Worth having, worth
being honest about.

MOST OF THIS FILE IS ABOUT REFUSALS
-----------------------------------
A memory that learns too eagerly is worse than none, because its mistakes are
fast, silent and repeated. The four refusals below each prevent a specific
bad outcome, and each has a test that would catch its removal.
"""
from __future__ import annotations

import json

import pytest

from jarvis import habits


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(habits, "HABITS_PATH", tmp_path / "habits.json")
    yield


def _teach(text, tool, args=None, times=habits.LEARN_AFTER):
    for _ in range(times):
        habits.remember(text, tool, args or {})


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------
def test_nothing_is_recalled_before_the_threshold():
    """
    Two is a coincidence. People ask the same thing twice by accident all the
    time; three identical times is a pattern.
    """
    for n in range(1, habits.LEARN_AFTER):
        habits.remember("summarise my emails", "unread_email_summary", {})
        assert habits.recall("summarise my emails") is None, (
            f"acted on a habit after only {n} sighting(s)"
        )
    habits.remember("summarise my emails", "unread_email_summary", {})
    assert habits.recall("summarise my emails") == ("unread_email_summary", {})


def test_wording_is_matched_exactly_after_normalising():
    _teach("summarise my emails", "unread_email_summary")
    assert habits.recall("Summarise my emails!") is not None
    assert habits.recall("summarise   my emails") is not None
    # NOT fuzzy. A near-match here means acting on something he did not say,
    # and a miss only costs one ordinary brain turn.
    assert habits.recall("summarise my telegram") is None
    assert habits.recall("delete my emails") is None


def test_a_changed_decision_resets_rather_than_accumulates():
    """
    He asks the same thing and the brain picks a different tool. That is the
    definition of an unstable decision, and counting the sightings together
    would let two disagreeing answers reach the threshold between them.
    """
    habits.remember("check my messages", "unread_email_summary", {})
    habits.remember("check my messages", "unread_email_summary", {})
    habits.remember("check my messages", "telegram_unread", {})
    assert habits.recall("check my messages") is None

    habits.remember("check my messages", "telegram_unread", {})
    habits.remember("check my messages", "telegram_unread", {})
    assert habits.recall("check my messages") == ("telegram_unread", {})


def test_arguments_are_part_of_the_decision():
    """
    "email Mary" and "email John" are different acts. If args were ignored,
    three emails to three people would teach it one habit and the fourth
    would go to whoever happened to be last.
    """
    habits.remember("send the update", "send_email", {"to": "mary@x.com"})
    habits.remember("send the update", "send_email", {"to": "john@x.com"})
    habits.remember("send the update", "send_email", {"to": "sam@x.com"})
    assert habits.recall("send the update") is None


def test_habits_expire():
    """
    A habit from two months ago is a guess about a person who has changed how
    they work.
    """
    now = 1_000_000.0
    for _ in range(habits.LEARN_AFTER):
        habits.remember("morning check", "morning_brief", {}, now=now)
    assert habits.recall("morning check", now=now + 86400) is not None
    stale = now + (habits.FORGET_AFTER_DAYS + 1) * 86400
    assert habits.recall("morning check", now=stale) is None


def test_using_a_habit_keeps_it_alive():
    now = 1_000_000.0
    for _ in range(habits.LEARN_AFTER):
        habits.remember("morning check", "morning_brief", {}, now=now)
    later = now + (habits.FORGET_AFTER_DAYS - 1) * 86400
    habits.note_use("morning check", now=later)
    assert habits.recall("morning check", now=later + 86400) is not None


# ---------------------------------------------------------------------------
# THE REFUSALS
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("arg", [
    "password", "passphrase", "api_key", "token", "secret", "pin",
    "credential", "otp", "vault_passphrase", "my_password",
])
def test_a_secret_shaped_argument_is_never_written_to_disk(arg, tmp_path):
    """
    These land in data/habits.json in plain text. There is no version of that
    which is acceptable, so the whole request becomes unlearnable.
    """
    _teach("do the thing", "unlock_vault", {arg: "hunter2"})
    assert habits.recall("do the thing") is None
    written = (habits.HABITS_PATH.read_text(encoding="utf-8")
               if habits.HABITS_PATH.exists() else "")
    assert "hunter2" not in written


def test_a_long_free_text_argument_is_not_learned():
    """
    A whole email body is not a stable argument, and remembering one means
    re-sending last week's text under this week's instruction.
    """
    _teach("send it", "send_email", {"body": "x" * 400})
    assert habits.recall("send it") is None


def test_only_flat_arguments_are_learned():
    """
    A nested structure is a plan, not a parameter, and replaying a plan is
    the thing this deliberately does not do.
    """
    _teach("do it", "send_posts", {"posts": ["one", "two", "three"]})
    assert habits.recall("do it") is None


def test_learning_only_happens_for_a_single_tool_turn():
    """
    THE IMPORTANT REFUSAL, asserted where it is enforced.

    A multi-step plan is exactly where the brain earns its cost. Replaying
    one blind is how last week's email reaches this week's person.
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen._learn_from)
    assert "len(calls) != 1" in source, (
        "the single-tool restriction has gone - multi-step plans can now be "
        "replayed from memory without the brain"
    )


def test_a_recalled_habit_still_goes_through_the_safety_engine():
    """
    Speed is never a reason to act on something he did not get asked about.
    The recall path calls handle_local, which classifies exactly as a fresh
    intent does — it does not execute anything itself.
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen.process)
    start = source.index("habits.recall")
    after = source[start:start + 600]
    assert "handle_local" in after, (
        "a recalled habit is being executed without handle_local - it now "
        "bypasses the safety engine"
    )
    assert "systools.call" not in after


def test_recall_returns_a_decision_never_an_answer():
    """
    It remembers what to DO, never what was said. Caching the answer would be
    an assistant confidently reciting yesterday's calendar.
    """
    _teach("what's on my calendar", "todays_events")
    tool, args = habits.recall("what's on my calendar")
    assert tool == "todays_events"
    assert args == {}
    stored = json.loads(habits.HABITS_PATH.read_text(encoding="utf-8"))
    for entry in stored.values():
        assert "answer" not in entry and "result" not in entry


# ---------------------------------------------------------------------------
# Being inspectable
# ---------------------------------------------------------------------------
def test_he_can_see_what_it_thinks_he_does():
    """
    A learning system nobody can inspect is one nobody can correct, and the
    first question about one is always "what does it think I do".
    """
    assert "haven't picked up any habits" in habits.what_i_have_learned()
    _teach("summarise my emails", "unread_email_summary")
    habits.note_use("summarise my emails")
    out = habits.what_i_have_learned()
    assert "summarise my emails" in out
    assert "unread_email_summary" in out


def test_he_can_correct_it():
    _teach("summarise my emails", "unread_email_summary")
    assert habits.recall("summarise my emails") is not None
    assert "Forgotten" in habits.forget("summarise my emails")
    assert habits.recall("summarise my emails") is None


def test_he_can_wipe_all_of_it():
    _teach("one thing", "get_time")
    _teach("another thing", "todays_events")
    assert "all 2 habits" in habits.forget()
    assert habits.recall("one thing") is None
