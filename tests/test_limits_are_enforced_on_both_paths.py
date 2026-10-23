"""
A limit that holds on one path is a limit with a hole in it.

CLAUDE.md: "Two gates decide whether you are heard, and they must agree" -
and the same shape applies here. An action reaches the machine through the
ROUTER (app.handle_local) or through the BRAIN's PreToolUse hook
(agent.Brain._make_hook). jalen/limits.py is enforced in both, through the one
governor that hangs off the one SafetyEngine they share. These tests drive the
real code on each path, then pin the properties that must hold across them:

  * over a cap he is asked, and only his yes runs it - on both paths;
  * a cancelled action does not use up a limit, a RED action asks about the
    limit BEFORE its own "Confirm?", and a refused one never reaches it;
  * lockdown refuses acting tools on both and lets reading through;
  * nothing the model can call raises a limit or lifts the lockdown, and the
    two files that hold the lock and the counts are on the never-touch list;
  * lifting a lockdown needs a sentence that is his: word for word, with his
    name;
  * every brain turn, from any door, passes the brain-turn cap;
  * the spoken phrases reach the right tool, and nothing that used to route
    elsewhere has been taken.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from jalen import app as app_module
from jalen import limits
from jalen.app import ConfirmAnswer, Jalen
from jalen.audit import AuditLog
from jalen.brain.agent import Brain
from jalen.brain.router import Intent, IntentRouter
from jalen.config import CONFIG
from jalen.limits import Governor, Rule
from jalen.safety import SafetyEngine

ROOT = Path(__file__).resolve().parent.parent
HOUR = 3600.0
YES = ConfirmAnswer("yes", "yes")
NO = ConfirmAnswer("no", "no")


class Clock:
    now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def small_governor(engine: SafetyEngine, *rules: Rule, **kwargs) -> Governor:
    return Governor(rules, acting=engine.is_acting, clock=Clock(), **kwargs)


def closing(max_: int = 1) -> Rule:
    return Rule("closing", frozenset({"close_app"}), max_, HOUR, noun="windows closed")


def deleting(max_: int = 1) -> Rule:
    return Rule("deletions", frozenset({"delete_file"}), max_, HOUR, noun="deletions")


# --------------------------------------------------------------------------
# THE ROUTER PATH: Jalen.handle_local
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def the_app():
    j = Jalen()
    j.muted = True
    try:
        yield j
    finally:
        j.shutdown()


@pytest.fixture
def jalen(the_app, monkeypatch):
    """The one real Jalen, with a small governor of the test's own and a spy on every call."""
    the_app.safety.paranoid = False
    the_app.safety.posture = "irreversible_only"
    the_app.safety.governor = small_governor(the_app.safety, closing(1), deleting(1))
    the_app.said = []
    the_app.say = lambda t, force=False: the_app.said.append(t)

    ran = []

    def fake_call(tool, args):
        ran.append((tool, dict(args or {})))
        return f"{tool} done"

    monkeypatch.setattr(app_module.systools, "call", fake_call)
    the_app.ran = ran

    asked = []

    async def scripted_confirm(self, question):
        asked.append(question)
        return the_app.answers.pop(0) if the_app.answers else NO

    the_app.answers = []
    the_app.asked = asked
    monkeypatch.setattr(Jalen, "confirm", scripted_confirm)
    return the_app


def close_notepad(jalen: Jalen):
    return jalen.handle_local(Intent(tool="close_app", args={"name": "notepad"}),
                              his_own_words=True)


def test_under_the_cap_the_router_runs_it_without_a_question(jalen):
    assert close_notepad(jalen) == "close_app done"
    assert jalen.asked == []
    assert jalen.safety.governor.usage()[0]["used"] == 1


def test_over_the_cap_the_router_asks_him_and_runs_it_only_on_his_yes(jalen):
    close_notepad(jalen)
    jalen.answers = [YES]
    assert close_notepad(jalen) == "close_app done"
    assert len(jalen.asked) == 1 and jalen.asked[0].endswith("Confirm?")
    assert "windows closed" in jalen.asked[0]
    assert len(jalen.ran) == 2


def test_over_the_cap_his_no_means_it_does_not_run_and_is_not_counted(jalen):
    close_notepad(jalen)
    jalen.answers = [NO]
    said = close_notepad(jalen)
    assert "Cancelled" in said and "limit" in said
    assert len(jalen.ran) == 1
    assert jalen.safety.governor.usage()[0]["used"] == 1


def test_a_hard_limit_is_refused_by_the_router_without_a_question(jalen):
    jalen.safety.governor = small_governor(
        jalen.safety, Rule("closing", frozenset({"close_app"}), 0, HOUR, on_limit="refuse",
                           noun="windows closed"))
    jalen.answers = [YES]
    said = close_notepad(jalen)
    assert "won't" in said and jalen.asked == [] and jalen.ran == []


def test_a_red_action_past_a_limit_is_asked_about_once_naming_the_action_and_the_limit(jalen):
    """
    The limit's question names the action, so his yes to it stands in for the
    plain "Confirm?" - never for the consequence question a dangerous action
    asks after it (`consequences:` in safety.yaml; deleting a file is one).
    """
    jalen.safety.governor.record("delete_file")
    jalen.answers = [YES, YES]
    jalen.handle_local(Intent(tool="delete_file", args={"path": "C:/tmp/doesnotmatter.txt"}),
                       his_own_words=True)
    limit_question, consequence = jalen.asked
    assert limit_question.startswith("delete file: C:/tmp/doesnotmatter.txt")
    assert "deletions" in limit_question and limit_question.endswith("Confirm?")
    assert "deleted for good" in consequence and consequence.endswith("Confirm?")
    assert [t for t, _ in jalen.ran] == ["delete_file"]


def test_under_the_limit_a_red_action_still_gets_its_own_question(jalen):
    jalen.answers = [YES, YES]
    jalen.handle_local(Intent(tool="delete_file", args={"path": "C:/tmp/doesnotmatter.txt"}),
                       his_own_words=True)
    question, consequence = jalen.asked
    assert question == "delete file: C:/tmp/doesnotmatter.txt. Confirm?"
    assert "deleted for good" in consequence
    assert [t for t, _ in jalen.ran] == ["delete_file"]


def test_an_amber_action_past_a_limit_is_asked_once_and_not_also_announced(jalen, monkeypatch):
    announced = []

    async def announce(self, text):
        announced.append(text)

    monkeypatch.setattr(Jalen, "announce", announce)
    jalen.safety.governor = small_governor(
        jalen.safety, Rule("edits", frozenset({"edit_file"}), 1, HOUR, noun="edits"))
    jalen.safety.governor.record("edit_file")
    jalen.answers = [YES]
    jalen.handle_local(Intent(tool="edit_file", args={"path": "C:/tmp/notes.txt"}),
                       his_own_words=True)
    assert len(jalen.asked) == 1 and announced == []
    assert [t for t, _ in jalen.ran] == ["edit_file"]


def test_an_amber_action_under_the_limit_is_still_announced(jalen, monkeypatch):
    announced = []

    async def announce(self, text):
        announced.append(text)

    monkeypatch.setattr(Jalen, "announce", announce)
    jalen.handle_local(Intent(tool="edit_file", args={"path": "C:/tmp/notes.txt"}),
                       his_own_words=True)
    assert jalen.asked == [] and len(announced) == 1


def test_a_no_to_the_limit_means_the_red_question_is_never_asked(jalen):
    jalen.safety.governor.record("delete_file")
    jalen.answers = [NO]
    jalen.handle_local(Intent(tool="delete_file", args={"path": "C:/tmp/doesnotmatter.txt"}),
                       his_own_words=True)
    assert len(jalen.asked) == 1 and jalen.ran == []


def test_his_no_to_the_one_question_runs_nothing_and_uses_up_nothing(jalen):
    jalen.safety.governor.record("delete_file")
    jalen.answers = [NO]
    said = jalen.handle_local(Intent(tool="delete_file", args={"path": "C:/tmp/doesnotmatter.txt"}),
                              his_own_words=True)
    assert "Cancelled" in said and jalen.ran == []
    assert jalen.safety.governor.usage()[1]["used"] == 1       # only the one recorded above


def test_a_tool_that_does_not_exist_uses_up_nothing(jalen, monkeypatch):
    """A habit recalled after its tool was removed: systools.call raises KeyError and nothing runs."""
    async def announce(self, text):
        return None

    monkeypatch.setattr(Jalen, "announce", announce)
    jalen.safety.governor = small_governor(
        jalen.safety, Rule("ghost", frozenset({"no_such_tool_xyz"}), 5, HOUR))

    def real_dispatch(tool, args):
        raise KeyError(tool)

    monkeypatch.setattr(app_module.systools, "call", real_dispatch)
    assert jalen.handle_local(Intent(tool="no_such_tool_xyz", args={})) is None
    assert jalen.safety.governor.usage()[0]["used"] == 0


def test_the_limit_is_judged_before_a_never_touch_black_verdict_would_be_irrelevant(jalen):
    """A BLACK action is refused by the kind-of-thing gate and never counted or asked about."""
    jalen.answers = [YES]
    said = jalen.handle_local(Intent(tool="delete_file", args={"path": "C:/Windows/system.ini"}),
                              his_own_words=True)
    assert "won't" in said and jalen.asked == [] and jalen.ran == []
    assert jalen.safety.governor.usage()[1]["used"] == 0


def test_lockdown_refuses_an_acting_tool_on_the_router_and_lets_a_read_through(jalen):
    jalen.safety.governor.lock("test")
    refused = close_notepad(jalen)
    assert "lockdown" in refused and jalen.ran == []
    assert jalen.handle_local(Intent(tool="get_time", args={})) == "get_time done"


def test_a_refused_action_leaves_an_audit_line_that_says_why(jalen):
    jalen.safety.governor.lock("test")
    close_notepad(jalen)
    rows = [r for r in jalen.audit.since(1) if r["tool"] == "close_app"]
    assert rows and rows[0]["outcome"] == "blocked"


def test_a_plain_read_never_leaves_the_calling_thread(jalen, monkeypatch):
    """The fast path: nothing near a limit must cost no hop to the event loop."""
    def no_loop(coro):
        coro.close()
        raise AssertionError("a command nothing is limiting went through the event loop")

    monkeypatch.setattr(Jalen, "_run_coro", lambda self, coro: no_loop(coro))
    assert jalen.handle_local(Intent(tool="get_time", args={})) == "get_time done"
    assert close_notepad(jalen) == "close_app done"        # under its cap too


# --------------------------------------------------------------------------
# LIFTING A LOCKDOWN NEEDS A SENTENCE THAT IS HIS
# --------------------------------------------------------------------------
def test_anything_may_lock_but_only_his_own_words_with_his_name_lift(jalen):
    governor = jalen.safety.governor
    lock = Intent(tool="jalen_lockdown", args={})
    lift = Intent(tool="jalen_lift_lockdown", args={})

    # Locking is always the safe direction, so no source is turned away.
    assert "Hands off" in jalen.handle_local(lock, his_own_words=False, from_him=False)
    assert governor.locked

    # Expanded from something read, or let in by a door: not his word for word.
    assert "name" in jalen.handle_local(lift, his_own_words=False, from_him=True)
    assert governor.locked
    # Heard in the question window with no name - might be the television.
    assert "name" in jalen.handle_local(lift, his_own_words=True, from_him=False)
    assert governor.locked

    assert "acting again" in jalen.handle_local(lift, his_own_words=True, from_him=True)
    assert not governor.locked


def test_lifting_when_not_locked_says_so_and_changes_nothing(jalen):
    said = jalen.handle_local(Intent(tool="jalen_lift_lockdown", args={}),
                              his_own_words=True, from_him=True)
    assert "wasn't in lockdown" in said


def test_a_lock_that_could_not_be_written_down_is_said_to_him(jalen):
    governor = jalen.safety.governor
    governor._lock_path = Path("/proc/definitely/not/writable/jalen-lockdown.json")
    said = jalen.handle_local(Intent(tool="jalen_lockdown", args={}))
    assert "couldn't write that down" in said and governor.locked


def test_a_lock_that_cannot_be_cleared_stays_locked_and_says_how_to_clear_it(jalen, tmp_path, monkeypatch):
    governor = jalen.safety.governor
    governor.attach(tmp_path / "u.json", tmp_path / "jalen-lockdown.json")
    governor.lock()
    monkeypatch.setattr(type(tmp_path), "unlink",
                        lambda self, missing_ok=False: (_ for _ in ()).throw(PermissionError("held")))
    said = jalen.handle_local(Intent(tool="jalen_lift_lockdown", args={}),
                              his_own_words=True, from_him=True)
    assert "staying locked" in said and "jalen-lockdown.json" in said
    assert governor.locked


def test_a_lockdown_mentions_coding_jobs_that_keep_running(jalen, monkeypatch):
    from jalen.tools import devwork

    monkeypatch.setattr(devwork, "live_jobs", lambda: [{"id": "a"}, {"id": "b"}])
    said = jalen.handle_local(Intent(tool="jalen_lockdown", args={}))
    assert "2 coding jobs are still running" in said and "cancel the jobs" in said


# --------------------------------------------------------------------------
# THE BRAIN'S HOOK: Brain._make_hook
# --------------------------------------------------------------------------
class Scripted:
    def __init__(self) -> None:
        self.answers: list = []
        self.questions: list[str] = []

    async def confirm(self, question: str):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else NO

    async def announce(self, text: str) -> None:
        return None


@pytest.fixture
def brain():
    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"
    engine.governor = small_governor(engine, closing(1), deleting(1))
    script = Scripted()
    b = Brain(CONFIG, engine, AuditLog(CONFIG, "test-session-limits-hook"),
              confirm=script.confirm, announce=script.announce)
    b.script = script
    return b


def hook_call(brain: Brain, tool: str, args: dict | None = None) -> dict:
    hook = brain._make_hook()
    return asyncio.run(hook({"tool_name": f"mcp__jalen__{tool}", "tool_input": args or {}}, "id", None))


def denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def reason(result: dict) -> str:
    return result["hookSpecificOutput"]["permissionDecisionReason"]


def test_under_the_cap_the_hook_allows_and_counts(brain):
    assert hook_call(brain, "close_app", {"name": "notepad"}) == {}
    assert brain.script.questions == []
    assert brain.safety.governor.usage()[0]["used"] == 1


def test_over_the_cap_the_hook_asks_him_and_allows_only_on_his_yes(brain):
    hook_call(brain, "close_app", {"name": "notepad"})
    brain.script.answers = [YES]
    assert hook_call(brain, "close_app", {"name": "notepad"}) == {}
    assert len(brain.script.questions) == 1 and "windows closed" in brain.script.questions[0]
    assert brain.safety.governor.usage()[0]["used"] == 2


def test_over_the_cap_his_no_denies_and_tells_the_model_not_to_retry(brain):
    hook_call(brain, "close_app", {"name": "notepad"})
    brain.script.answers = [NO]
    result = hook_call(brain, "close_app", {"name": "notepad"})
    assert denied(result) and "retry" in reason(result).lower()
    assert brain.safety.governor.usage()[0]["used"] == 1
    # ...and the model asking again in the same request is not a second question.
    assert denied(hook_call(brain, "close_app", {"name": "notepad"}))
    assert len(brain.script.questions) == 1


def test_the_hook_denies_in_lockdown_and_lets_a_read_through(brain):
    brain.safety.governor.lock("test")
    result = hook_call(brain, "close_app", {"name": "notepad"})
    assert denied(result) and "lockdown" in reason(result).lower()
    assert hook_call(brain, "read_file", {"path": "C:/tmp/x.txt"}) == {}
    assert brain.script.questions == []


def test_a_red_action_past_a_limit_is_asked_about_once_by_the_hook(brain):
    """The limit's yes stands in for "Confirm?", not for the consequence question."""
    brain.safety.governor.record("delete_file")
    brain.script.answers = [YES, YES]
    assert hook_call(brain, "delete_file", {"path": "C:/tmp/x.txt"}) == {}
    limit_question, consequence = brain.script.questions
    assert limit_question.startswith("delete file: C:/tmp/x.txt") and "deletions" in limit_question
    assert "deleted for good" in consequence
    assert brain.safety.governor.usage()[1]["used"] == 2


def test_under_the_limit_the_hooks_red_question_is_still_asked(brain):
    brain.script.answers = [YES, YES]
    assert hook_call(brain, "delete_file", {"path": "C:/tmp/x.txt"}) == {}
    question, consequence = brain.script.questions
    assert question == "delete file: C:/tmp/x.txt. Confirm?"
    assert "deleted for good" in consequence


def test_an_amber_action_past_a_limit_is_asked_once_and_not_announced_by_the_hook():
    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"
    engine.governor = small_governor(engine, Rule("edits", frozenset({"edit_file"}), 1, HOUR))
    engine.governor.record("edit_file")
    script = Scripted()
    script.answers = [YES]
    announced = []

    async def announce(text):
        announced.append(text)

    b = Brain(CONFIG, engine, AuditLog(CONFIG, "test-session-limits-amber-once"),
              confirm=script.confirm, announce=announce)
    assert hook_call(b, "edit_file", {"path": "C:/tmp/x.txt", "content": "x"}) == {}
    assert len(script.questions) == 1 and announced == []
    assert engine.governor.usage()[0]["used"] == 2


def test_a_no_to_the_limit_means_the_hooks_red_question_is_never_asked(brain):
    brain.safety.governor.record("delete_file")
    brain.script.answers = [NO]
    assert denied(hook_call(brain, "delete_file", {"path": "C:/tmp/x.txt"}))
    assert len(brain.script.questions) == 1


def test_his_no_to_the_hooks_one_question_denies_and_uses_up_nothing(brain):
    brain.safety.governor.record("delete_file")
    brain.script.answers = [NO]
    assert denied(hook_call(brain, "delete_file", {"path": "C:/tmp/x.txt"}))
    assert brain.safety.governor.usage()[1]["used"] == 1


def test_an_amber_action_cancelled_in_its_stop_window_is_not_counted():
    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"
    engine.governor = small_governor(engine, Rule("edits", frozenset({"edit_file"}), 5, HOUR))
    script = Scripted()

    async def stopped(text):
        raise RuntimeError("cancelled by user")

    b = Brain(CONFIG, engine, AuditLog(CONFIG, "test-session-limits-amber"),
              confirm=script.confirm, announce=stopped)
    assert denied(hook_call(b, "edit_file", {"path": "C:/tmp/x.txt", "content": "x"}))
    assert engine.governor.usage()[0]["used"] == 0


def test_a_black_action_is_refused_before_the_governor_is_consulted(brain):
    brain.safety.governor.lock("test")
    result = hook_call(brain, "transfer_funds")
    assert denied(result) and "lockdown" not in reason(result).lower()


def test_a_brain_built_without_a_governor_still_works(brain):
    """Older callers and stub engines: no governor means no limits, not a crash."""
    brain.safety = SimpleNamespace(
        classify=SafetyEngine(CONFIG).classify)       # no .governor at all
    assert hook_call(brain, "read_file", {"path": "C:/tmp/x.txt"}) == {}


# --------------------------------------------------------------------------
# ONE GOVERNOR, ONE ORDER - the two paths cannot drift apart unnoticed
# --------------------------------------------------------------------------
def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    i = source.index(start)
    return source[i:source.index(end, i)]


def test_the_router_gate_asks_the_limit_before_its_own_question_and_counts_after_every_no():
    body = _between(_source("jalen/app.py"), "def handle_local(", "async def _start_brain")
    admit = body.index("self._admit(verdict)")
    red = body.index("if verdict.tier is Tier.RED:")
    amber = body.index("elif verdict.tier is Tier.AMBER and not admission.approved_action:")
    record = body.index("governor.record(tool)")
    call = body.index("systools.call(tool, intent.args)")
    assert body.index("verdict.tier is Tier.BLACK") < admit < red < amber < record < call
    assert body.count("governor.record(") == 1


def test_the_hook_asks_the_limit_before_its_own_question_and_counts_only_when_allowing():
    body = _between(_source("jalen/brain/agent.py"), "def _make_hook(", "# ------------------------------------------------------------------ client")
    black = body.index("verdict.tier is Tier.BLACK")
    admit = body.index("governor.admit(verdict, self.confirm)")
    red = body.index("if verdict.tier is Tier.RED:")
    assert black < admit < red
    assert body.count("governor.record(") == 1                    # one place: allowed_to_run
    assert body.count("return allowed_to_run()") == 3             # RED yes, AMBER, GREEN
    # The only bare allow left is inside allowed_to_run itself: a fourth
    # `return {}` would be a way through the hook that does not count.
    assert body.count("return {}") == 1
    # One question, not two - and only because the governor's question named it.
    assert "approved_by_limit or await self.confirm(" in body
    assert "if verdict.tier is Tier.AMBER and not approved_by_limit:" in body


def test_the_brain_is_built_on_the_same_engine_the_router_uses():
    start = _between(_source("jalen/app.py"), "async def _start_brain", "# --------------------------------------------------- when Claude can't")
    assert "Brain(\n                self.cfg, self.safety," in start


def test_a_brain_turn_is_counted_in_the_one_place_every_door_passes():
    body = _between(_source("jalen/app.py"), "async def handle_with_brain", "async def _prewarm_brain")
    assert body.index("admit_brain_turn(self.confirm)") < body.index("record_brain_turn()") \
        < body.index("self.brain.ask(")
    # ...and every door reaches the brain through it: app.py asks the model in
    # exactly one place, and that place is the function checked above.
    assert _source("jalen/app.py").count("brain.ask(") == 1


# --------------------------------------------------------------------------
# NOTHING THE MODEL CAN CALL RAISES A LIMIT OR LIFTS THE LOCKDOWN
# --------------------------------------------------------------------------
def test_the_model_has_no_tool_that_locks_unlocks_or_sets_a_limit():
    from jalen import tools
    from jalen.brain.tools import TOOL_SPECS

    for name in ("jalen_lockdown", "jalen_lift_lockdown"):
        assert name not in TOOL_SPECS and name not in tools.REGISTRY, name
    risky = [n for n in TOOL_SPECS
             if re.search(r"lockdown|set_limit|raise_limit|unfreeze|limit", n)]
    assert risky == [], f"a tool whose name suggests it can move a limit: {risky}"


def test_the_tool_layer_can_only_read_the_governor():
    """tools/usage.py calls limits.current() and nothing else on the module."""
    used: dict[str, set[str]] = {}
    for path in [*(ROOT / "jalen" / "tools").glob("*.py"), ROOT / "jalen" / "brain" / "tools.py"]:
        # (?!py\b): "jalen/limits.py" in a docstring is a file name, not a call.
        found = set(re.findall(r"\blimits\.(?!py\b)(\w+)", path.read_text(encoding="utf-8")))
        if found:
            used[path.name] = found
    assert used == {"usage.py": {"current"}}, used


def test_the_tool_layer_never_touches_the_governors_setters():
    for path in [*(ROOT / "jalen" / "tools").glob("*.py"), ROOT / "jalen" / "brain" / "tools.py"]:
        text = path.read_text(encoding="utf-8")
        for setter in (".unlock(", "governor.lock(", ".new_instruction(", ".attach(",
                       "governor.record(", "_lockdown_off", "_lockdown_on"):
            assert setter not in text, f"{path.name} reaches {setter}"


@pytest.mark.parametrize("name", ["jalen-lockdown.json", "jalen-usage.json"])
@pytest.mark.parametrize("tool,key", [
    ("delete_file", "path"), ("read_file", "path"), ("edit_file", "path"),
    ("create_file", "path"), ("move_file", "path"), ("rename_file", "path"),
    ("copy_file", "path"), ("open_target", "name"),
])
def test_the_lock_and_the_counts_are_beyond_every_file_tool(name, tool, key):
    """
    Otherwise move_file (GREEN) could rename the lock away before a restart and
    "only he can lift it" would be a sentence about tool names.
    """
    engine = SafetyEngine(CONFIG)
    value = f"C:/Jalen/data/{name}" if key == "path" else f"file C:/Jalen/data/{name}"
    args = {key: value}
    if tool in ("move_file", "copy_file", "rename_file"):
        args["destination"] = "C:/tmp/elsewhere.json"
    assert engine.classify(tool, args).tier.value == "black", (tool, name)
    # ...and as the DESTINATION of a move or copy: planting a fake over them.
    if tool in ("move_file", "copy_file"):
        planted = {"path": "C:/tmp/a.json", "destination": f"C:/Jalen/data/{name}"}
        assert engine.classify(tool, planted).tier.value == "black"


def test_a_file_that_merely_sounds_similar_is_not_caught():
    engine = SafetyEngine(CONFIG)
    for name in ("limits.json", "usage.json", "lockdown.json", "my-limits.json"):
        assert engine.classify("read_file", {"path": f"C:/Users/x/proj/{name}"}).tier.value == "green", name


# --------------------------------------------------------------------------
# EVERY BRAIN TURN, FROM ANY DOOR
# --------------------------------------------------------------------------
class FakeBrain:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, text, on_text=None):
        self.asked.append(text)
        return "hello"


@pytest.fixture
def thinking(the_app, monkeypatch):
    the_app.brain = FakeBrain()
    the_app._brain_down = None
    the_app.safety.governor = Governor.from_config(
        {"brain_turns": {"max": 1, "window_s": HOUR}}, acting=the_app.safety.is_acting,
        clock=Clock())
    the_app.answers = []
    the_app.asked_him = []

    async def scripted(self, question):
        the_app.asked_him.append(question)
        return the_app.answers.pop(0) if the_app.answers else NO

    monkeypatch.setattr(Jalen, "confirm", scripted)
    yield the_app
    the_app.brain = None


def think(app: Jalen, text: str = "hi") -> str:
    return app._run_coro(app.handle_with_brain(text))


def test_a_brain_turn_under_the_cap_is_free(thinking):
    assert think(thinking) == "hello" and thinking.asked_him == []


def test_a_brain_turn_past_the_cap_asks_him_and_his_yes_buys_that_one_turn(thinking):
    think(thinking)
    thinking.answers = [YES]
    assert think(thinking, "again") == "hello"
    assert len(thinking.asked_him) == 1 and "thinking turns" in thinking.asked_him[0]
    assert thinking.brain.asked == ["hi", "again"]


def test_a_brain_turn_past_the_cap_with_his_no_never_reaches_the_model(thinking):
    think(thinking)
    thinking.answers = [NO]
    said = think(thinking, "again")
    assert "limit" in said and thinking.brain.asked == ["hi"]
    thinking.answers = []                      # asking again after a no: not asked twice
    think(thinking, "and again")
    assert len(thinking.asked_him) == 1


def test_the_chat_panel_door_is_counted_too(thinking, monkeypatch):
    think(thinking)
    thinking.answers = [NO]
    sent = []
    from jalen.bridge import server

    class Srv:
        def send_command(self, name, payload, timeout=10):
            sent.append(payload["text"])

    monkeypatch.setattr(server, "get_server", lambda: Srv())
    thinking._answer_panel("tell me something")
    assert thinking.brain.asked == ["hi"] and sent and "limit" in sent[0]


# --------------------------------------------------------------------------
# HE SPEAKS: A NEW INSTRUCTION
# --------------------------------------------------------------------------
def test_his_sentence_starts_a_new_instruction_but_speech_without_his_name_does_not(jalen, monkeypatch):
    calls = []
    monkeypatch.setattr(jalen.safety.governor, "new_instruction", lambda: calls.append(1))
    jalen.process("nothing else")
    assert calls == [1]
    jalen.process("nothing else", from_him=False)     # may be the television
    assert calls == [1]


def test_the_panel_is_his_typing_and_starts_a_new_instruction(thinking, monkeypatch):
    calls = []
    monkeypatch.setattr(thinking.safety.governor, "new_instruction", lambda: calls.append(1))
    from jalen.bridge import server

    monkeypatch.setattr(server, "get_server",
                        lambda: SimpleNamespace(send_command=lambda *a, **k: None))
    thinking._answer_panel("hello")
    assert calls == [1]


# --------------------------------------------------------------------------
# STARTUP: A LOCKDOWN HE SET SURVIVES, AND A LIMIT THAT COULD NOT BE READ IS SAID
# --------------------------------------------------------------------------
def test_a_lockdown_left_on_is_still_on_after_a_restart_and_is_said(tmp_path):
    limits.LOCK_PATH.write_text("{}", encoding="utf-8")
    j = Jalen()
    try:
        assert j.safety.governor.locked
        notes = j.startup_notes()
        assert len(notes) == 1 and "lockdown" in notes[0] and "end the lockdown" in notes[0]
    finally:
        j.shutdown()


def test_a_limit_that_could_not_be_read_is_said_out_loud_not_dropped(the_app):
    the_app.safety.governor = Governor.from_config(
        {"rules": [{"name": "x", "tools": [], "max": 1, "window_s": 60}, GOOD_RULE]})
    notes = the_app.startup_notes()
    assert len(notes) == 1 and "couldn't be read" in notes[0] and "rest are in force" in notes[0]


GOOD_RULE = {"name": "ok", "tools": ["send_email"], "max": 3, "window_s": 60}


def test_with_nothing_unusual_startup_says_nothing(the_app):
    the_app.safety.governor = SafetyEngine(CONFIG).governor
    assert the_app.startup_notes() == []


def test_the_attached_governor_is_the_one_the_tool_layer_reads(the_app):
    """usage_report reads limits.current(); it must be this process's governor."""
    j = Jalen()
    try:
        assert limits.current() is j.safety.governor
    finally:
        j.shutdown()


# --------------------------------------------------------------------------
# WHAT HE SAYS
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def router():
    return IntentRouter(CONFIG)


def tool_for(router: IntentRouter, phrase: str):
    intent = router.route(phrase)
    return intent.tool if intent else None


@pytest.mark.parametrize("phrase", [
    "hands off", "lock down", "lockdown", "freeze", "freeze everything", "stop acting",
    "don't touch anything", "go into lockdown", "Hands off, Jalen", "lockdown mode",
])
def test_these_phrases_put_jalen_in_lockdown(router, phrase):
    assert tool_for(router, phrase) == "jalen_lockdown"


@pytest.mark.parametrize("phrase", [
    "end the lockdown", "lift the lockdown", "lockdown off", "lockdown over", "hands on",
    "unfreeze", "act again", "end lockdown", "turn off the lockdown",
])
def test_these_phrases_end_it(router, phrase):
    assert tool_for(router, phrase) == "jalen_lift_lockdown"


@pytest.mark.parametrize("phrase", [
    "what are my limits", "show me my limits", "tell me my limits", "usage report",
    "limits status", "how much have you used", "how many things have you done today",
    "what have you used today", "what are my usage limits",
])
def test_these_phrases_read_out_the_usage(router, phrase):
    assert tool_for(router, phrase) == "usage_report"


@pytest.mark.parametrize("phrase,expected", [
    ("stop", None),                                   # the kill phrase, handled elsewhere
    ("lock the screen", "lock_workstation"),
    ("quit", "jalen_quit"),
    ("pause", "jalen_pause"),
    ("relax", "set_posture"),
    ("paranoid mode on", "set_posture"),
    ("what time is it", "get_time"),
])
def test_nothing_that_used_to_route_elsewhere_was_taken(router, phrase, expected):
    assert tool_for(router, phrase) == expected


@pytest.mark.parametrize("phrase", [
    "what are your limits",                           # the brain: what Jalen cannot do
    "freeze the screen",
    "hands off my sandwich please",
    "lock down the doors",
    "end the meeting",
])
def test_near_misses_are_left_to_the_brain(router, phrase):
    assert tool_for(router, phrase) not in {"jalen_lockdown", "jalen_lift_lockdown", "usage_report"}


def test_the_usage_report_tool_reads_the_installed_governor(the_app):
    governor = SafetyEngine(CONFIG).governor
    limits.install(governor)
    governor.record("send_email")
    from jalen import tools

    said = tools.call("usage_report", {})
    assert "emails: 1 of 10" in said
    limits.install(None)
    assert "No usage limits are loaded" in tools.call("usage_report", {})
