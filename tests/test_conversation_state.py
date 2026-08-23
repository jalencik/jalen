"""
Knowing what "it" means, what "yes" agrees to, and what is still running.

    9.  "I said it to send it to my saved messages" — and it drafted
        something else instead.
    11. "I said yes go on, but it has stopped man, it should have a
        consistent memory."
    6C. a task must not silently disappear.

Three complaints, one missing piece: nothing remembered what the current
subject WAS. Every turn arrived as though it were the first, so "it" referred
to nothing, "yes" agreed to nothing, and a task existed only inside the thread
running it.

WHY THERE IS NO GRAPH DATABASE HERE
-----------------------------------
He offered Obsidian and Graphiti. `jarvis/tools/memory.py` already holds real
semantic long-term memory — local, embedded, with a filter that refuses
credential-shaped text before it reaches the database. That layer works and
was never the gap.

The gap was the PRESENT TENSE: the last thing offered, the thing "it" points
at, which task "continue" means. That is a handful of fields living for
minutes. Putting it in a graph database would add a service to run and a
failure mode where the assistant cannot answer "yes" because a database is
down.
"""
from __future__ import annotations

import pytest

from jarvis import conversation as c
from jarvis import plan as planning


@pytest.fixture(autouse=True)
def clean():
    c.forget_context()
    c._tasks.clear()
    yield
    c.forget_context()
    c._tasks.clear()


# ---------------------------------------------------------------------------
# REQUIREMENT 9 — the action and the destination survive the turn
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("said,action,destination", [
    # The exact sentence from his log that produced a draft instead of a send.
    ("could you please make a post about them and send them in my saved messages",
     "send", "Saved Messages"),
    ("just send it to my saved messages", "send", "Saved Messages"),
    ("draft an email to antonis@gmu.edu", "draft", "antonis@gmu.edu"),
    ("send the email to antonis@gmu.edu", "send", "antonis@gmu.edu"),
    ("post it to my ML channel", "send", "your ML channel"),
    ("save this to a text file", "save", "a text file"),
])
def test_the_verb_and_the_place_are_both_read(said, action, destination):
    got = planning.read_plan(said)
    assert got.action == action
    assert got.destination == destination


def test_the_exact_failure_from_his_log_is_now_caught():
    """
    He said "send them in my saved messages".
    Jalen ran community_post_guide, voice_guide, save_telegram_draft.

    A draft is not a send, and he had to ask twice.
    """
    contract = planning.read_plan(
        "could you please make a post about them and send them in my saved messages")
    what_happened = ["community_post_guide", "voice_guide", "save_telegram_draft"]
    assert contract.betrayed_by(what_happened) == "save_telegram_draft"
    assert contract.betrayed_by(["send_telegram_message"]) == ""


def test_every_forbidden_name_is_a_real_tool():
    """
    A forbidden list with a wrong name in it is a check that passes while the
    bug happens. This caught one: the first version said
    "draft_telegram_post", which exists, and is not what ran.
    """
    from jarvis import tools

    named = {t for names in planning.ACTIONS.values() for t in names}
    assert not named - set(tools.REGISTRY), (
        f"these are not real tools: {sorted(named - set(tools.REGISTRY))}"
    )


def test_half_a_request_is_not_a_contract():
    """
    "Send this" with no destination is him trusting Jalen to choose. Holding
    it to a guess would be worse than the guess.
    """
    vague = planning.read_plan("send this")
    assert not vague.specific
    assert vague.betrayed_by(["save_draft_text"]) == ""


def test_the_first_verb_wins():
    """
    "Draft it and send it later" is a draft. "Send me a draft" is a send.
    Whichever he led with is the request.
    """
    assert planning.read_plan("draft it and send it later to my channel").action == "draft"
    assert planning.read_plan("send me a draft to my channel").action == "send"


def test_the_complaint_names_what_he_asked_for():
    contract = planning.read_plan("send it to my saved messages")
    said = planning.complaint(contract, "save_telegram_draft")
    assert "you said send" in said
    assert "Saved Messages" in said
    assert "save_telegram_draft" in said


# ---------------------------------------------------------------------------
# REQUIREMENT 11 — "it", "there", and "yes go on"
# ---------------------------------------------------------------------------
def test_it_expands_to_what_we_were_talking_about():
    c.remember_subject(thing="the draft to Antonis", place="Saved Messages")
    assert c.expand_references("send it there") == \
        "send the draft to Antonis to Saved Messages"


def test_nothing_is_invented_when_there_is_no_subject():
    """
    A wrong expansion sends the right message to the wrong place, which is
    worse than asking. With no remembered subject the sentence is untouched.
    """
    assert c.expand_references("send it there") == "send it there"


def test_a_credential_never_becomes_the_subject():
    """
    "it" must never expand into a password. This is held in a process that
    writes an audit log.
    """
    c.remember_subject(thing="my password is hunter2")
    assert c.current_subject() is None
    assert c.expand_references("send it") == "send it"


def test_context_expires():
    """
    "Yes" tomorrow morning must not agree to yesterday's email.
    """
    c.remember_subject(thing="the draft")
    assert c.current_subject() is not None
    c._subject.at -= c.CONTEXT_LIFE_S + 1
    assert c.current_subject() is None


def test_yes_agrees_to_the_thing_that_was_offered():
    c.propose(action="send", destination="Saved Messages",
              summary="the post about the opportunity")
    offered = c.last_proposal()
    assert offered is not None
    assert "send" in offered.describe()
    assert "Saved Messages" in offered.describe()


@pytest.mark.parametrize("said", [
    "yes go on", "go on", "continue", "carry on", "keep going",
    "yes, go ahead", "please continue", "proceed", "what next",
])
def test_continuation_is_recognised(said):
    assert c.is_continuation_request(said)


@pytest.mark.parametrize("said", [
    "send it to saved messages", "yes", "no", "open chrome",
    "go on holiday", "continue the meeting notes",
])
def test_a_real_request_is_not_mistaken_for_continuation(said):
    """
    "Go on holiday" is not "go on". Swallowing a real request as a
    continuation is the same class of bug in the other direction.
    """
    assert not c.is_continuation_request(said)


# ---------------------------------------------------------------------------
# REQUIREMENT 6C — tasks that do not disappear
# ---------------------------------------------------------------------------
def test_a_task_can_be_asked_about():
    task_id = c.start_task("sorting your machine learning emails")
    c.update_task(task_id, state=c.EXECUTING, progress="reading email 12 of 40")
    said = c.describe_progress()
    assert "machine learning emails" in said
    assert "12 of 40" in said


def test_nothing_running_says_so_plainly():
    assert "Nothing running" in c.describe_progress()


def test_a_finished_task_is_still_reportable():
    task_id = c.start_task("drafting the reply to Antonis")
    c.update_task(task_id, state=c.COMPLETED)
    said = c.describe_progress()
    assert "last finished" in said
    assert "Antonis" in said


def test_waiting_says_what_it_is_waiting_for():
    task_id = c.start_task("asking Gemini about the richest people")
    c.update_task(task_id, state=c.WAITING_FOR_EXTERNAL_AI, waiting="Gemini")
    assert "waiting for Gemini" in c.describe_progress()


def test_with_two_tasks_it_asks_which_rather_than_guessing():
    """
    His spec is explicit about this. Guessing between two live tasks is how
    "cancel that" cancels the wrong one.
    """
    c.start_task("sorting your emails")
    c.start_task("asking Gemini about the richest people")
    answer = c.which_task("")
    assert isinstance(answer, str)
    assert "Which one" in answer


def test_with_two_tasks_a_clue_in_the_words_picks_one():
    c.start_task("sorting your emails")
    gemini = c.start_task("asking Gemini about the richest people")
    chosen = c.which_task("cancel the gemini one")
    assert isinstance(chosen, c.Task)
    assert chosen.id == gemini


def test_with_one_task_it_does_not_ask():
    task_id = c.start_task("sorting your emails")
    chosen = c.which_task("")
    assert isinstance(chosen, c.Task) and chosen.id == task_id


def test_a_failed_task_keeps_its_error():
    task_id = c.start_task("opening the browser")
    c.update_task(task_id, state=c.FAILED, error="Chrome wouldn't start")
    assert c.get_task(task_id).error == "Chrome wouldn't start"
    assert not c.get_task(task_id).live


def test_the_task_list_stays_bounded():
    """This is the present tense, not a history."""
    for n in range(60):
        task_id = c.start_task(f"thing {n}")
        c.update_task(task_id, state=c.COMPLETED)
    assert len(c.all_tasks()) <= 40


# ---------------------------------------------------------------------------
# The wiring, asserted where it is easy to cut
# ---------------------------------------------------------------------------
def test_references_are_expanded_before_anything_routes():
    """
    "send it to Saved Messages" reaching the router as literally "it" is how
    a request becomes a guess.
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen.process)
    expand = source.index("expand_references")
    route = source.index("self.router.route")
    assert expand < route, "references are expanded after routing - too late"


def test_the_plan_is_checked_after_the_turn():
    import inspect

    from jarvis.app import Jalen

    assert "_check_the_plan" in inspect.getsource(Jalen.run)


def test_a_mismatch_is_reported_and_not_silently_re_run():
    """
    Automatically re-running it as a send would be a second guess on top of
    the first. The one thing worse than doing the wrong thing is doing it
    twice.
    """
    import inspect

    from jarvis.app import Jalen

    source = inspect.getsource(Jalen._check_the_plan)
    assert "self.say(" in source
    assert "systools.call" not in source
