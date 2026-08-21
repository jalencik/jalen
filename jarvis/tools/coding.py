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
_CANDIDATES = ["claude.cmd", "claude.exe", "claude"]


def _claude_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for name in _CANDIDATES:
        candidate = _NPM_BIN / name
        if candidate.is_file():
            return str(candidate)
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
    return (
        f"Claude Code is starting in {where}{what}, working on: {text}"
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
