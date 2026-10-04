"""
Regression test for a critical bug found while researching Phase C's tool
registration (handoff: "no empty tools=[]"): SDK MCP tools are reported to
PreToolUse hooks as "mcp__<server>__<tool>", not the bare name safety.yaml
classifies by. Confirmed with a live Agent SDK call — a tool registered as
"delete_file" on the "jarvis" server arrived at the hook as
"mcp__jalen__delete_file".

Brain._make_hook() previously classified against the raw, prefixed name.
Since SafetyEngine only knows bare tool names, every real tool call would
have silently misclassified as unclassified-AMBER regardless of its true
tier — RED and BLACK actions alike — the moment Brain.tools stopped being
empty. A critical, invisible safety-gate bypass for the entire agent/brain
path, caught before any real tool existed to be affected by it.

Fixed in agent.py by stripping the confirmed "mcp__jalen__" prefix before
classification. These tests exercise the real _make_hook() closure with the
exact input shape confirmed live, so no further SDK calls are needed to
verify it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.audit import AuditLog  # noqa: E402
from jalen.brain.agent import Brain, MCP_TOOL_PREFIX  # noqa: E402
from jalen.config import CONFIG  # noqa: E402
from jalen.safety import SafetyEngine  # noqa: E402


def _hook_input(tool_name: str, tool_input: dict | None = None) -> dict:
    """Matches the real shape confirmed live from the SDK."""
    return {"tool_name": tool_name, "tool_input": tool_input or {}}


@pytest.fixture
def brain():
    safety = SafetyEngine(CONFIG)
    safety.paranoid = False
    safety.posture = "irreversible_only"
    audit = AuditLog(CONFIG, "test-session-agent-hook")
    answer = {"value": True}

    async def confirm(question: str) -> bool:
        return answer["value"]

    async def announce(text: str) -> None:
        pass

    b = Brain(CONFIG, safety, audit, confirm=confirm, announce=announce)
    b._test_answer = answer  # lets tests flip the confirmation outcome
    return b


def test_prefix_matches_what_was_confirmed_live():
    assert MCP_TOOL_PREFIX == "mcp__jalen__"


def test_black_tier_denied_with_mcp_prefix(brain):
    hook = brain._make_hook()
    result = asyncio.run(hook(_hook_input("mcp__jalen__transfer_funds"), "id1", None))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_red_tier_confirmed_yes_allows_with_mcp_prefix(brain):
    hook = brain._make_hook()
    brain._test_answer["value"] = True
    result = asyncio.run(
        hook(_hook_input("mcp__jalen__delete_file", {"path": "C:/tmp/x.txt"}), "id2", None)
    )
    assert result == {}


def test_red_tier_confirmed_no_denies_with_mcp_prefix(brain):
    hook = brain._make_hook()
    brain._test_answer["value"] = False
    result = asyncio.run(
        hook(_hook_input("mcp__jalen__delete_file", {"path": "C:/tmp/x.txt"}), "id3", None)
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_green_tier_allows_with_mcp_prefix(brain):
    hook = brain._make_hook()
    result = asyncio.run(hook(_hook_input("mcp__jalen__read_file", {"path": "C:/tmp/x.txt"}), "id4", None))
    assert result == {}


def test_unprefixed_tool_name_still_classifies_correctly(brain):
    """Any future non-SDK call shape without the mcp__ prefix must still work."""
    hook = brain._make_hook()
    result = asyncio.run(hook(_hook_input("transfer_funds"), "id5", None))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_this_is_what_the_bug_actually_was():
    """Direct proof of the defect: classifying the raw, prefixed name treats
    a BLACK-tier tool as merely unclassified-AMBER."""
    safety = SafetyEngine(CONFIG)
    verdict_raw = safety.classify("mcp__jalen__transfer_funds", {})
    verdict_fixed = safety.classify("transfer_funds", {})
    assert verdict_raw.detail.get("unclassified") is True  # the bug
    assert verdict_fixed.tier.value == "black"  # the truth
