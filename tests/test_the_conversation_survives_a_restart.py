"""
"Constant memory ... save up the conversation."         - his words, 2026-10-23

Facts he tells Jalen survive in memory.py. The conversation did not: the SDK
holds it in the Claude CLI child, so every restart - and every Ctrl+Alt+K,
which ends the process - began the next turn knowing nothing that had just
been said.

Now (jalen/brain/agent.py) the session of every turn that ended well is
written down (brain.session_path), and the next Brain resumes it with the
SDK's `resume`:

  * by that exact id - never `continue_conversation`, which would pick up the
    newest session in the folder, his own Claude Code session included;
  * only within brain.resume_within_s, and only an id shaped like the CLI's,
    because it becomes a command-line argument of the child;
  * never at the cost of the brain: a session that cannot be resumed is
    forgotten and a fresh one starts;
  * never a session that had a turn in private mode;
  * and "new conversation" lets it go on purpose.

NOT CHECKED FROM HERE: the real CLI resuming a real transcript. There is no
Claude login on this machine; these tests drive Brain against a fake SDK
client, which is what every other brain test does.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from claude_agent_sdk.types import ResultMessage

from jalen.brain import agent
from jalen.brain.agent import Brain
from jalen.config import CONFIG

SESSION = "3f2b8c1e-9a4d-4e6f-8b2a-1c5d7e9f0a3b"
OTHER = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


def _result(session=SESSION, **over):
    fields = dict(subtype="success", duration_ms=10, duration_api_ms=5,
                  is_error=False, num_turns=1, session_id=session, result="Done.")
    fields.update(over)
    return ResultMessage(**fields)


class _Sdk:
    """Stands in for claude_agent_sdk.ClaudeSDKClient: records every client made."""

    made: list = []
    refuse_resume = False
    replies: list = []
    during_turn = None

    def __init__(self, options=None, **_kw):
        self.options = options
        self.closed = False
        _Sdk.made.append(self)

    async def __aenter__(self):
        if _Sdk.refuse_resume and self.options.resume:
            raise RuntimeError("No conversation found with session ID")
        return self

    async def __aexit__(self, *_exc):
        self.closed = True

    async def query(self, text):
        self.asked = text

    async def receive_response(self):
        if _Sdk.during_turn is not None:
            _Sdk.during_turn()
        for message in _Sdk.replies or [_result()]:
            yield message


@pytest.fixture
def sdk(monkeypatch):
    import claude_agent_sdk

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _Sdk)
    _Sdk.made, _Sdk.refuse_resume, _Sdk.replies, _Sdk.during_turn = [], False, [], None
    return _Sdk


def brain(private=False):
    async def confirm(_question):
        return True

    async def announce(_text):
        return None

    return Brain(CONFIG, SafetyEngine(), SimpleNamespace(private=private),
                 confirm=confirm, announce=announce)


def SafetyEngine():
    from jalen.safety import SafetyEngine as Engine

    return Engine(CONFIG)


def record():
    path = agent.saved_session_path(CONFIG)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_record(session=SESSION, age_s=60.0):
    path = agent.saved_session_path(CONFIG)
    path.write_text(json.dumps({"session_id": session, "at": time.time() - age_s}),
                    encoding="utf-8")


def turn(b, text="what were we talking about?"):
    return asyncio.run(b.ask(text))


# --------------------------------------------------------------------------
# 1. A GOOD TURN IS WRITTEN DOWN, AND THE NEXT START PICKS IT UP
# --------------------------------------------------------------------------
def test_the_tests_never_touch_his_real_record():
    assert "jarvis-test-data" in str(agent.saved_session_path(CONFIG))


def test_a_turn_that_ended_well_writes_its_session_down(sdk):
    turn(brain())
    assert record()["session_id"] == SESSION
    assert abs(record()["at"] - time.time()) < 60


def test_the_next_brain_resumes_that_conversation(sdk):
    turn(brain())
    after_restart = brain()
    asyncio.run(after_restart.start())
    assert sdk.made[-1].options.resume == SESSION
    assert after_restart.resumed == SESSION


def test_it_never_picks_up_whatever_session_is_newest_in_the_folder(sdk):
    """continue_conversation would resume his own Claude Code session on this repo."""
    write_record()
    asyncio.run(brain().start())
    assert sdk.made[-1].options.continue_conversation is False


def test_with_nothing_written_down_a_new_conversation_starts(sdk):
    asyncio.run(brain().start())
    assert sdk.made[-1].options.resume is None


@pytest.mark.parametrize("failed", [
    dict(subtype="success", is_error=True),             # an API error the CLI could not place
    dict(subtype="error_max_turns", is_error=True),     # cut off
])
def test_a_turn_that_did_not_end_well_is_not_written_down(sdk, failed):
    sdk.replies = [_result(**failed)]
    try:
        turn(brain())
    except Exception:  # noqa: BLE001 - how it fails is other tests' business
        pass
    assert record() is None


# --------------------------------------------------------------------------
# 2. WHAT IS NEVER RESUMED
# --------------------------------------------------------------------------
def test_a_conversation_older_than_the_window_is_not_picked_up(sdk):
    write_record(age_s=CONFIG.get_path("brain.resume_within_s", 43200) + 60)
    asyncio.run(brain().start())
    assert sdk.made[-1].options.resume is None


@pytest.mark.parametrize("bad", ["", "s", "abc --dangerously-skip-permissions",
                                 SESSION + "\n--print", "../../etc/passwd"])
def test_only_an_id_shaped_like_the_clis_reaches_its_command_line(sdk, bad):
    write_record(session=bad)
    asyncio.run(brain().start())
    assert sdk.made[-1].options.resume is None


def test_an_unreadable_record_is_not_guessed_at(sdk):
    agent.saved_session_path(CONFIG).write_text("{not json", encoding="utf-8")
    asyncio.run(brain().start())
    assert sdk.made[-1].options.resume is None


def test_a_conversation_that_cannot_be_resumed_never_costs_him_the_brain(sdk):
    write_record()
    sdk.refuse_resume = True
    b = brain()
    asyncio.run(b.start())
    first, second = sdk.made
    assert first.options.resume == SESSION and first.closed
    assert second.options.resume is None and b._client is second
    assert record() is None, "a session that would fail again was kept"
    assert b.resumed == ""


def test_a_start_that_fails_with_nothing_to_resume_still_says_so(sdk):
    class Down(_Sdk):
        async def __aenter__(self):
            raise RuntimeError("Claude CLI not found")

    import claude_agent_sdk

    claude_agent_sdk.ClaudeSDKClient = Down
    with pytest.raises(RuntimeError, match="not found"):
        asyncio.run(brain().start())


def test_a_private_turn_is_never_brought_back(sdk):
    write_record()
    turn(brain(private=True))
    assert record() is None, "the conversation before private mode was kept to resume"


def test_a_session_that_had_a_private_turn_stays_unrecorded_after_it(sdk):
    b = brain(private=True)
    turn(b)
    b.audit.private = False
    turn(b)
    assert record() is None, "the private part would come back after a restart"


# --------------------------------------------------------------------------
# 3. "NEW CONVERSATION"
# --------------------------------------------------------------------------
def test_new_conversation_forgets_it_and_the_next_turn_starts_a_new_one(sdk):
    b = brain()
    turn(b)
    old = sdk.made[-1]
    b.forget_conversation()
    assert record() is None
    sdk.replies = [_result(session=OTHER)]
    turn(b)
    assert old.closed and sdk.made[-1] is not old
    assert sdk.made[-1].options.resume is None
    assert record()["session_id"] == OTHER


def test_a_turn_still_running_does_not_write_the_old_one_back(sdk):
    b = brain()
    sdk.during_turn = b.forget_conversation
    turn(b)
    assert record() is None


@pytest.mark.parametrize("said", ["new conversation", "start a new conversation",
                                  "let's start a fresh conversation", "new chat",
                                  "forget this conversation", "clear our conversation",
                                  "end the conversation"])
def test_new_conversation_is_a_free_spoken_command(said):
    from jalen.brain.router import IntentRouter

    hit = IntentRouter(CONFIG).route(said)
    assert hit is not None and hit.tool == "new_conversation", said


@pytest.mark.parametrize("said", ["start over", "start the conversation with Ali",
                                  "new chat with Ali"])
def test_a_sentence_about_something_else_does_not_wipe_it(said):
    from jalen.brain.router import IntentRouter

    hit = IntentRouter(CONFIG).route(said)
    assert hit is None or hit.tool != "new_conversation", said


def test_new_conversation_runs_at_once():
    from jalen.safety import Tier

    verdict = SafetyEngine().classify("new_conversation", {})
    assert verdict.tier is Tier.GREEN and not verdict.detail.get("unclassified")


def test_new_conversation_with_no_brain_yet_still_forgets_it(sdk):
    from jalen.app import Jalen
    from jalen.brain.router import Intent

    write_record()
    j = Jalen.__new__(Jalen)
    j.brain = None
    j._brain_session_path = agent.saved_session_path(CONFIG)
    reply = j.handle_local(Intent("new_conversation", {}, "New conversation."))
    assert reply == "New conversation." and record() is None


def test_new_conversation_with_a_brain_tells_the_brain(sdk):
    from jalen.app import Jalen
    from jalen.brain.router import Intent

    j = Jalen.__new__(Jalen)
    j.brain = brain()
    turn(j.brain)
    j.handle_local(Intent("new_conversation", {}, "New conversation."))
    assert j.brain._fresh and record() is None
