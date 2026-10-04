"""
Which Claude Code binary the brain spawns, and how it can be redirected.

The live finding this prevents: claude-agent-sdk resolves the CLI by trying
its OWN bundled binary first, before PATH is ever consulted. Verified on this
machine 2026-09-20: the SDK ran
.venv/Lib/site-packages/claude_agent_sdk/_bundled/claude.exe (v2.1.235) while
`where claude` gave only npm's shims at AppData/Roaming/npm/claude.CMD
(v2.1.263). Nobody reading Brain.start() would guess that, because it passes
no cli_path at all — the choice is made several layers down in the SDK.

The trap in "just point it at the one my terminal uses": on Windows the SDK
REFUSES to execute a batch script (_reject_windows_batch_cli), and npm's
Windows install is exactly that — a claude.cmd shim. So setting cli_path to
what `where claude` returns does not unify the two installations, it stops
the brain from starting at all, with a batch-script refusal rather than
anything that reads like a configuration mistake.

Hence chosen_cli_path(): an opt-in CLAUDE_CLI_PATH override that refuses the
batch script instead of forwarding it, ignores a path that is not there
rather than taking the brain down over a typo, and otherwise leaves the SDK
to pick its own bundled binary exactly as before. A misconfigured override
must never be more damaging than no override — the same rule parse_hotkey
follows for a mistyped hotkey (hotkeys.py:104-110): one broken dial costs
that dial, not the whole process.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.brain.agent import chosen_cli_path  # noqa: E402


def test_an_unset_override_leaves_the_sdk_to_pick_its_own_binary(monkeypatch):
    """No CLAUDE_CLI_PATH means None, not "". Passing an empty string as
    cli_path would make the SDK try to spawn "", so the absence of the
    variable has to come back as a real None."""
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)

    assert chosen_cli_path() is None


def test_an_empty_override_is_treated_as_unset(monkeypatch):
    """A key left blank in .env (CLAUDE_CLI_PATH=) parses as "" rather than
    absent, and that is the shape a half-finished edit actually leaves."""
    monkeypatch.setenv("CLAUDE_CLI_PATH", "   ")

    assert chosen_cli_path() is None


def test_a_batch_script_is_refused_rather_than_handed_to_the_sdk(monkeypatch, tmp_path):
    """The whole reason this function exists. npm's Windows install is a
    claude.cmd shim; the SDK refuses to spawn one. Forwarding it would
    replace a working brain with a startup failure, so refuse it here and
    fall back to the binary that does work."""
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(shim))

    assert chosen_cli_path() is None


def test_a_dot_bat_script_is_refused_too(monkeypatch, tmp_path):
    """.bat is the same class of file as .cmd and CreateProcess cannot run
    either one directly."""
    shim = tmp_path / "claude.bat"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(shim))

    assert chosen_cli_path() is None


def test_the_batch_refusal_ignores_case(monkeypatch, tmp_path):
    """shutil.which on this machine returns claude.CMD in capitals, so a
    case-sensitive suffix check would let the shim straight through."""
    shim = tmp_path / "CLAUDE.CMD"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(shim))

    assert chosen_cli_path() is None


def test_a_path_that_is_not_there_is_ignored_rather_than_raising(monkeypatch, tmp_path):
    """A typo, or a binary that moved, must cost the override and nothing
    else. Raising here would mean the brain never starts and the reason is
    buried in a traceback that pythonw discards."""
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(tmp_path / "not-installed" / "claude.exe"))

    assert chosen_cli_path() is None


def test_a_native_executable_that_exists_is_used(monkeypatch, tmp_path):
    """The success case: a real claude.exe, named deliberately, wins over
    the SDK's bundled copy."""
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(exe))

    assert chosen_cli_path() == str(exe)


def test_surrounding_whitespace_and_quotes_are_stripped(monkeypatch, tmp_path):
    """Pasting a Windows path from Explorer brings quotes with it, and .env
    does not strip them. A quoted path that exists should still be found
    rather than silently ignored as missing."""
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("CLAUDE_CLI_PATH", f'  "{exe}"  ')

    assert chosen_cli_path() == str(exe)


def test_a_directory_is_not_mistaken_for_the_executable(monkeypatch, tmp_path):
    """Path.exists() is true for a directory, so naming the containing
    folder instead of the binary has to be caught as well."""
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(tmp_path))

    assert chosen_cli_path() is None


def test_a_refused_override_is_recorded_where_why_can_find_it(monkeypatch, tmp_path):
    """Silently ignoring the override would leave someone editing .env with
    no way to tell the setting never took. It goes to data/crash.log, which
    is what `python run.py --why` prints."""
    from jalen import crashlog

    written: list[str] = []
    monkeypatch.setattr(crashlog, "write", lambda message: written.append(message))

    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(shim))

    assert chosen_cli_path() is None
    assert any("CLAUDE_CLI_PATH" in message for message in written)


def test_a_broken_log_sink_does_not_take_the_brain_down(monkeypatch, tmp_path):
    """Diagnostics may never raise (the rule stated at crashlog.py:46-48).
    A full disk breaking the log must not turn a harmless bad override into
    a brain that will not start."""
    from jalen import crashlog

    def explode(_message):
        raise OSError("disk full")

    monkeypatch.setattr(crashlog, "write", explode)
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(tmp_path / "gone" / "claude.exe"))

    assert chosen_cli_path() is None


class _FakeSdkClient:
    """Captures the options Brain.start() builds, without connecting."""

    captured: dict = {}

    def __init__(self, options=None, **_kwargs):
        _FakeSdkClient.captured["options"] = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


def _start_brain_capturing_options(monkeypatch):
    import asyncio

    import claude_agent_sdk

    from jalen.audit import AuditLog
    from jalen.brain.agent import Brain
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _FakeSdkClient)
    _FakeSdkClient.captured.clear()

    async def confirm(_question: str) -> bool:
        return True

    async def announce(_text: str) -> None:
        return None

    brain = Brain(
        CONFIG,
        SafetyEngine(CONFIG),
        AuditLog(CONFIG, "test-session-cli-path"),
        confirm=confirm,
        announce=announce,
    )
    asyncio.run(brain.start())
    return _FakeSdkClient.captured["options"]


def test_brain_start_forwards_the_chosen_binary_to_the_sdk(monkeypatch):
    """The function existing is not the same as it being wired in. Returning
    a sentinel proves Brain.start() actually consults it, which asserting
    None could not — cli_path defaults to None whether it is passed or not."""
    from jalen.brain import agent

    monkeypatch.setattr(agent, "chosen_cli_path", lambda: "C:/somewhere/claude.exe")

    options = _start_brain_capturing_options(monkeypatch)

    assert options.cli_path == "C:/somewhere/claude.exe"


def test_no_override_still_leaves_the_sdk_to_resolve_the_binary(monkeypatch):
    """The default path must stay exactly as it was before this option
    existed: cli_path=None sends the SDK through its own _find_cli, which is
    what picks the bundled binary."""
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)

    options = _start_brain_capturing_options(monkeypatch)

    assert options.cli_path is None
