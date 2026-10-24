"""
"Make sure everything I have said to you is perfect."   - his words, 2026-10-24

The unit tests of each new piece pin that piece. These drive what he actually
says through the real front door - Jalen.process(), the router, the gate, the
governor and the dispatch, on one real Jalen - and the brain's real tool hook
for what only the brain does, so a piece that works alone but is never
reached would fail here.

NOT CHECKED FROM HERE: the microphone, the speakers, his Telegram account and
the Claude CLI. This box has none of them; Telegram and the model are faked
exactly where every other test fakes them.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from jalen import app as app_module
from jalen import taint
from jalen.app import Jalen
from jalen.audit import AuditLog
from jalen.brain.agent import Brain, saved_session_path
from jalen.config import CONFIG
from jalen.safety import SafetyEngine
from jalen.tools import memory


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
    the_app.safety.paranoid = False
    the_app.safety.posture = "irreversible_only"
    the_app.said = []
    the_app.say = lambda text, force=False: the_app.said.append(text)
    the_app.ran = []

    def spy(tool, args):
        the_app.ran.append((tool, dict(args or {})))
        return f"{tool} done"

    monkeypatch.setattr(app_module.systools, "call", spy)
    return the_app


# --------------------------------------------------------------------------
# MEMORY, SAID OUT LOUD
# --------------------------------------------------------------------------
def test_remember_that_reaches_memory_in_his_own_words(jalen):
    jalen.process("remember that my sister is called Dilnoza")
    assert ("remember", {"text": "my sister is called Dilnoza"}) in jalen.ran


def test_remember_that_still_works_after_jalen_has_read_something(jalen):
    """His words are his: a read page does not stop him storing his own fact."""
    taint.mark("an email")
    jalen.process("remember that my exam is on June 5")
    assert ("remember", {"text": "my exam is on June 5"}) in jalen.ran


def test_do_you_remember_searches_memory(jalen):
    jalen.process("do you remember my sister's name?")
    assert ("recall_memory", {"query": "my sister's name"}) in jalen.ran


def test_private_mode_said_aloud_stops_memory_storing(the_app, monkeypatch):
    the_app.said = []
    the_app.say = lambda text, force=False: the_app.said.append(text)
    try:
        the_app.process("private mode on")
        assert memory._PRIVATE.is_set()
        reply = memory.remember("my sister is called Dilnoza")
        assert reply.startswith("Private mode is on, so I'm not storing anything")
    finally:
        the_app.process("private mode off")
    assert not memory._PRIVATE.is_set()


# --------------------------------------------------------------------------
# "NEW CONVERSATION"
# --------------------------------------------------------------------------
def test_new_conversation_said_aloud_lets_the_conversation_go(jalen):
    path = saved_session_path(CONFIG)
    path.write_text('{"session_id": "3f2b8c1e-9a4d-4e6f-8b2a-1c5d7e9f0a3b", "at": 1}',
                    encoding="utf-8")
    jalen.brain = None
    jalen.process("start a new conversation")
    assert not path.exists()
    assert any("New conversation" in s for s in jalen.said)
    assert jalen.ran == [], "it was dispatched as a tool instead of handled as his own state"


# --------------------------------------------------------------------------
# WHAT ONLY THE BRAIN DOES: THE HOOK ASKS THE RIGHT QUESTION
# --------------------------------------------------------------------------
class _Asked:
    def __init__(self, answers):
        self.answers, self.questions, self.announced = list(answers), [], []

    async def confirm(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False

    async def announce(self, text):
        self.announced.append(text)
        return True


def _hook(answers=()):
    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"
    asked = _Asked(answers)
    brain = Brain(CONFIG, engine, AuditLog(CONFIG, "test-end-to-end"),
                  confirm=asked.confirm, announce=asked.announce)
    hook = brain._make_hook()

    def call(tool, args):
        return asyncio.run(hook({"tool_name": f"mcp__jalen__{tool}", "tool_input": args},
                                "id", None))

    return call, asked


def _denied(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_a_brain_worded_message_is_heard_before_his_yes():
    call, asked = _hook(answers=[True])
    result = call("send_telegram_message", {"to": "Ali", "text": "I'll be 15 minutes late, sorry!"})
    assert not _denied(result)
    assert asked.questions and "I'll be 15 minutes late, sorry!" in asked.questions[0]


def test_a_scheduled_message_is_asked_with_its_day_and_time():
    call, asked = _hook(answers=[True])
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 09:00")
    call("send_telegram_message", {"to": "Ali", "text": "Happy birthday!", "schedule_at": tomorrow})
    assert "to go out tomorrow at 09:00" in asked.questions[0]


def test_deleting_a_message_is_asked_twice_and_his_no_stops_it():
    call, asked = _hook(answers=[True, False])
    result = call("delete_message", {"chat": "Ali", "message": "#11"})
    assert _denied(result)
    assert len(asked.questions) == 2 and "can't be undone" in asked.questions[1]


def test_nothing_read_can_post_schedule_or_delete_through_the_brain():
    call, asked = _hook(answers=[True, True])
    taint.mark("a stranger's message")
    for tool, args in [
        ("send_telegram_message", {"to": "Ali", "text": "x"}),
        ("send_telegram_message", {"to": "Ali", "text": "x", "schedule_at": "tomorrow 09:00"}),
        ("delete_message", {"chat": "Ali"}),
        ("cancel_scheduled_post", {"chat": "Ali"}),
        ("pin_telegram_message", {"chat": "Ali"}),
    ]:
        assert _denied(call(tool, args)), tool
    assert asked.questions == [], "it asked him about something a page wanted"
