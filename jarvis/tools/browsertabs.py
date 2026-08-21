"""
Telling one browser window from another.

He asked for it in plain terms: Jalen "should recognize youtube window of a
chrome, or common app window of a chrome, when closing chrome, it should be
able to remove according pages or windows".

The existing close_app matches a window by title, which sounds like it
already does this — and does not. "Close Chrome" matches the FIRST Chrome
window it finds, which on a machine with six of them open is a coin toss,
and the one it picks might be the research he has spent an hour on.

WHAT WINDOWS ACTUALLY EXPOSES, AND WHAT IT DOES NOT
---------------------------------------------------
Every top-level browser window has a title, and that title is its ACTIVE
tab: "YouTube - Google Chrome", "Common App - Google Chrome". So windows are
addressable by what they are showing, which is how he thinks about them.

Individual background tabs are NOT addressable. Chrome does not publish its
tab strip to the accessibility tree in any usable way, and the honest
consequence is written into every reply here: "the YouTube window" means the
window currently showing YouTube, and if YouTube is a background tab in a
window showing something else, this will not find it. Saying that plainly
beats a tool that silently closes the wrong window.

CLOSING IS BY EXACT WINDOW HANDLE
---------------------------------
Once a window is identified it is closed by its handle, not by re-matching
its title. Between finding and closing, he might switch tabs — and a second
title match could then land on a different window entirely.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import time
from typing import Any

from .system import IS_WINDOWS

# THE WINDOW CLASS IS NOT ENOUGH, and trusting it was a real bug caught on
# the first live run: "Chrome_WidgetWin_1" belongs to Chromium, and every
# Electron application is Chromium. Visual Studio Code, Slack, Discord and
# the Claude desktop app were all being listed as browser windows — so
# "close the jarvis window" could have closed his editor.
#
# The owning PROCESS is what actually distinguishes them.
BROWSER_CLASSES = {
    "Chrome_WidgetWin_1": "Chrome",     # ...and every Electron app
    "MozillaWindowClass": "Firefox",
}

BROWSER_EXES = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "opera_gx.exe": "Opera GX",
    "vivaldi.exe": "Vivaldi",
}

# Chrome appends its own name to every title. Stripping it makes the spoken
# reply "YouTube" rather than "YouTube - Google Chrome", and matching work
# on what he actually said.
_BROWSER_SUFFIX = re.compile(
    r"\s*[-–—]\s*(?:Google Chrome|Microsoft.​?Edge|Mozilla Firefox|Brave|Opera GX|Opera)\s*$",
    re.I,
)

_user32 = ctypes.windll.user32 if IS_WINDOWS else None
_EnumWindowsProc = (
    ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    if IS_WINDOWS else None
)

WM_CLOSE = 0x0010


def _browser_for(hwnd: int) -> str | None:
    """
    The browser this window belongs to, or None if it is not a browser.

    Looks at the owning process image name. psutil is already a dependency
    (runtime.py uses it) so this costs nothing new, and a lookup failure
    returns None — an unidentifiable window is treated as not-a-browser,
    which fails toward leaving windows alone.
    """
    try:
        import psutil

        pid = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = psutil.Process(pid.value).name().lower()
    except Exception:
        return None
    return BROWSER_EXES.get(name)


def _windows() -> list[tuple[int, str, str]]:
    """Visible browser windows as (handle, page title, browser name)."""
    if not IS_WINDOWS:
        return []
    found: list[tuple[int, str, str]] = []

    def callback(hwnd, _param):
        if not _user32.IsWindowVisible(hwnd):
            return True
        class_name = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value not in BROWSER_CLASSES:
            return True
        browser = _browser_for(hwnd)
        if browser is None:
            return True     # an Electron app wearing Chromium's window class
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True     # a hidden helper window, not a real one
        title = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, title, length + 1)
        page = _BROWSER_SUFFIX.sub("", title.value).strip()
        if page:
            found.append((hwnd, page, browser))
        return True

    _user32.EnumWindows(_EnumWindowsProc(callback), None)
    return found


def list_browser_tabs() -> str:
    """
    Every browser window and what it is showing — GREEN.

    Use before closing anything, so he can hear what is open and pick.
    """
    if not IS_WINDOWS:
        return "Windows only."
    windows = _windows()
    if not windows:
        return "No browser windows are open."
    lines = [f"{len(windows)} browser window(s):"]
    for _hwnd, page, browser in windows:
        lines.append(f"  - {page}   [{browser}]")
    lines.append(
        "That's the page each window is SHOWING. Background tabs aren't "
        "visible to me — Chrome doesn't publish them."
    )
    return "\n".join(lines)


def _match(windows, wanted: str):
    """Windows whose page title contains the words he said."""
    needle = (wanted or "").strip().lower()
    if not needle:
        return []
    exact = [w for w in windows if w[1].lower() == needle]
    if exact:
        return exact
    return [w for w in windows if needle in w[1].lower()]


def focus_browser_tab(page: str) -> str:
    """Bring the browser window showing this page to the front — GREEN."""
    if not IS_WINDOWS:
        return "Windows only."
    matches = _match(_windows(), page)
    if not matches:
        return (
            f"No browser window is showing {page}. It might be a background "
            "tab, which I can't see — bring it to the front and ask again."
        )
    if len(matches) > 1:
        names = ", ".join(m[1] for m in matches[:5])
        return f"More than one window matches {page}: {names}. Which one?"
    hwnd, title, _browser = matches[0]
    _user32.ShowWindow(hwnd, 9)          # SW_RESTORE, in case it is minimised
    _user32.SetForegroundWindow(hwnd)
    return f"Brought {title} to the front."


def close_browser_tab(page: str) -> str:
    """
    Close the browser window showing this page — AMBER.

    AMBER rather than GREEN because a page can hold unsaved work — a
    half-written application, a form, a draft — and the browser's own "leave
    site?" prompt is not a substitute for him hearing which window is about
    to go.

    Refuses when several windows match. Closing one of three windows whose
    titles all contain "google" and reporting success would be exactly the
    coin-toss this module exists to remove.
    """
    if not IS_WINDOWS:
        return "Windows only."
    windows = _windows()
    matches = _match(windows, page)
    if not matches:
        open_now = ", ".join(w[1] for w in windows[:5]) or "none"
        return (
            f"No browser window is showing {page}, so I've closed nothing. "
            f"Open right now: {open_now}."
        )
    if len(matches) > 1:
        names = ", ".join(m[1] for m in matches[:5])
        return (
            f"{len(matches)} windows match {page} — {names}. I haven't closed "
            "any; tell me which one and I'll close that."
        )

    hwnd, title, _browser = matches[0]
    # WM_CLOSE, not TerminateProcess: it is the same as clicking the X, so
    # the page's own "you have unsaved changes" prompt still gets to appear.
    _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    # Verify. A window that refuses to close because a dialog appeared is a
    # normal outcome, and reporting it as closed would be a lie he only
    # discovers later.
    for _ in range(20):
        time.sleep(0.1)
        if not any(h == hwnd for h, _t, _b in _windows()):
            return f"Closed the {title} window."
    return (
        f"I asked the {title} window to close and it's still there — it's "
        "probably showing a 'leave site?' prompt. Have a look."
    )


REGISTRY: dict[str, Any] = {
    "list_browser_tabs": list_browser_tabs,
    "focus_browser_tab": focus_browser_tab,
    "close_browser_tab": close_browser_tab,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
