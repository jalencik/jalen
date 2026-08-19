"""
Regression tests for two critical findings in how Brain.start() configures
the Agent SDK — both found live while verifying Phase C's tool registration
end-to-end, both far more severe than the tool-specific bugs found earlier
this session:

1. ClaudeAgentOptions.tools left unset (the prior state) gives Claude the
   SDK's full built-in toolset — Bash, PowerShell, Write, Edit, Read, Agent,
   and more — entirely separate from and in addition to the jarvis MCP
   tools. Confirmed live: a probe call with tools left at its default could
   see a real Bash tool and successfully invoke it. None of those built-in
   names exist in safety.yaml, so they'd fall to unclassified — RED only
   while paranoid_first_week is on, plain AMBER (announce, then
   auto-proceed in 4s, no real confirmation) the moment it's turned off,
   which the handoff explicitly expects to happen "once you trust it."
   That's arbitrary shell execution behind what looks like an ordinary
   confirmation, completely bypassing every tier boundary this project is
   built around. Fixed with tools=[].

2. permission_mode="default" (the prior state) prompts interactively for
   "dangerous" operations, entirely separate from and in addition to the
   PreToolUse hook. There is no interactive terminal to ever answer that
   prompt in Jarvis's actual usage (voice, text mode, or Telegram).
   Confirmed live: every registered tool call failed with "permission
   wasn't granted," including plain GREEN-tier reads — the brain was, in
   effect, completely unable to use any tool at all. Fixed with
   permission_mode="bypassPermissions", which — confirmed straight from the
   SDK's own source (types.py's can-use-tool shadowing-warning helper) —
   shadows only the unused can_use_tool callback, not PreToolUse hooks.
   The hook remains the sole, real gate; live RED-denial (confirmed no)
   was re-verified under this exact configuration and still correctly
   prevented execution.

These are asserted here as static configuration facts (what Brain.start()
actually passes to ClaudeAgentOptions), not via a live SDK call — fast and
deterministic, and a regression here is exactly the kind of thing that
would otherwise only be caught by someone happening to try it live again.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.audit import AuditLog  # noqa: E402
from jarvis.brain.agent import Brain  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.safety import SafetyEngine  # noqa: E402


class _FakeSdkClient:
    """Captures the ClaudeAgentOptions Brain.start() actually builds,
    without making a real SDK connection."""

    captured_options: dict = {}

    def __init__(self, options=None, **_kwargs):
        _FakeSdkClient.captured_options["options"] = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


@pytest.fixture
def started_brain(monkeypatch):
    import claude_agent_sdk

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _FakeSdkClient)
    _FakeSdkClient.captured_options.clear()

    safety = SafetyEngine(CONFIG)
    audit = AuditLog(CONFIG, "test-session-sdk-config")

    async def confirm(question: str) -> bool:
        return True

    async def announce(text: str) -> None:
        pass

    brain = Brain(CONFIG, safety, audit, confirm=confirm, announce=announce)
    asyncio.run(brain.start())
    return _FakeSdkClient.captured_options["options"]


def test_built_in_tools_are_disabled(started_brain):
    """tools=[] — otherwise Bash/PowerShell/Write/Edit are available and
    bypass every tier in safety.yaml. This is not the same thing as
    Brain.tools (the jarvis-specific tool list, passed via mcp_servers) —
    it's the SDK's separate built-in toolset, and leaving it at its
    default is what actually caused the live Bash access."""
    assert started_brain.tools == []


def test_permission_mode_bypasses_the_unanswerable_prompt_not_the_hook(started_brain):
    """bypassPermissions removes the interactive default-mode prompt that
    nothing in Jarvis can ever answer. It must NOT be "default" (which
    hangs/denies everything) and must not silently become
    "acceptEdits"/"plan"/something else that changes the semantics without
    anyone deciding to."""
    assert started_brain.permission_mode == "bypassPermissions"


def test_pre_tool_use_hook_is_still_registered(started_brain):
    """The actual gate. bypassPermissions must never ship without this."""
    assert "PreToolUse" in started_brain.hooks
    assert len(started_brain.hooks["PreToolUse"]) >= 1
