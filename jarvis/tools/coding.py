"""
Handing a job to Claude Code by voice.

He describes what he wants in his own words; Jalen opens Claude Code in
the right folder and starts it on that prompt.

WHY THIS LAUNCHES A PROCESS INSTEAD OF TYPING INTO A WINDOW
-----------------------------------------------------------
The obvious build is desktop automation: open a terminal, find the window,
type the prompt, press Enter. Jalen already has type_text and
keyboard_shortcut, so it looks like free reuse.

It is not. That approach has to guess which window is focused, race the
shell's startup before it starts typing, and survive the prompt containing
a quote or a newline — and every one of those failures is silent and looks
identical to success from Jalen's side. The audit log already contains one
of these: asked to play a song, Jalen "typed and cancelled" and reported
that it had played it.

The CLI takes an initial prompt as an argument. Passing it as an argv entry
means the shell never parses it, so quotes, apostrophes and newlines in
whatever he dictated cannot break the command or, worse, be interpreted by
it. There is no window to find and no race to lose.

TIER: AMBER, deliberately
-------------------------
This starts an autonomous agent with its own tool access on his machine, so
GREEN would be too casual. But RED — a spoken "confirm?" on every single
"ask Claude to look at this" — is the friction that made the earlier build
unusable, and Claude Code runs its own permission prompts for anything
destructive anyway. AMBER announces the folder and the prompt and gives him
two seconds to say stop, which is the honest middle: he hears exactly what
is about to be launched, and it costs him nothing when it is right.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .launcher import find_files

# npm's global bin is not always on the PATH that a GUI-launched Python
# inherits, so look there explicitly before giving up.
_NPM_BIN = Path(os.environ.get("APPDATA", "")) / "npm"
_CANDIDATES = ["claude.exe", "claude", "claude.cmd"]

# THE SAME BINARY THE BRAIN ITSELF RUNS. claude-agent-sdk ships the CLI
# inside the wheel and resolves it before PATH, which is why CLAUDE.md says
# to verify auth against this path rather than against `where claude`. It is
# a real executable, so it is the answer to the problem below whenever the
# SDK is installed - which is always, since the brain will not start without
# it.
_BUNDLED_CLI = (
    Path(__file__).resolve().parent.parent.parent
    / ".venv" / "Lib" / "site-packages" / "claude_agent_sdk"
    / "_bundled" / "claude.exe"
)

_BATCH_SUFFIXES = (".cmd", ".bat")


def _is_batch(path: str) -> bool:
    return str(path).lower().endswith(_BATCH_SUFFIXES)


def _claude_cli() -> str | None:
    """
    A real executable if one exists, and only then the npm batch shim.

    THE ORDER IS THE FIX. npm's Windows install is a claude.CMD, Windows can
    only run a .CMD through cmd.exe, and cmd.exe owns the argument before
    the script ever sees it - it truncates at the first newline, flattens an
    em dash, strips accented characters to whatever the console codepage
    carries, and expands %VAR%. The brief handed to Claude Code is a
    multi-paragraph prompt written by the model, so every line after the
    first was being discarded in silence.

    jarvis/brain/agent.py has refused a .cmd outright since the CLI-path
    work ("a batch script the Agent SDK refuses to spawn"). This module
    never got the message: _CANDIDATES used to list claude.cmd FIRST, and
    shutil.which finds it on PATH ahead of everything.
    """
    for name in ("claude.exe", "claude"):
        found = shutil.which(name)
        if found and not _is_batch(found):
            return found
    if _BUNDLED_CLI.is_file():
        return str(_BUNDLED_CLI)
    for name in _CANDIDATES:
        candidate = _NPM_BIN / name
        if candidate.is_file() and not _is_batch(candidate):
            return str(candidate)
    # Nothing but a shim. Still returned - "I can't find the CLI" would be a
    # worse answer than a working launch with the brief delivered by file.
    found = shutil.which("claude")
    if found:
        return found
    for name in _CANDIDATES:
        candidate = _NPM_BIN / name
        if candidate.is_file():
            return str(candidate)
    return None


def _brief_on_disk(text: str) -> Path | None:
    """
    Park the brief in a UTF-8 file and return where.

    For the machine that has only the npm shim. cmd.exe cannot carry a
    newline in an argument at all, so no amount of quoting rescues the
    payload - the brief has to travel some other way, and the argument
    becomes a short ASCII sentence naming the file. That sentence survives
    cmd.exe because there is nothing left in it to mangle.

    The system temp directory, never the working folder: Claude Code is
    about to start work in a repository, and a scratch file dropped into it
    is one more thing for him to find in a diff.
    """
    import tempfile
    import uuid

    try:
        target = Path(tempfile.gettempdir()) / f"jalen-brief-{uuid.uuid4().hex[:8]}.md"
        if not str(target).isascii():
            # A non-ASCII temp path would be destroyed on the way in just
            # like the brief was. Better to say so than to send a path that
            # will not resolve.
            return None
        target.write_text(text, encoding="utf-8")
        return target
    except OSError:
        return None


def _resolve_folder(name: str) -> tuple[str | None, str | None]:
    """
    Turn "the eco pulse folder" into a real path.

    Returns (path, problem). Never guesses between two equally good matches:
    starting an autonomous coding agent in the wrong repository is a much
    more expensive mistake than asking which one he meant.
    """
    raw = (name or "").strip().strip('"').strip("'")
    if not raw:
        return str(Path.cwd().resolve()), None

    # .resolve() matters: a bare "jarvis" is a valid RELATIVE directory, so
    # this would otherwise hand Popen a path interpreted against whatever
    # cwd Jalen happened to be launched from — which, started from the
    # tray or an autostart entry, is not the project folder at all. The
    # agent would then start in the wrong place while reporting the right
    # one.
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if expanded.is_dir():
        return str(expanded.resolve()), None
    if expanded.is_file():
        return str(expanded.resolve().parent), None

    hits = [h for h in find_files(raw, dirs_only=True) if Path(h).is_dir()]
    if not hits:
        return None, f"I couldn't find a folder called {raw!r}."
    if len(hits) > 1:
        best = Path(hits[0]).name.lower()
        rival = Path(hits[1]).name.lower()
        if best == rival:
            names = ", ".join(hits[:3])
            return None, (
                f"There's more than one folder called {raw!r}: {names}. "
                "Which one?"
            )
    return str(Path(hits[0]).resolve()), None


def ask_claude_code(prompt: str, folder: str = "", agent: str = "") -> str:
    """
    Open Claude Code in a folder and start it on a prompt.

    `agent` is optional and passed through as a slash command ("cowork",
    "code"), for "tell Claude to use cowork on this".
    """
    text = (prompt or "").strip()
    if not text:
        return "Tell me what you want Claude to do and I'll start it."

    cli = _claude_cli()
    if cli is None:
        return (
            "I can't find the Claude Code CLI on this machine. It's normally "
            "at AppData\\Roaming\\npm\\claude.cmd — install it with "
            "npm install -g @anthropic-ai/claude-code."
        )

    path, problem = _resolve_folder(folder)
    if problem:
        return problem

    slash = (agent or "").strip().lstrip("/")
    initial = f"/{slash} {text}" if slash else text

    # THE SHIM ROUTE. Only when no real executable exists on this machine -
    # see _claude_cli. The brief goes to a file and the argument becomes
    # something cmd.exe cannot damage.
    brief = None
    if _is_batch(cli):
        brief = _brief_on_disk(initial)
        if brief is None:
            return (
                "Claude Code is only installed here as an npm batch shim, "
                "which cannot carry a multi-line brief, and I couldn't park "
                "the brief in a file either. Install the native binary with "
                "irm https://claude.ai/install.ps1 | iex and I'll hand it "
                "over intact."
            )
        initial = f"Read the brief at {brief} and carry it out."

    # A NEW console window, so Claude Code gets a real terminal to draw its
    # interface in and keeps running after Jalen's call returns. Without
    # CREATE_NEW_CONSOLE it inherits Jalen's (which has no visible window
    # when Jalen runs from the launcher) and he sees nothing happen at all.
    creation = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.Popen(
            [cli, initial],
            cwd=path,
            creationflags=creation,
            close_fds=True,
        )
    except OSError as exc:
        return f"I couldn't start Claude Code ({type(exc).__name__}: {exc})."

    where = Path(path).name or path
    what = f" using {slash}" if slash else ""
    # SAY WHEN THE DEGRADED ROUTE WAS USED. Silence about it reads as
    # success, and it is also the only prompt he will get to install the
    # native binary.
    via = (
        f" I sent it as a brief on disk, because Claude Code here is an npm "
        f"batch shim that would cut the prompt at the first line."
        if brief is not None else ""
    )
    return (
        f"Claude Code is starting in {where}{what}, working on: {text}{via}"
    )


def claude_code_status() -> str:
    """Is the CLI installed, and where."""
    cli = _claude_cli()
    if cli is None:
        return (
            "Claude Code isn't installed, or isn't on PATH. Install it with "
            "npm install -g @anthropic-ai/claude-code."
        )
    return f"Claude Code CLI found at {cli}."


REGISTRY: dict[str, Any] = {
    "ask_claude_code": ask_claude_code,
    "claude_code_status": claude_code_status,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
