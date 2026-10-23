"""
How MANY, and who can lift it.

safety.py decides what KIND of thing is allowed; nothing used to count. A
looping model, a Telegram flood or a microphone that heard its own voice could
send fifty messages or start a dozen coding agents, each one "allowed". These
tests pin the mechanism in jalen/limits.py:

  * a cap asks HIM, and his yes lets one action through - it does not raise
    the cap, and a no (or silence) ends the asking until he speaks again;
  * `refuse` never asks, and `max: 0` switches a whole kind of action off;
  * the leash, the brain-turn cap and the lockdown;
  * the lockdown is a file whose PRESENCE means locked, so a corrupt one fails
    closed, and a lock that cannot be proven lifted stays on;
  * the shipped numbers in config/safety.yaml parse, name real tools, and a
    rule that cannot be read is reported, never silently dropped.

The wiring into the two gates (router and brain hook) is in
test_limits_are_enforced_on_both_paths.py.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from jalen.app import ConfirmAnswer
from jalen.config import CONFIG
from jalen.limits import Admission, Governor, Rule
from jalen.safety import SafetyEngine

HOUR = 3600.0


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Asker:
    """A confirm() that records every question and answers from a script."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def __call__(self, question: str):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else ConfirmAnswer("no")


YES = ConfirmAnswer("yes", "yes")
NO = ConfirmAnswer("no", "no")
TIMEOUT = ConfirmAnswer("timeout")

ACTING = {"send_email", "close_app", "type_text", "delete_file", "cancel_task", "send_sticker"}


def v(tool: str, summary: str = "") -> SimpleNamespace:
    return SimpleNamespace(tool=tool, summary=summary or f"do {tool}")


def governor(*rules: Rule, clock=None, **kwargs) -> Governor:
    return Governor(rules, acting=lambda tool: tool in ACTING,
                    clock=clock or Clock(), **kwargs)


def emails(max_: int = 2, **kw) -> Rule:
    return Rule("emails", frozenset({"send_email"}), max_, HOUR, noun="emails", **kw)


def run(coro):
    return asyncio.run(coro)


def use_up(gov: Governor, tool: str, times: int) -> None:
    for _ in range(times):
        gov.record(tool)


# --------------------------------------------------------------------------
# THE SHIPPED NUMBERS
# --------------------------------------------------------------------------
def test_the_shipped_limits_parse_without_a_single_problem():
    engine = SafetyEngine(CONFIG)
    assert engine.governor.problems == []
    assert engine.governor.enabled
    assert engine.governor.usage(), "no rules loaded - the file would read as limited and not be"


def test_every_tool_a_shipped_rule_names_is_a_real_classified_tool():
    """A typo in a rule is a rule that never fires: it reads as protection and is not."""
    engine = SafetyEngine(CONFIG)
    unknown = sorted({t for rule in engine.governor._rules for t in rule.tools
                      if t not in engine._tier_map})
    assert not unknown, f"limits name tools safety.yaml has never heard of: {unknown}"


def test_no_tool_is_on_the_lockdown_exemption_list_unless_it_only_stops_work():
    engine = SafetyEngine(CONFIG)
    assert engine.governor._still_allowed == frozenset({"cancel_task"})
    for tool in engine.governor._still_allowed:
        assert tool in engine._tier_map, tool


def test_the_money_rule_refuses_rather_than_asks():
    """The money note in safety.yaml: raising it is an edit to the file, in daylight."""
    rows = {r["name"]: r for r in SafetyEngine(CONFIG).governor.usage()}
    assert rows["money"]["on_limit"] == "refuse"


# --------------------------------------------------------------------------
# A RULE THAT CANNOT BE READ IS SAID, NEVER DROPPED
# --------------------------------------------------------------------------
GOOD = {"name": "ok", "tools": ["send_email"], "max": 3, "window_s": 60}


@pytest.mark.parametrize("bad", [
    {"tools": ["send_email"], "max": 3, "window_s": 60},                       # no name
    {"name": "x", "tools": "send_email", "max": 3, "window_s": 60},            # not a list
    {"name": "x", "tools": [], "max": 3, "window_s": 60},                      # empty list
    {"name": "x", "tools": ["send_email"], "max": "lots", "window_s": 60},     # not a number
    {"name": "x", "tools": ["send_email"], "max": -1, "window_s": 60},         # below zero
    {"name": "x", "tools": ["send_email"], "max": 3, "window_s": 0},           # no window
    {"name": "x", "tools": ["send_email"], "max": 3, "window_s": 60, "on_limit": "shrug"},
    {"name": "x", "tools": ["send_email"], "max": True, "window_s": 60},       # a bool is not a count
    "not a mapping",
])
def test_a_malformed_rule_is_reported_and_the_good_ones_still_load(bad):
    gov = Governor.from_config({"rules": [bad, GOOD]})
    assert gov.problems, f"{bad!r} vanished without a word"
    assert [r["name"] for r in gov.usage()] == ["ok"]


def test_a_repeated_rule_name_is_reported():
    gov = Governor.from_config({"rules": [GOOD, dict(GOOD, tools=["close_app"])]})
    assert len(gov.problems) == 1 and "repeats" in gov.problems[0]
    assert [r["name"] for r in gov.usage()] == ["ok"]


def test_a_block_that_is_not_a_mapping_is_reported_and_leaves_no_rules():
    gov = Governor.from_config(["nonsense"])
    assert gov.problems and not gov.enabled and gov.usage() == []


def test_no_limits_block_means_no_caps_but_the_lockdown_still_works():
    gov = Governor.from_config(None, acting=lambda t: True)
    assert not gov.enabled and gov.problems == []
    assert run(gov.admit(v("send_email"), Asker())).allowed
    gov.lock("test")
    assert not run(gov.admit(v("send_email"), Asker())).allowed


def test_max_zero_with_refuse_switches_a_whole_kind_of_action_off():
    gov = Governor.from_config({"rules": [dict(GOOD, max=0, on_limit="refuse")]},
                               acting=lambda t: True)
    asker = Asker(YES)
    out = run(gov.admit(v("send_email"), asker))
    assert not out.allowed and out.outcome == "refused"
    assert asker.questions == []


# --------------------------------------------------------------------------
# A CAP ASKS HIM - and only his own yes lifts it, for one action
# --------------------------------------------------------------------------
def test_under_the_cap_nothing_is_asked():
    gov = governor(emails(3))
    asker = Asker()
    use_up(gov, "send_email", 2)
    assert run(gov.admit(v("send_email"), asker)).allowed
    assert asker.questions == []


def test_at_the_cap_he_is_asked_and_his_yes_lets_ONE_action_through():
    gov = governor(emails(2))
    use_up(gov, "send_email", 2)
    asker = Asker(YES, YES)

    first = run(gov.admit(v("send_email", "send email: a@b.com"), asker))
    assert first.allowed and first.asked
    gov.record("send_email")

    # His yes did not raise the cap: the next one asks again.
    second = run(gov.admit(v("send_email", "send email: c@d.com"), asker))
    assert second.allowed and len(asker.questions) == 2


def test_the_question_names_the_action_the_limit_and_ends_the_way_every_red_question_does():
    gov = governor(emails(2))
    use_up(gov, "send_email", 2)
    asker = Asker(YES)
    run(gov.admit(v("send_email", "send email: a@b.com"), asker))
    (question,) = asker.questions
    assert "send email: a@b.com" in question
    assert "emails" in question and "2" in question and "hour" in question
    # "Confirm?" is what the echo defence in Jalen._echoes_the_confirmation
    # was built and measured around; a new ending would be a new hole.
    assert question.endswith("Confirm?")


def test_past_the_cap_the_question_says_how_far_past():
    gov = governor(emails(2))
    use_up(gov, "send_email", 5)
    asker = Asker(YES)
    run(gov.admit(v("send_email"), asker))
    assert "past your limit" in asker.questions[0] and "5" in asker.questions[0]


def test_his_no_ends_the_asking_until_he_speaks_again():
    """A model that retries must not turn a limit into a stream of questions."""
    gov = governor(emails(1))
    use_up(gov, "send_email", 1)
    asker = Asker(NO, YES)

    first = run(gov.admit(v("send_email"), asker))
    assert not first.allowed and first.outcome == "declined"

    again = run(gov.admit(v("send_email"), asker))
    assert not again.allowed and again.outcome == "declined"
    assert len(asker.questions) == 1, "asked again within the same request"

    gov.new_instruction()                      # he said something new
    third = run(gov.admit(v("send_email"), asker))
    assert third.allowed and len(asker.questions) == 2


def test_silence_is_a_no_and_the_model_is_not_told_he_refused():
    gov = governor(emails(1))
    use_up(gov, "send_email", 1)
    out = run(gov.admit(v("send_email"), Asker(TIMEOUT)))
    assert not out.allowed and out.outcome == "timeout"
    assert "did not answer" in out.for_model.lower()
    assert "refused" not in out.for_model.lower().replace("do not say he refused", "")


def test_a_correction_is_handed_back_and_is_not_a_yes():
    gov = governor(emails(1))
    use_up(gov, "send_email", 1)
    out = run(gov.admit(v("send_email"), Asker(ConfirmAnswer("correction", "send it to Rodion instead"))))
    assert not out.allowed
    assert "send it to Rodion instead" in out.for_model


def test_a_bare_true_from_an_older_caller_is_a_yes_and_a_bare_false_is_a_no():
    gov = governor(emails(1))
    use_up(gov, "send_email", 1)
    assert run(gov.admit(v("send_email"), Asker(True))).allowed
    gov.new_instruction()
    assert not run(gov.admit(v("send_email"), Asker(False))).allowed


def test_not_being_able_to_ask_is_a_no():
    gov = governor(emails(1))
    use_up(gov, "send_email", 1)

    async def broken(question):
        raise RuntimeError("no voice session")

    out = run(gov.admit(v("send_email"), broken))
    assert not out.allowed
    assert "no voice session" in gov.last_error


def test_a_cancelled_action_does_not_use_up_a_limit():
    """Counting happens in record(), which the gates call only when the action runs."""
    gov = governor(emails(2))
    for _ in range(5):
        assert run(gov.admit(v("send_email"), Asker())).allowed
    assert gov.usage()[0]["used"] == 0


def test_the_window_slides():
    clock = Clock()
    gov = governor(emails(2), clock=clock)
    use_up(gov, "send_email", 2)
    assert not run(gov.admit(v("send_email"), Asker(NO))).allowed
    gov.new_instruction()
    clock.advance(HOUR + 1)
    assert run(gov.admit(v("send_email"), Asker())).allowed
    assert gov.usage()[0]["used"] == 0


def test_a_tool_in_no_rule_is_never_asked_about():
    gov = governor(emails(1))
    use_up(gov, "send_email", 9)
    assert run(gov.admit(v("close_app"), Asker())).allowed


def test_refuse_never_asks_and_tells_him_where_the_number_lives():
    gov = governor(emails(1, on_limit="refuse"))
    use_up(gov, "send_email", 1)
    asker = Asker(YES)
    out = run(gov.admit(v("send_email"), asker))
    assert not out.allowed and out.outcome == "refused"
    assert asker.questions == []
    assert "safety.yaml" in out.for_model


def test_a_hard_limit_is_not_softened_by_an_ask_limit_on_the_same_tool():
    soft = Rule("soft", frozenset({"send_email"}), 1, HOUR, noun="soft")
    hard = Rule("hard", frozenset({"send_email"}), 1, HOUR, on_limit="refuse", noun="hard")
    gov = governor(soft, hard)
    use_up(gov, "send_email", 1)
    asker = Asker(YES)
    out = run(gov.admit(v("send_email"), asker))
    assert out.outcome == "refused" and asker.questions == []


def test_two_ask_limits_both_have_to_be_answered():
    a = Rule("a", frozenset({"send_email"}), 1, HOUR, noun="a-things")
    b = Rule("b", frozenset({"send_email"}), 1, HOUR, noun="b-things")
    gov = governor(a, b)
    use_up(gov, "send_email", 1)
    asker = Asker(YES, NO)
    assert not run(gov.admit(v("send_email"), asker)).allowed
    assert len(asker.questions) == 2


# --------------------------------------------------------------------------
# THE LEASH
# --------------------------------------------------------------------------
def test_the_leash_counts_acting_calls_and_not_reads():
    gov = governor(leash=3)
    use_up(gov, "read_file", 50)                 # not acting
    asker = Asker()
    assert run(gov.admit(v("close_app"), asker)).allowed
    assert asker.questions == []


def test_past_the_leash_he_is_asked_and_a_yes_buys_another_batch():
    gov = governor(leash=3)
    use_up(gov, "close_app", 3)
    asker = Asker(YES)
    out = run(gov.admit(v("close_app", "close app notepad"), asker))
    assert out.allowed and len(asker.questions) == 1
    assert "3 actions" in asker.questions[0] and "close app notepad" in asker.questions[0]
    assert asker.questions[0].endswith("Confirm?")
    # The batch restarted, so the next ones are not asked about.
    gov.record("close_app")
    assert run(gov.admit(v("close_app"), Asker())).allowed


def test_a_new_instruction_resets_the_leash():
    gov = governor(leash=2)
    use_up(gov, "close_app", 2)
    gov.new_instruction()
    assert run(gov.admit(v("close_app"), Asker())).allowed


def test_his_no_to_the_leash_stops_the_rest_of_the_request():
    gov = governor(leash=2)
    use_up(gov, "close_app", 2)
    asker = Asker(NO)
    assert not run(gov.admit(v("close_app"), asker)).allowed
    assert not run(gov.admit(v("type_text"), asker)).allowed
    assert len(asker.questions) == 1


# --------------------------------------------------------------------------
# BRAIN TURNS
# --------------------------------------------------------------------------
def brain_governor(max_: int = 2, **kw) -> Governor:
    return Governor.from_config(
        {"brain_turns": {"max": max_, "window_s": HOUR, **kw}},
        acting=lambda t: True, clock=Clock())


def test_brain_turns_under_the_cap_run_freely():
    gov = brain_governor(2)
    asker = Asker()
    gov.record_brain_turn()
    assert run(gov.admit_brain_turn(asker)).allowed and asker.questions == []


def test_a_brain_turn_past_the_cap_asks_and_his_yes_buys_that_one_turn():
    gov = brain_governor(2)
    gov.record_brain_turn()
    gov.record_brain_turn()
    asker = Asker(YES, NO)
    assert run(gov.admit_brain_turn(asker)).allowed
    gov.record_brain_turn()
    out = run(gov.admit_brain_turn(asker))
    assert not out.allowed and len(asker.questions) == 2
    assert "thinking turns" in asker.questions[0] and asker.questions[0].endswith("Confirm?")


def test_a_brain_turn_cap_set_to_refuse_never_asks():
    gov = brain_governor(1, on_limit="refuse")
    gov.record_brain_turn()
    asker = Asker(YES)
    assert not run(gov.admit_brain_turn(asker)).allowed and asker.questions == []


def test_with_no_brain_rule_every_turn_is_free():
    assert run(governor().admit_brain_turn(Asker())).allowed


# --------------------------------------------------------------------------
# LOCKDOWN
# --------------------------------------------------------------------------
def test_lockdown_refuses_every_acting_tool_and_lets_reading_through():
    gov = governor()
    gov.lock("test")
    asker = Asker(YES)
    for tool in ("close_app", "type_text", "send_email"):
        out = run(gov.admit(v(tool), asker))
        assert not out.allowed and out.outcome == "locked", tool
    assert run(gov.admit(v("read_file"), asker)).allowed
    assert asker.questions == [], "a lockdown is not a question"


def test_the_model_is_told_not_to_look_for_another_way():
    gov = governor()
    gov.lock()
    out = run(gov.admit(v("close_app"), Asker()))
    assert "do not retry" in out.for_model.lower() and "only he can lift" in out.for_model.lower()


def test_stopping_work_is_still_allowed_in_lockdown():
    gov = governor(still_allowed=["cancel_task"])
    gov.lock()
    assert run(gov.admit(v("cancel_task"), Asker())).allowed
    assert not run(gov.admit(v("close_app"), Asker())).allowed


def test_lockdown_beats_every_limit_and_unlocking_gives_the_tools_back():
    gov = governor(emails(1))
    gov.lock()
    assert not run(gov.admit(v("send_email"), Asker(YES))).allowed
    assert gov.unlock()
    assert not gov.locked
    assert run(gov.admit(v("send_email"), Asker())).allowed


def test_the_lock_is_a_file_and_survives_a_restart(tmp_path):
    lock = tmp_path / "jalen-lockdown.json"
    first = governor()
    first.attach(tmp_path / "usage.json", lock)
    assert first.lock("he said so") is True
    assert lock.exists()

    second = governor()
    second.attach(tmp_path / "usage.json", lock)
    assert second.locked
    assert not run(second.admit(v("close_app"), Asker())).allowed

    assert second.unlock()
    assert not lock.exists()
    third = governor()
    third.attach(tmp_path / "usage.json", lock)
    assert not third.locked


@pytest.mark.parametrize("content", ["", "{not json", "[1, 2]", "null", "\x00\x01"])
def test_a_corrupt_lock_file_still_means_locked(tmp_path, content):
    """PRESENT MEANS LOCKED. Half-written is the likeliest way it gets damaged."""
    lock = tmp_path / "jalen-lockdown.json"
    lock.write_text(content, encoding="utf-8")
    gov = governor()
    gov.attach(tmp_path / "usage.json", lock)
    assert gov.locked
    assert not run(gov.admit(v("close_app"), Asker())).allowed


def test_a_lock_that_cannot_be_removed_stays_on(tmp_path, monkeypatch):
    lock = tmp_path / "jalen-lockdown.json"
    gov = governor()
    gov.attach(tmp_path / "usage.json", lock)
    gov.lock()

    def refuse(self, missing_ok=False):
        raise PermissionError("held open")

    monkeypatch.setattr(type(lock), "unlink", refuse)
    assert gov.unlock() is False
    assert gov.locked, "unlock() reported failure but let go of the lock"


def test_a_lock_that_cannot_be_written_holds_for_the_run_and_says_so(tmp_path, monkeypatch):
    gov = governor()
    gov.attach(tmp_path / "usage.json", tmp_path / "no" / "such" / "jalen-lockdown.json")
    errors = []
    gov._on_error = errors.append

    def broken(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(type(tmp_path), "write_text", broken)
    assert gov.lock() is False
    assert gov.locked and errors


def test_unlock_forgets_a_no_he_said_before():
    gov = governor(emails(1))
    use_up(gov, "send_email", 1)
    run(gov.admit(v("send_email"), Asker(NO)))
    gov.lock()
    gov.unlock()
    assert run(gov.admit(v("send_email"), Asker(YES))).allowed


# --------------------------------------------------------------------------
# WHAT IS REMEMBERED
# --------------------------------------------------------------------------
def test_counts_survive_a_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "usage.json"
    first = governor(emails(2), clock=clock)
    first.attach(path, tmp_path / "lock.json")
    use_up(first, "send_email", 2)

    second = governor(emails(2), clock=clock)
    second.attach(path, tmp_path / "lock.json")
    assert second.usage()[0]["used"] == 2
    assert not run(second.admit(v("send_email"), Asker(NO))).allowed


def test_a_restart_does_not_hand_the_budget_back_but_an_expired_window_does(tmp_path):
    clock = Clock()
    path = tmp_path / "usage.json"
    first = governor(emails(2), clock=clock)
    first.attach(path, tmp_path / "lock.json")
    use_up(first, "send_email", 2)
    clock.advance(HOUR + 5)
    second = governor(emails(2), clock=clock)
    second.attach(path, tmp_path / "lock.json")
    assert second.usage()[0]["used"] == 0


def test_a_corrupt_counts_file_is_set_aside_and_reported_not_overwritten(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("{truncated", encoding="utf-8")
    errors = []
    gov = governor(emails(2))
    gov.attach(path, tmp_path / "lock.json", on_error=errors.append)
    assert errors
    assert gov.usage()[0]["used"] == 0
    assert list(tmp_path.glob("usage.json.corrupt-*")), "the broken file was destroyed"


def test_a_counts_file_that_cannot_be_written_is_reported_and_the_limit_still_holds(tmp_path):
    errors = []
    gov = governor(emails(1))
    gov.attach(tmp_path / "missing" / "dir" / "usage.json", tmp_path / "lock.json",
               on_error=errors.append)
    # Make the parent impossible to create: a FILE where the folder must go.
    (tmp_path / "missing").write_text("in the way", encoding="utf-8")
    gov.record("send_email")
    assert errors, "a write that failed said nothing"
    assert not run(gov.admit(v("send_email"), Asker(NO))).allowed


def test_attach_reads_the_module_paths_at_call_time(tmp_path, monkeypatch):
    """conftest redirects these two attributes; reading them at import would defeat it."""
    from jalen import limits

    monkeypatch.setattr(limits, "STATE_PATH", tmp_path / "u.json")
    monkeypatch.setattr(limits, "LOCK_PATH", tmp_path / "l.json")
    gov = governor(emails(2))
    gov.attach()
    gov.record("send_email")
    gov.lock()
    assert (tmp_path / "u.json").exists() and (tmp_path / "l.json").exists()


def test_the_counts_file_is_bounded(tmp_path):
    from jalen import limits

    gov = governor(Rule("many", frozenset({"close_app"}), 10 ** 9, 10 ** 9))
    gov.attach(tmp_path / "u.json", tmp_path / "l.json")
    for _ in range(limits._MAX_KEPT + 50):
        gov._events.setdefault("many", []).append(gov._clock())
        gov.record("close_app")
    assert len(gov._events["many"]) <= limits._MAX_KEPT


# --------------------------------------------------------------------------
# FAILING CLOSED, AND THE FAST PATH
# --------------------------------------------------------------------------
def test_a_check_that_breaks_refuses_rather_than_lets_it_through():
    gov = governor(emails(5))

    def boom(*a, **k):
        raise ValueError("bug")

    gov._hits = boom
    out = run(gov.admit(v("send_email"), Asker(YES)))
    assert not out.allowed and out.outcome == "refused"
    assert "bug" in gov.last_error
    assert gov.quick(v("send_email")).allowed is False


def test_a_tool_the_engine_cannot_classify_counts_as_acting():
    gov = Governor(acting=lambda tool: 1 / 0, clock=Clock())
    gov.lock()
    assert not run(gov.admit(v("anything"), Asker())).allowed


def test_quick_answers_without_a_question_and_defers_when_one_is_needed():
    gov = governor(emails(1))
    assert gov.quick(v("read_file")) is not None and gov.quick(v("read_file")).allowed
    assert gov.quick(v("send_email")).allowed
    use_up(gov, "send_email", 1)
    assert gov.quick(v("send_email")) is None          # he has to be asked
    gov.lock()
    assert gov.quick(v("send_email")).outcome == "locked"


# --------------------------------------------------------------------------
# WHAT HE HEARS
# --------------------------------------------------------------------------
def test_the_report_names_the_busiest_limits_first():
    gov = governor(emails(10), Rule("closing", frozenset({"close_app"}), 4, 600, noun="windows closed"),
                   leash=40)
    use_up(gov, "send_email", 1)
    use_up(gov, "close_app", 3)
    said = gov.report()
    assert said.index("windows closed") < said.index("emails")
    assert "3 of 4" in said and "10 minutes" in said
    assert "40 actions" in said


def test_the_report_says_so_when_nothing_has_been_used():
    assert "haven't used any" in governor(emails(10)).report()


def test_the_report_says_lockdown_first():
    gov = governor(emails(10))
    gov.lock()
    assert gov.report().startswith("I'm in lockdown")


def test_the_report_says_when_the_limits_are_switched_off():
    gov = Governor.from_config({"enabled": False, "rules": [GOOD]})
    assert "switched off" in gov.report()


# --------------------------------------------------------------------------
# WHAT THE ENGINE CALLS ACTING
# --------------------------------------------------------------------------
@pytest.mark.parametrize("tool,expected", [
    ("read_file", False),            # GREEN, reads
    ("get_time", False),
    ("usage_report", False),
    ("ask_user", False),             # asking him something changes nothing
    ("type_text", True),             # GREEN but on refuse_from_content
    ("close_app", True),
    ("create_file", True),
    ("edit_file", True),             # AMBER
    ("send_email", True),            # RED
    ("made_up_tool_xyz", True),      # unclassified is AMBER, not read-only
    ("transfer_funds", False),       # BLACK never runs
])
def test_what_counts_as_acting(tool, expected):
    assert SafetyEngine(CONFIG).is_acting(tool) is expected


def test_acting_is_judged_from_the_file_not_the_posture():
    engine = SafetyEngine(CONFIG)
    engine.posture = "autonomous"            # turns AMBER into GREEN
    assert engine.is_acting("edit_file") is True


def test_admission_is_immutable_and_ok_is_allowed():
    from jalen import limits

    assert limits.OK.allowed and limits.OK.outcome == "ok"
    with pytest.raises(Exception):
        limits.OK.allowed = False                                 # type: ignore[misc]
    assert isinstance(limits.OK, Admission)


def test_the_counts_file_is_valid_json_of_a_known_shape(tmp_path):
    path = tmp_path / "u.json"
    gov = governor(emails(5))
    gov.attach(path, tmp_path / "l.json")
    gov.record("send_email")
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert blob["v"] == 1 and list(blob["events"]) == ["emails"]
