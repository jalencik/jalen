"""
The diagnostics must check the binary the BRAIN runs, not the one on PATH.

Proven by incident, 20 September 2026. For roughly twenty minutes the brain
could not start at all -- a claude-agent-sdk upgrade had installed a wheel with
no bundled claude.exe, so the SDK fell through to npm's claude.CMD and refused
to spawn a batch script. Throughout that outage:

    scripts/check_env.py:129   shutil.which("claude") -> claude.CMD, probed it,
                               printed "authenticated - your Claude plan is
                               working"
    scripts/readiness.py:82    shutil.which("claude") -> truthy, printed
                               "Claude (the brain) AVAILABLE - CLI on PATH"

Both were structurally incapable of seeing the outage, because neither has ever
looked at the binary Brain.start() spawns. `.\\jalen.ps1 check` is the first
thing the README tells you to run when something is wrong, and it was
confidently wrong.

readiness.py was the weaker of the two: it reported the brain AVAILABLE on the
mere EXISTENCE of a file on PATH, without spawning anything or checking auth.

resolved_cli_path() is the single source of truth. Both diagnostics ask it, so
they cannot drift from the brain again.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.brain.agent import resolved_cli_path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_an_override_wins_and_says_it_was_the_override(monkeypatch, tmp_path):
    """If CLAUDE_CLI_PATH names a usable binary, that is what the brain will
    spawn, and the diagnostic has to name the same file."""
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(exe))

    path, source, spawnable = resolved_cli_path()

    assert path == str(exe)
    assert "CLAUDE_CLI_PATH" in source
    assert spawnable is True


def test_with_no_override_it_never_reports_a_batch_script_as_the_brains_binary(monkeypatch):
    """The exact wrong answer both diagnostics used to give. shutil.which
    returns claude.CMD on this machine; the SDK refuses to spawn it. A report
    naming it as the brain's binary is the lie that hid the outage."""
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)

    path, source, spawnable = resolved_cli_path()

    if path is not None and path.lower().endswith((".cmd", ".bat")):
        assert spawnable is False, (
            "a batch script was reported as the brain's binary without being "
            "flagged unspawnable - this is the bug that hid a real outage"
        )


def test_it_reports_whether_the_binary_can_actually_be_spawned(monkeypatch, tmp_path):
    """Existence is not usability. A .cmd exists and cannot be run by the SDK,
    which is precisely the distinction readiness.py failed to make."""
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(shim))

    path, source, spawnable = resolved_cli_path()

    assert spawnable is False


def test_a_missing_binary_is_reported_rather_than_guessed(monkeypatch, tmp_path):
    """When nothing usable can be found the diagnostic must say so, not fall
    back to something cheerful."""
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(tmp_path / "nope" / "claude.exe"))

    path, source, spawnable = resolved_cli_path()

    assert source, "the diagnostic must always explain where it looked"


def test_check_env_no_longer_decides_the_brain_from_path_alone():
    """Structural assertion, the way tests/test_adversarial.py pins the
    injection-guard ordering: behaviour tests cannot see that a diagnostic is
    consulting the wrong source, so the source itself is the assertion."""
    source = (ROOT / "scripts" / "check_env.py").read_text(encoding="utf-8")

    assert "resolved_cli_path" in source, (
        "check_env must ask the brain's own resolver"
    )

    # The ASSIGNMENT, not any mention: the fixed file explains in a comment
    # what it deliberately no longer does, and that comment names the old call.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'claude_path = shutil.which("claude")' not in code, (
        "still deciding the brain's binary from PATH - this reported a healthy "
        "plan during a real twenty-minute outage"
    )


def test_readiness_no_longer_calls_the_brain_available_because_a_file_exists():
    """readiness.py claimed AVAILABLE from shutil.which() alone, with no spawn
    and no auth check.

    Note it may still use shutil.which for the HANDOFF entry, and should: the
    handoff path (jarvis/tools/coding.py) deliberately launches the PATH shim
    interactively, so PATH is the correct source for that one line. Only the
    BRAIN entry has to come from the resolver."""
    source = (ROOT / "scripts" / "readiness.py").read_text(encoding="utf-8")

    assert "resolved_cli_path" in source, "the brain entry must use the resolver"

    brain_entry = source[source.index("def probe_brain"):]
    brain_entry = brain_entry[: brain_entry.index("Claude Code handoff")]
    assert "spawnable" in brain_entry, (
        "the brain entry must report usability, not mere existence"
    )
