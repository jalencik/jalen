"""
Regression tests for two gaps found live while driving the Phase B
checkpoint scenario through the real app (handoff's own rule: verify by
running it, not by reading the code).

1. end_phrases used substring containment ("nothing else" in low), so any
   sentence that happened to CONTAIN an end phrase as a substring got
   silently treated as "conversation over" and never reached the router or
   the brain. Fixed to exact match.

2. handle_local() — the router path — only special-cased RED and BLACK
   tiers. AMBER tools executed immediately with no announce, no undo
   window, no chance to say stop: exactly the "fourth path around the gate"
   §7 says to prove doesn't exist. Only the Brain/agent-hook path had ever
   implemented AMBER's announce+undo-window. Fixed by mirroring that
   handling in handle_local() too.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.app import Jalen  # noqa: E402
from jarvis.brain.router import Intent  # noqa: E402


@pytest.fixture
def jarvis():
    j = Jalen()
    j.muted = True
    try:
        yield j
    finally:
        j.shutdown()


# ------------------------------------------------------------- end phrases
def test_end_phrase_as_a_substring_does_not_end_the_conversation(jarvis, monkeypatch):
    """'...just the number, nothing else.' contains the end phrase 'nothing
    else' as a substring but is a real question, not a goodbye."""
    reached_brain = {}

    async def fake_handle_with_brain(self, text):
        reached_brain["text"] = text
        return "handled"

    monkeypatch.setattr(Jalen, "handle_with_brain", fake_handle_with_brain)
    jarvis.say = lambda t, force=False: None

    jarvis.process("What is 47 times 89? Answer with just the number, nothing else.")

    assert reached_brain.get("text"), "message was swallowed as an end phrase instead of reaching the brain"


def test_end_phrase_exact_match_still_ends_the_conversation(jarvis):
    said = []
    jarvis.say = lambda t, force=False: said.append(t)

    jarvis.process("nothing else")

    assert said == ["Any time."]


# --------------------------------------------------------------- AMBER gate
def test_amber_tier_router_tool_is_announced_before_executing(jarvis, monkeypatch):
    """handle_local() must call announce() for AMBER-tier tools — not just
    run them straight through like GREEN."""
    jarvis.safety.paranoid = False  # else paranoid_first_week promotes AMBER -> RED
    announced = {}

    async def fake_announce(self, text):
        announced["text"] = text

    monkeypatch.setattr(Jalen, "announce", fake_announce)

    # A synthetic, never-implemented tool name: unclassified in safety.yaml
    # (defaults to AMBER) and absent from every REGISTRY, so this exercises
    # the announce step and then cleanly no-ops (KeyError -> None) rather
    # than doing anything. Deliberately not a real tool name (e.g.
    # close_app used to serve this role, until Phase C actually implemented
    # it) — a real tool's implementation status can change; this can't.
    reply = jarvis.handle_local(Intent(tool="some_undefined_amber_tool_xyz", args={"name": "notepad"}))

    assert announced.get("text"), "AMBER tool executed without ever calling announce()"
    assert "stop" in announced["text"].lower()
    assert reply is None  # not implemented — the announce is what's under test


def test_amber_tier_stop_cancels_before_executing(jarvis, monkeypatch):
    """Saying stop during the AMBER window must cancel — not execute anyway."""
    jarvis.safety.paranoid = False  # else paranoid_first_week promotes AMBER -> RED

    async def instantly_stopped_announce(self, text):
        raise RuntimeError("cancelled by user")

    monkeypatch.setattr(Jalen, "announce", instantly_stopped_announce)

    reply = jarvis.handle_local(Intent(tool="some_undefined_amber_tool_xyz", args={"name": "notepad"}))

    assert reply == "Cancelled."


def test_green_tier_router_tool_still_executes_without_announcing(jarvis, monkeypatch):
    """Sanity check the fix didn't over-apply: GREEN tools must NOT announce."""
    announced = {"called": False}

    async def fake_announce(self, text):
        announced["called"] = True

    monkeypatch.setattr(Jalen, "announce", fake_announce)

    reply = jarvis.handle_local(Intent(tool="get_time", args={}))

    assert announced["called"] is False
    assert reply and "it's" in reply.lower()
