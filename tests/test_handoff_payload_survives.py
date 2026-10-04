r"""
The brief he dictated arrived at Claude Code cut off at the first line.

ask_claude_code() does

    subprocess.Popen([cli, initial], cwd=path, ...)

and `cli` comes from shutil.which("claude"), which on this machine returns

    C:\Users\user\AppData\Roaming\npm\claude.CMD

npm's Windows install is a BATCH SHIM. CreateProcess cannot run a .CMD
directly, so Windows runs it through cmd.exe - and cmd.exe owns the argument
before the script ever sees it.

MEASURED, sending one string through a .CMD and through a real .exe:

    sent   Objective - ship it. "Quoted", unicode-accents,
           and a second line. %EXAMPLE%

    .CMD   "Objective - ship it. \"Quoted\", unicode,
           newline    TRUNCATED - everything after line one is gone
           em dash    flattened to a hyphen
           accents    stripped by the cp1251 console codepage
           quotes     escaped and mangled
           %VAR%      never arrived, the string was cut before it

    .exe   byte-identical to what was sent

(The em dash and the accented characters are written as escapes in this file
rather than typed, because this repo has been bitten before by non-ASCII and
backslashes going through a shell heredoc - see CLAUDE.md.)

WHY IT MATTERS HERE SPECIFICALLY. The prompt handed to Claude Code is not a
short command: coding.py's own guidance is to "call master_prompt_guide and
write it properly: context, ..." - a multi-paragraph brief, written by the
model, with newlines in it. Every line after the first was being discarded in
silence, and Claude Code then started work on a fragment. And he writes Uzbek,
which the console codepage does not carry.

THE REPO ALREADY KNOWS THIS. jalen/brain/agent.py refuses a .cmd outright -
"a batch script the Agent SDK refuses to spawn" - and CLAUDE.md documents the
native installer as the way to get a real binary. coding.py never got the
message: its _CANDIDATES list puts "claude.cmd" FIRST.

A real claude.exe is already on this machine, bundled inside the SDK wheel at
.venv/Lib/site-packages/claude_agent_sdk/_bundled/claude.exe, and it is the
binary the brain itself runs.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from jalen.tools import coding

# The regression payload, built from escapes.
PAYLOAD = (
    "Objective \u2014 ship it. \"Quoted\", \u00fcn\u00efc\u00f6d\u00e9,\n"
    "and a second line."
)


# ---------------------------------------------------------------------------
# THE RESOLVER
# ---------------------------------------------------------------------------
def test_a_real_executable_is_preferred_over_the_batch_shim(monkeypatch, tmp_path):
    """
    Both exist on this machine. Only one of them can carry the brief.
    """
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    real = tmp_path / "claude.exe"
    real.write_bytes(b"MZ")

    monkeypatch.setattr(coding.shutil, "which",
                        lambda name: str(shim) if name == "claude" else None)
    monkeypatch.setattr(coding, "_NPM_BIN", tmp_path)
    monkeypatch.setattr(coding, "_BUNDLED_CLI", real)

    chosen = coding._claude_cli()
    assert chosen is not None
    assert not chosen.lower().endswith((".cmd", ".bat")), (
        f"picked {chosen!r} - a batch shim mangles the brief on the way in"
    )


def test_the_sdk_bundled_binary_counts_as_a_real_executable(monkeypatch, tmp_path):
    """
    The brain already runs it (jalen/brain/agent.py chosen_cli_path), so a
    machine with the SDK installed always has a usable executable even when
    npm only ever put a shim on PATH.
    """
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    bundled = tmp_path / "bundled" / "claude.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"MZ")

    monkeypatch.setattr(coding.shutil, "which",
                        lambda name: str(shim) if name == "claude" else None)
    monkeypatch.setattr(coding, "_NPM_BIN", tmp_path)
    monkeypatch.setattr(coding, "_BUNDLED_CLI", bundled)
    assert coding._claude_cli() == str(bundled)


def test_with_only_a_shim_available_it_is_still_found(monkeypatch, tmp_path):
    """
    Degrading to "I can't find the CLI" would be worse than the bug. The
    shim is still returned - what changes is how the brief is delivered to
    it, see below.
    """
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setattr(coding.shutil, "which",
                        lambda name: str(shim) if name == "claude" else None)
    monkeypatch.setattr(coding, "_NPM_BIN", tmp_path)
    monkeypatch.setattr(coding, "_BUNDLED_CLI", tmp_path / "nothing.exe")
    assert coding._claude_cli() == str(shim)


# ---------------------------------------------------------------------------
# THE DELIVERY
# ---------------------------------------------------------------------------
def test_a_real_executable_gets_the_brief_verbatim(monkeypatch, tmp_path):
    real = tmp_path / "claude.exe"
    real.write_bytes(b"MZ")
    monkeypatch.setattr(coding, "_claude_cli", lambda: str(real))
    monkeypatch.setattr(coding, "_resolve_folder", lambda name: (str(tmp_path), None))

    seen = {}
    monkeypatch.setattr(coding.subprocess, "Popen",
                        lambda argv, **kw: seen.update(argv=argv) or None)
    coding.ask_claude_code(PAYLOAD, folder="anywhere")
    assert seen["argv"][1] == PAYLOAD, (
        "the brief was altered on the way to a binary that can carry it"
    )


def test_a_batch_shim_gets_a_pointer_rather_than_a_mangled_brief(
        monkeypatch, tmp_path):
    """
    THE FIX FOR THE MACHINE WITHOUT THE NATIVE INSTALL. cmd.exe cannot carry
    a newline in an argument at all, so nothing clever with quoting rescues
    this. The brief goes to a UTF-8 file and the argument becomes a short
    ASCII instruction naming it - which survives cmd.exe intact because
    there is nothing in it left to mangle.
    """
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setattr(coding, "_claude_cli", lambda: str(shim))
    monkeypatch.setattr(coding, "_resolve_folder", lambda name: (str(tmp_path), None))

    seen = {}
    monkeypatch.setattr(coding.subprocess, "Popen",
                        lambda argv, **kw: seen.update(argv=argv) or None)
    coding.ask_claude_code(PAYLOAD, folder="anywhere")

    arg = seen["argv"][1]
    assert arg.isascii(), f"{arg!r} still carries what cmd.exe destroys"
    assert "\n" not in arg, "a newline cannot survive an argument to a .CMD"
    assert "%" not in arg, "cmd.exe would expand this"

    # And the brief itself has to actually be somewhere, in full. The
    # argument names it, so the test does not need to know where that is -
    # which is the point: a scratch file does not belong in his repository.
    import pathlib
    import re

    match = re.search(r"[A-Za-z]:[\\/][^\"']+\.md", arg)
    assert match, f"the argument names no brief file: {arg!r}"
    brief = pathlib.Path(match.group(0))
    assert brief.is_file(), f"{brief} was named but never written"
    assert brief.read_text(encoding="utf-8") == PAYLOAD
    assert tmp_path not in brief.parents, (
        "the brief was dropped into his working folder - a scratch file "
        "does not belong in a repository Claude Code is about to commit"
    )


def test_the_reply_says_which_route_was_used(monkeypatch, tmp_path):
    """
    Silence about a degraded path reads as success. He needs to know the
    brief went via a file, because that is also the prompt to install the
    native binary.
    """
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setattr(coding, "_claude_cli", lambda: str(shim))
    monkeypatch.setattr(coding, "_resolve_folder", lambda name: (str(tmp_path), None))
    monkeypatch.setattr(coding.subprocess, "Popen", lambda argv, **kw: None)

    reply = coding.ask_claude_code(PAYLOAD, folder="anywhere")
    assert "brief" in reply.lower()


def test_an_empty_prompt_is_still_refused_before_anything_launches(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("launched Claude Code on an empty brief")

    monkeypatch.setattr(coding.subprocess, "Popen", explode)
    assert "Tell me what you want" in coding.ask_claude_code("   ")


# ---------------------------------------------------------------------------
# THE GROUND TRUTH, run once so the claim above is not just a story.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe is the mangler")
def test_cmd_exe_really_does_destroy_this_payload(tmp_path):
    """
    Not a test of Jalen - a test of the premise. If a future Windows makes
    .CMD arguments lossless, this fails and the workaround above can go.
    """
    out = tmp_path / "got.txt"
    shim = tmp_path / "shim.cmd"
    shim.write_text(f'@echo off\r\n>"{out}" echo %1\r\n', encoding="utf-8")
    subprocess.run([str(shim), PAYLOAD], capture_output=True, timeout=30)
    got = out.read_text(encoding="utf-8", errors="replace")
    assert "second line" not in got, (
        "cmd.exe no longer truncates at the newline - re-check the workaround"
    )
