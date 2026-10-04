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

from jalen.audit import AuditLog  # noqa: E402
from jalen.brain.agent import Brain  # noqa: E402
from jalen.config import CONFIG  # noqa: E402
from jalen.safety import SafetyEngine  # noqa: E402


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


def test_jarvis_does_not_inherit_the_machines_claude_code_setup(started_brain):
    """
    Seen in a real audit log: asked to open a local PDF, Jarvis reached for a
    `chrome-devtools` MCP tool belonging to an unrelated developer setup on
    this machine.

    With setting_sources at its default (None) the SDK loads every filesystem
    settings source, ~/.claude/settings.json included, so whatever MCP servers
    and skills are configured for Claude Code get injected into Jarvis. Those
    tools appear under names safety.yaml has never heard of, so they classify
    as unclassified-AMBER — real capability arriving through a door the tier
    system was never designed for. [] is the SDK's isolation mode.
    """
    assert started_brain.setting_sources == []
    assert started_brain.skills == []


def test_account_level_connectors_are_refused(started_brain):
    """
    setting_sources=[] stops the machine's FILESYSTEM config leaking in. It
    does nothing about the connectors the CLI fetches from the signed-in
    ACCOUNT, which arrive over the network and are a separate door.

    This one is not hypothetical and not a latency argument. data/audit.db
    holds 41 foreign-MCP rows that reached the PreToolUse hook across four
    separate days, classified unclassified-AMBER because safety.yaml has never
    heard of those names -- announce, then auto-proceed:

        2026-08-19  AMBER  COMPOSIO_REMOTE_BASH_TOOL      x2
        2026-08-20  AMBER  COMPOSIO_MULTI_EXECUTE_TOOL    x9
        2026-09-01  AMBER  COMPOSIO_MANAGE_CONNECTIONS    x3
        2026-09-03  AMBER  COMPOSIO_MULTI_EXECUTE_TOOL    x2

    An arbitrary remote shell ran behind a four-second announce window, twice.
    `claude mcp list` confirms seven such connectors are live on this login.

    Same class of defect as the setting_sources one above, so the same
    treatment: a pinned safety decision rather than a preference.

    On the token cost, which is NOT what this test defends: an investigation on
    SDK 0.2.140 measured 46,419 connector tokens, 62% of a 75,007-token prefix.
    A single-turn probe on 0.2.156 showed 28,582 either way, because the
    connectors arrive in waves a short turn never sees. Unconfirmed on this
    SDK; the safety boundary is the reason.

    Jalen's own MCP server is passed explicitly in the same options block, so
    nothing it actually uses is lost.
    """
    assert started_brain.strict_mcp_config is True
