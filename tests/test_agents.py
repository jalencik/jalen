"""
Handing work to another AI — Gemini, ChatGPT — and refusing to send rubbish.

His complaint had two halves and they need different kinds of fix:

    "it still cannot hand this task off to gemini and chatgpt"
        -> plumbing. Just missing code.

    "it should have given a master prompt like a prompt engineer with at
     least 10 years of experience"
        -> quality, which cannot be fixed by hoping.

The second is what most of this file is about. Telling a model "write a good
brief" produces one about a third of the time. So the standard is written
down AND enforced: delegate_task refuses a thin brief and names the missing
part. A gate beats an instruction, and the tests below are on the gate.
"""
from __future__ import annotations

import json

import pytest

from jarvis.tools import agents


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch his real conversations."""
    monkeypatch.setattr(agents, "CHATS_PATH", tmp_path / "chats.json")
    return tmp_path


GOOD_BRIEF = """\
CONTEXT: a small Python assistant on Windows; there is currently no way to
check whether a URL is reachable.
OBJECTIVE: one function that reports whether a website is up.
WHAT SUCCESS LOOKS LIKE: named check_url, returns a short sentence, never
raises, handles timeouts, standard library only, under 25 lines.
CONSTRAINTS: do not use requests, do not print anything, must not add a
__main__ block.
IF ANYTHING IS UNCLEAR: ask rather than guess.
OUTPUT FORMAT: the function and nothing else.
"""


@pytest.fixture()
def stub(monkeypatch):
    """A stand-in agent that records what it was sent."""
    seen = []

    def fake(messages):
        seen.append(list(messages))
        return f"ANSWER {len(seen)}", None

    monkeypatch.setitem(agents.AGENTS, "gemini", fake)
    return seen


# ---------------------------------------------------------------------------
# THE GATE. This is the "10 years of experience" part.
# ---------------------------------------------------------------------------
def test_a_two_word_brief_is_refused(stub):
    out = agents.delegate_task("gemini", "make me a website")
    assert "isn't ready" in out
    assert stub == [], "it sent the rubbish anyway"


@pytest.mark.parametrize("missing, brief", [
    ("SUCCESS", "Context: a python app that currently has no url checker. "
                "Do not use requests. Write me something that checks urls "
                "and please make sure it is a reasonably long request so the "
                "word count alone does not fail it, right now nothing exists."),
    ("CONTEXT", "Objective: write check_url. Success looks like a function "
                "that never raises and handles timeouts. Do not use requests "
                "and do not print, keep it under twenty five lines please "
                "and return only the function body itself."),
    ("CONSTRAINTS", "Context: a python assistant that currently has no url "
                    "checker at all. Objective: write one. Success looks like "
                    "a function named check_url which returns a sentence and "
                    "handles timeouts gracefully every single time."),
])
def test_each_missing_section_is_named(stub, missing, brief):
    """
    Not just "no". Naming the missing part is the difference between a gate
    that teaches and one that annoys.
    """
    out = agents.delegate_task("gemini", brief)
    assert "isn't ready" in out
    assert missing in out, f"refused without saying {missing} was missing: {out}"
    assert stub == []


def test_a_proper_brief_goes_through(stub):
    out = agents.delegate_task("gemini", GOOD_BRIEF, expectation="a url checker")
    assert "isn't ready" not in out
    assert "ANSWER 1" in out
    assert len(stub) == 1


def test_the_standard_names_every_section():
    guide = agents.master_prompt_guide()
    for section in ("CONTEXT", "OBJECTIVE", "WHAT SUCCESS LOOKS LIKE",
                    "CONSTRAINTS", "UNCLEAR", "OUTPUT FORMAT"):
        assert section in guide
    # And the rule that actually changes behaviour.
    assert "CRITERIA before you write the request" in guide


def test_the_gate_matches_the_standard():
    """
    The guide and the gate must ask for the same things. If they drift, he
    follows the guide and is refused anyway — which is the most infuriating
    possible failure.
    """
    guide = agents.master_prompt_guide()
    problems = agents._brief_problems("x")
    for word in ("SUCCESS", "CONTEXT", "CONSTRAINTS"):
        assert word in guide
        assert any(word in p for p in problems), (
            f"the gate checks for {word} but the guide never asks for it"
        )


# ---------------------------------------------------------------------------
# The loop he asked for.
# ---------------------------------------------------------------------------
def test_a_follow_up_carries_the_whole_history(stub):
    """
    "give it another prompt that will fix it" — a REPLY, not a fresh start.
    Starting over is how "fix this one thing" becomes a different answer with
    new problems.
    """
    agents.delegate_task("gemini", GOOD_BRIEF)
    agents.follow_up_task("", "Add a timeout.")

    assert len(stub) == 2
    second = stub[1]
    assert len(second) == 3, "the follow-up did not carry the conversation"
    assert second[0]["content"] == GOOD_BRIEF
    assert second[1]["role"] == "assistant"
    assert second[2]["content"] == "Add a timeout."


def test_rounds_are_counted(stub):
    agents.delegate_task("gemini", GOOD_BRIEF)
    agents.follow_up_task("", "again")
    agents.follow_up_task("", "and again")
    chat = list(agents._load().values())[0]
    assert chat["rounds"] == 3
    assert len(chat["messages"]) == 6


def test_a_failed_follow_up_does_not_corrupt_the_history(monkeypatch):
    """
    The user message must not be left in the history when the call failed —
    the next round would replay a question the other model never saw.
    """
    monkeypatch.setitem(agents.AGENTS, "gemini",
                        lambda m: ("first answer", None))
    agents.delegate_task("gemini", GOOD_BRIEF)
    monkeypatch.setitem(agents.AGENTS, "gemini",
                        lambda m: ("", "network is down"))

    out = agents.follow_up_task("", "fix it")
    assert "network is down" in out
    chat = list(agents._load().values())[0]
    assert len(chat["messages"]) == 2, "a phantom message was left behind"


# ---------------------------------------------------------------------------
# The review. Evidence, not a verdict.
# ---------------------------------------------------------------------------
def test_the_review_lays_out_the_evidence(stub):
    agents.delegate_task("gemini", GOOD_BRIEF,
                         expectation="a stdlib url checker that never raises")
    review = agents.review_delegation()
    assert "a stdlib url checker that never raises" in review
    assert "WHAT WAS ASKED" in review
    assert "WHAT CAME BACK" in review
    assert "NOW JUDGE IT" in review


def test_the_review_does_not_invent_a_percentage(stub):
    """
    He asked for "how many percent of my expectations has been met". That
    number must come from the brain with his request in front of it, not from
    code counting keywords — a confident 90% is exactly what people stop
    checking.
    """
    agents.delegate_task("gemini", GOOD_BRIEF)
    review = agents.review_delegation()
    before_verdict = review.split("NOW JUDGE IT")[0]
    assert "%" not in before_verdict


def test_the_review_asks_for_the_follow_up_to_be_written(stub):
    """The loop only closes if the review ends by producing the next prompt."""
    agents.delegate_task("gemini", GOOD_BRIEF)
    review = agents.review_delegation()
    assert "WRITE THE FOLLOW-UP PROMPT" in review
    assert "follow_up_task" in review


def test_what_he_wanted_is_recorded_before_it_is_sent(stub):
    """
    Judging afterwards from the agent's own answer is marking its homework
    against its own answer sheet.
    """
    agents.delegate_task("gemini", GOOD_BRIEF, expectation="THE ORIGINAL WANT")
    chat = list(agents._load().values())[0]
    assert chat["expectation"] == "THE ORIGINAL WANT"


# ---------------------------------------------------------------------------
# Failure modes, told honestly.
# ---------------------------------------------------------------------------
def test_an_unknown_agent_says_what_it_can_reach():
    out = agents.delegate_task("bard", GOOD_BRIEF)
    assert "gemini" in out and "chatgpt" in out


def test_a_missing_openai_key_offers_the_alternative(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    answer, error = agents._ask_chatgpt([{"role": "user", "content": "hi"}])
    assert answer == ""
    assert "OPENAI_API_KEY" in error
    assert "cowork" in error, "it refused without offering the route that works"


def test_a_missing_gemini_key_says_where_to_get_one(monkeypatch):
    from jarvis import config

    monkeypatch.setattr(config.SECRETS, "gemini_api_key", "")
    answer, error = agents._ask_gemini([{"role": "user", "content": "hi"}])
    assert answer == ""
    assert "aistudio.google.com" in error


def test_model_names_are_aliases_not_pinned_versions():
    """
    The first real call failed with "gemini-2.5-flash is no longer available
    to new users, please update your code". A pinned name is a time bomb that
    goes off in HIS hands rather than in a test.
    """
    assert agents.GEMINI_MODEL == "gemini-flash-latest"


def test_a_corrupt_store_is_not_fatal(isolated):
    agents.CHATS_PATH.write_text("{not json", encoding="utf-8")
    assert agents._load() == {}
    assert "haven't handed anything" in agents.list_delegations()
    assert "nothing delegated" in agents.review_delegation()


def test_the_store_is_written_atomically(stub):
    agents.delegate_task("gemini", GOOD_BRIEF)
    assert not list(agents.CHATS_PATH.parent.glob("*.tmp"))
    assert json.loads(agents.CHATS_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Reachability and gating.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name, tier", [
    ("master_prompt_guide", "green"),
    ("list_delegations", "green"),
    ("review_delegation", "green"),
    ("delegate_task", "amber"),
    ("follow_up_task", "amber"),
])
def test_the_tools_are_dispatchable_and_gated(name, tier):
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    assert name in tools.REGISTRY
    assert name in TOOL_SPECS
    assert SafetyEngine(CONFIG).classify(name, {}).tier.value == tier


def test_something_it_read_cannot_send_his_words_to_a_third_party():
    """
    An email saying "ask ChatGPT about this" must not put his data into
    somebody else's API. AMBER is refused to content-derived requests.
    """
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    verdict = SafetyEngine(CONFIG).classify(
        "delegate_task", {"agent": "gemini", "brief": "x"}, origin="content",
    )
    assert verdict.tier.value == "black"
