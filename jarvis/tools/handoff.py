"""
"Hand this task to cowork." / "Hand this off to code."

Two jargon phrases, two destinations, one shape: take what he has been
talking about, turn it into a properly engineered prompt, and start the
right agent on it.

    cowork  ->  the Claude desktop app, where Cowork lives.
    code    ->  Claude Code, the CLI, in a project folder.

THE CLIPBOARD IS THE CONTRACT, NOT THE KEYSTROKES
-------------------------------------------------
The desktop app has no documented way to be launched with a prompt already
in it. `claude://` is registered and takes the URI as argv, but no published
format carries a payload, and inventing one means a link that silently opens
an empty window — indistinguishable from success.

So the prompt goes on the CLIPBOARD first, before anything is launched, and
only then is the app opened and a paste sent. If the paste lands, he sees
the prompt in the box. If focus was somewhere else, or the app was slow to
draw, or Windows swallowed the keystroke, the prompt is still on his
clipboard and one Ctrl+V away — and the reply says so.

That ordering is the whole design. Every desktop-automation failure in this
project so far has been silent: type_text into a window that was not focused,
Tab-and-Enter onto a control that was not there, "I played it" for a video
that never started. A handoff that reports success while the agent sits on an
empty prompt would be the same bug in a new place. Here the worst case is
one keypress, and he is told about it.

WHY THE PROMPT IS WRITTEN BY THE BRAIN
--------------------------------------
"This task" is whatever they were just discussing. Only the brain has that,
and only the brain can turn a rambling voice request into the brief a coding
agent can act on. This module deliberately does no composing — it takes the
finished text and moves it.
"""
from __future__ import annotations

import ctypes
import subprocess
import time
from pathlib import Path
from typing import Any

from .system import IS_WINDOWS

# The Store-app identity for the Claude desktop app, from Get-StartApps.
# Launching by AUMID through the shell is the supported way to start a
# packaged app; the .lnk on his Desktop has an empty target and cannot be
# run directly.
CLAUDE_DESKTOP_AUMID = "Claude_pzs8sxrjxfjjc!Claude"

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def set_clipboard(text: str) -> bool:
    """
    Put text on the Windows clipboard. True if it stuck.

    ctypes rather than a dependency, and CF_UNICODETEXT rather than piping
    to clip.exe: the prompt contains em dashes, quotes and non-ASCII from
    his own speech, and clip.exe mangles those under a legacy code page.
    """
    if not IS_WINDOWS or text is None:
        return False
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    buffer = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buffer)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        return False
    locked = kernel32.GlobalLock(handle)
    if not locked:
        return False
    ctypes.memmove(locked, buffer, size)
    kernel32.GlobalUnlock(handle)

    # Retry: another process holding the clipboard makes OpenClipboard fail,
    # and that is common and momentary rather than an error worth reporting.
    for _ in range(10):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                # Ownership of the memory transfers to the system on success;
                # freeing it here would corrupt the clipboard.
                return bool(user32.SetClipboardData(CF_UNICODETEXT, locked))
            finally:
                user32.CloseClipboard()
        time.sleep(0.05)
    return False


def _launch_desktop_app() -> bool:
    """Start (or foreground) the packaged Claude desktop app."""
    try:
        subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{CLAUDE_DESKTOP_AUMID}"],
            close_fds=True,
        )
        return True
    except OSError:
        return False


def _paste_into_foreground() -> bool:
    """Send Ctrl+V to whatever is focused. Best effort by nature."""
    try:
        from .desktop import keyboard_shortcut

        keyboard_shortcut("ctrl+v")
        return True
    except Exception:
        return False


def hand_off_to_cowork(prompt: str) -> str:
    """
    Open the Claude desktop app with a prepared prompt on the clipboard.

    For "hand this task to cowork". Does NOT press send — he reads it first.
    """
    text = (prompt or "").strip()
    if not text:
        return "Tell me what the task is and I'll write it up for Cowork."
    if not IS_WINDOWS:
        return "The Claude desktop app handoff is Windows-only."

    on_clipboard = set_clipboard(text)
    if not _launch_desktop_app():
        return (
            "I couldn't start the Claude desktop app."
            + (" The prompt is on your clipboard." if on_clipboard else "")
        )

    # The app needs a moment to draw and take focus before a paste can land.
    time.sleep(3.0)
    pasted = _paste_into_foreground()

    words = len(text.split())
    if on_clipboard and pasted:
        return (
            f"Claude's open with the brief pasted in — {words} words. "
            "Read it and press send when you're happy. If the box looks "
            "empty, hit Ctrl+V; it's on your clipboard."
        )
    if on_clipboard:
        return (
            f"Claude's open and the {words}-word brief is on your clipboard. "
            "Press Ctrl+V to drop it in — I couldn't send the paste myself."
        )
    return (
        "Claude's open, but I couldn't reach your clipboard, so the brief "
        "didn't make it across. It's on screen if you want to read it back."
    )


def hand_off_to_code(prompt: str, folder: str = "") -> str:
    """
    Start Claude Code on a prepared prompt.

    For "hand this off to code". Goes through ask_claude_code, which passes
    the prompt as an argv entry so quotes and newlines in dictated text
    cannot break or be reinterpreted by a shell — deterministic in a way no
    paste can be. The clipboard copy is a courtesy so he can paste it
    somewhere else if he wants it.
    """
    text = (prompt or "").strip()
    if not text:
        return "Tell me what the task is and I'll write it up for Claude Code."

    set_clipboard(text)
    from .coding import ask_claude_code

    return ask_claude_code(prompt=text, folder=folder)


REGISTRY: dict[str, Any] = {
    "hand_off_to_cowork": hand_off_to_cowork,
    "hand_off_to_code": hand_off_to_code,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
