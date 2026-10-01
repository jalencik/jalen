"""
Desktop control via UIA — the accessibility tree as text (Phase C).

uiautomation gives Claude the screen as text: far cheaper and faster than
screenshots into a vision model, and it's what lets Jalen actually operate
apps rather than just describe them.

Two hard-won details, confirmed live against real Notepad/Chrome/Explorer/
VS Code windows on this machine, not assumed from the docs:

  - Chrome does not populate its UIA tree unless launched with
    --force-renderer-accessibility. Without it, read_screen on a Chrome
    window returns almost nothing below the window/tab-strip level — that's
    Chrome's default behaviour, not a bug here.
  - It's not just Chrome. VS Code is Electron — same Chromium engine, same
    class name (Chrome_WidgetWin_1) — and its tree comes back just as sparse
    (a handful of window-chrome buttons: Minimize/Maximize/Restore/Close,
    nothing of the actual editor). Detecting "is this Chrome" by class name
    or window title alone would have quietly missed VS Code and reported it
    as fine. The check below instead looks at how many nodes in the tree
    actually have a name — window-chrome buttons alone clear a naive node
    COUNT easily, they just don't clear a MEANING bar — and only claims the
    specific --force-renderer-accessibility fix when the process is
    genuinely chrome.exe (via psutil, already a hard dependency). For any
    other sparse-tree app, it says so honestly without guessing at a fix
    that hasn't been verified for that app.
  - WalkTree + GetFirstChildControl/GetNextSiblingControl is the reliable
    way to enumerate a full subtree; there's no built-in "tree as string"
    helper in this library version, so it's built here.

Windows-only, like the rest of jarvis/tools/system.py.
"""
from __future__ import annotations

import ctypes
import os
import re
from typing import Any

try:
    import uiautomation as auto
except ImportError:  # pragma: no cover
    auto = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

# Below this many NAMED nodes (root excluded), a tree is functionally empty —
# calibrated against real windows: Chrome ~2-3 named nodes, VS Code's welcome
# screen ~5 (four window-chrome buttons plus its own title pane), Notepad
# ~15+. There's a wide, comfortably-separated gap between "just window chrome"
# and "an app that actually exposes its content".
SPARSE_TREE_NAMED_NODE_THRESHOLD = 8

CHROME_ACCESSIBILITY_HINT = (
    "Chrome's accessibility tree comes back empty unless Chrome was launched "
    "with --force-renderer-accessibility (a Chrome launch flag, not something "
    "Jalen can turn on after the fact). Close Chrome and relaunch it with "
    "that flag if you need Jalen to read the page — otherwise UIA only sees "
    "the window chrome (tabs, address bar), not page content."
)


def _require_uia() -> None:
    if auto is None:
        raise RuntimeError("uiautomation isn't installed. Run: pip install uiautomation")


def _find_window(name: str):
    # Case-insensitive: the router lowercases every utterance, so a
    # case-sensitive pattern never matched real window titles. See
    # system.py's _title_pattern for the full explanation.
    import re as _re

    win = auto.WindowControl(searchDepth=1, RegexName=_re.compile(f".*{_re.escape(name)}.*", _re.I))
    return win if win.Exists(2, 0.3) else None


def _tree_as_text(control, max_depth: int = 6, max_nodes: int = 300) -> tuple[str, int, int]:
    """Returns (text, node_count, named_node_count). named_node_count excludes
    the root itself and is what actually distinguishes a useful tree from
    Chrome's stack of anonymous PaneControls with no accessibility data —
    raw node count alone doesn't: that stack is several nodes deep, just
    empty ones."""
    lines: list[str] = []
    count = 0
    named = 0
    for node, depth in auto.WalkTree(
        control,
        getFirstChild=lambda c: c.GetFirstChildControl(),
        getNextSibling=lambda c: c.GetNextSiblingControl(),
        includeTop=True,
        maxDepth=max_depth,
    ):
        count += 1
        if count > max_nodes:
            lines.append("... (truncated — narrow the window or increase max_nodes)")
            break
        name = (node.Name or "").strip()
        if name and depth > 0:
            named += 1
        if len(name) > 90:
            name = name[:90] + "…"
        line = "  " * depth + f"[{node.ControlTypeName}] {name}".rstrip()
        lines.append(line)
    return "\n".join(lines), count, named


# ---------------------------------------------------- who owns a window
# A window's CLASS says which toolkit drew it, not which program it is. Every
# Chromium and Electron app shares one class, Chrome_WidgetWin_1: Chrome, VS
# Code, Slack and the Claude desktop app. get_window_list reported
# "Claude (Chrome_WidgetWin_1)" and the brain, reading that, called Claude's
# own app "a Chrome window" (live QA, 2026-10-01). The program is found from the
# window's process instead, and spoken by a friendly name, never by file name.
_FRIENDLY_NAMES = {
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox",
    "brave.exe": "Brave", "opera.exe": "Opera",
    "msedgewebview2.exe": "Edge web content",
    "code.exe": "VS Code", "cursor.exe": "Cursor",
    "windowsterminal.exe": "Windows Terminal", "wt.exe": "Windows Terminal",
    "cmd.exe": "Command Prompt", "conhost.exe": "Console host",
    "powershell.exe": "PowerShell", "pwsh.exe": "PowerShell", "bash.exe": "Bash",
    "python.exe": "Python", "pythonw.exe": "Python", "node.exe": "Node.js", "git.exe": "Git",
    "explorer.exe": "File Explorer", "svchost.exe": "Windows services",
    "dwm.exe": "Windows desktop manager", "taskmgr.exe": "Task Manager",
    "searchhost.exe": "Windows Search", "searchindexer.exe": "Windows Search",
    "searchapp.exe": "Windows Search", "msmpeng.exe": "Windows Defender",
    "runtimebroker.exe": "Windows runtime broker", "sihost.exe": "Windows shell host",
    "onedrive.exe": "OneDrive",
    "vmmem": "WSL virtual machine", "vmmemwsl": "WSL virtual machine", "wslhost.exe": "WSL",
    "docker desktop.exe": "Docker Desktop", "com.docker.backend.exe": "Docker Desktop",
    "telegram.exe": "Telegram", "slack.exe": "Slack", "discord.exe": "Discord",
    "teams.exe": "Microsoft Teams", "ms-teams.exe": "Microsoft Teams", "zoom.exe": "Zoom",
    "spotify.exe": "Spotify", "vlc.exe": "VLC", "aimp.exe": "AIMP",
    "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint",
    "outlook.exe": "Outlook", "onenote.exe": "OneNote",
    "notepad.exe": "Notepad", "calc.exe": "Calculator", "calculatorapp.exe": "Calculator",
    "mspaint.exe": "Paint", "snippingtool.exe": "Snipping Tool",
    "photoshop.exe": "Photoshop", "obs64.exe": "OBS Studio", "steam.exe": "Steam",
}
# Not programs anyone asked about: the system's own bookkeeping.
_NOT_PROGRAMS = {"system idle process", "system", "registry", "memory compression", "secure system"}
# The desktop and the taskbar: windows that are always there and are not "open apps".
_SHELL_WINDOW_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
# Store apps (Calculator, Settings...) draw their window inside this host, so the
# window's process is the host and not the app. The window title is the name.
_STORE_APP_HOST = "applicationframehost.exe"
MAX_LISTED_WINDOWS = 40
MAX_TITLE_CHARS = 90


def _prettify(process_name: str) -> str:
    stem = re.sub(r"\.exe$", "", process_name or "", flags=re.I).strip()
    if not stem:
        return "an unknown program"
    stem = " ".join(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem.replace("_", " ").replace("-", " ")).split())
    return stem[0].upper() + stem[1:] if stem.islower() else stem


def friendly_program_name(process_name: str, exe_path: str = "", window_title: str = "") -> str:
    """
    What to call a program out loud: 'Google Chrome', not chrome.exe, and not
    Chrome_WidgetWin_1. claude.exe is two different programs on this machine -
    the Claude desktop app and the Claude Code CLI Jalen spawns for his brain
    - told apart by where the file lives.
    """
    low = (process_name or "").lower()
    if low == _STORE_APP_HOST:
        return " ".join((window_title or "").split()) or "a Windows app"
    if low == "claude.exe":
        path = (exe_path or "").lower().replace("/", "\\")
        return "Claude Code" if ("claude-code" in path or "claude_agent_sdk" in path) else "Claude"
    return _FRIENDLY_NAMES.get(low) or _prettify(process_name)


def _friendly_for_pid(pid: int, title: str = "") -> str:
    """The friendly name of the program that owns this process, or '' if it
    cannot be read (a protected or already-gone process)."""
    if psutil is None or not pid:
        return ""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
    except Exception:
        return ""
    try:
        exe = proc.exe()
    except Exception:
        exe = ""
    return friendly_program_name(name, exe, title)


def _short_title(title: str, program: str = "") -> str:
    """A window title as it is spoken: spaces collapsed, the program's own name
    trimmed off the end ('Inbox - Gmail - Google Chrome'), and capped."""
    text = " ".join((title or "").split())
    for dash in (" - ", " — ", " – "):
        if program and text.lower().endswith(dash + program.lower()):
            text = text[: -len(dash + program)]
            break
    return text if len(text) <= MAX_TITLE_CHARS else text[:MAX_TITLE_CHARS] + "..."


def _window_label(title: str, program: str) -> str:
    """'Inbox - Gmail in Google Chrome'; just 'Claude' when title and program agree."""
    short = _short_title(title, program)
    if not program or short.lower() == program.lower():
        return short or program
    return f"{short} in {program}"


def _foreground_hwnd() -> int:
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _foreground_window() -> dict | None:
    """Title, process id and class of the window that is in front right now, from
    GetForegroundWindow. None when Windows says nothing is (a moment between
    windows) or the call fails."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {"hwnd": int(hwnd), "title": title.value, "pid": int(pid.value), "cls": cls.value}
    except Exception:
        return None


def _visible_windows() -> list[tuple[int, str]]:
    """(process id, title) for every visible, titled, uncloaked top-level window
    that is not the desktop or the taskbar. A Store app that is minimised or on
    another virtual desktop is 'cloaked': visible to IsWindowVisible, not to him."""
    found: list[tuple[int, str]] = []
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi

        def visit(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                cloaked = ctypes.c_int(0)
                dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
                if cloaked.value:
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                if cls.value in _SHELL_WINDOW_CLASSES or title.value == "Program Manager":
                    return True
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((int(pid.value), title.value))
            except Exception:
                pass
            return True

        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(visit)
        user32.EnumWindows(callback, 0)
    except Exception:
        pass
    return found


# ---------------------------------------------------------------------- read
def get_window_list() -> str:
    """
    The windows open on screen, which one is in front, and which program each
    belongs to - GREEN, read-only. The desktop, the taskbar and Jalen's own
    windows are left out: they are always there and nobody asked about them.
    """
    _require_uia()
    front = _foreground_hwnd()
    me = os.getpid()
    shell_seen = False
    in_front = ""
    others: list[str] = []
    for child in auto.GetRootControl().GetChildren():
        try:
            if child.ClassName in _SHELL_WINDOW_CLASSES:
                shell_seen = True
                continue
            title = (child.Name or "").strip()
            rect = child.BoundingRectangle
            if not title or rect.width() <= 0 or rect.height() <= 0:
                continue
            if title == "Program Manager":
                shell_seen = True
                continue
            pid = child.ProcessId
            if pid == me:
                continue
            label = _window_label(title, _friendly_for_pid(pid, title))
            if front and child.NativeWindowHandle == front and not in_front:
                in_front = label
            elif len(others) < MAX_LISTED_WINDOWS:
                others.append(label)
        except Exception:
            continue
    if not in_front and not others:
        return ("Nothing is open except the desktop and the taskbar." if shell_seen
                else "No visible windows found.")
    if not in_front:
        return "Open windows: " + "; ".join(others) + "."
    return f"In front: {in_front}." + (f" Also open: {'; '.join(others)}." if others else "")


def foreground_app() -> str:
    """Which program is in front right now - GREEN, read-only."""
    info = _foreground_window()
    if not info:
        return "I can't tell which window is in front right now."
    title = " ".join((info.get("title") or "").split())
    if info.get("cls") in _SHELL_WINDOW_CLASSES or title == "Program Manager":
        return "Nothing is in front - you're on the desktop."
    pid = info.get("pid") or 0
    if pid == os.getpid():
        return "One of Jalen's own windows is in front."
    program = _friendly_for_pid(pid, title)
    if not program:
        return (f"The window titled {_short_title(title)} is in front." if title
                else "I can't tell which program is in front.")
    short = _short_title(title, program)
    if not short or short.lower() == program.lower():
        return f"{program} is in front."
    return f"{program} is in front. The window is titled {short}."


def running_programs(top_n: int = 8) -> str:
    """
    The programs running now, grouped (Chrome's twenty processes are one
    program), by friendly name, biggest memory first, those with a window open
    first - GREEN, read-only. This is what "what programs are running" means;
    get_window_list is the windows and memory_report is the memory totals.
    """
    if psutil is None:
        return "I can't read the running programs - psutil isn't installed."
    from .sysinfo import _size_str

    try:
        top_n = max(1, min(int(top_n), 20))
    except (TypeError, ValueError):
        top_n = 8

    windows = _visible_windows()
    with_window = {pid for pid, _ in windows}
    # name -> [memory in bytes, has a window, memory is known]
    groups: dict[str, list] = {}
    for pid, title in windows:
        # A Store app's window belongs to the shared host; the host's memory is
        # not the app's, so the app is named and no size is claimed for it.
        if _friendly_for_pid(pid) == friendly_program_name(_STORE_APP_HOST, "", ""):
            groups.setdefault(_short_title(title), [0, True, False])
    try:
        processes = list(psutil.process_iter(["pid", "name", "exe", "memory_info"]))
    except Exception:
        return "I couldn't read the list of running programs."
    for proc in processes:
        try:
            info = proc.info
            name = info.get("name") or ""
            if not name or name.lower() in _NOT_PROGRAMS or name.lower() == _STORE_APP_HOST:
                continue
            memory = info.get("memory_info")
            entry = groups.setdefault(friendly_program_name(name, info.get("exe") or ""), [0, False, True])
            entry[0] += memory.rss if memory else 0
            entry[1] = entry[1] or info.get("pid") in with_window
        except Exception:
            continue
    if not groups:
        return "I couldn't read the list of running programs."

    def line(items) -> str:
        return ", ".join(f"{name} at {_size_str(entry[0])}" if entry[2] else name
                         for name, entry in items)

    by_size = sorted(groups.items(), key=lambda kv: kv[1][0], reverse=True)
    windowed = [(n, e) for n, e in by_size if e[1]][:top_n]
    background = [(n, e) for n, e in by_size if not e[1]][:3]
    text = f"{len(groups)} programs are running."
    text += (f" With a window open: {line(windowed)}." if windowed
             else " Nothing has a window open.")
    if background:
        text += f" In the background, the biggest are {line(background)}."
    return text


def read_screen(window: str | None = None, max_depth: int = 6, max_nodes: int = 300) -> str:
    """
    The UIA tree of a window as text — GREEN, read-only.
    window: substring of the window title. None = the current foreground window.
    """
    _require_uia()
    control = _find_window(window) if window else auto.GetForegroundControl().GetTopLevelControl()
    if control is None:
        return f"I can't find a window called {window}."

    text, count, named = _tree_as_text(control, max_depth=max_depth, max_nodes=max_nodes)
    if named > SPARSE_TREE_NAMED_NODE_THRESHOLD:
        return text or "(empty — nothing readable in this window's accessibility tree)"

    process_name = _process_name(control)
    if process_name == "chrome.exe":
        return (text + "\n\n" if text else "") + CHROME_ACCESSIBILITY_HINT
    return (text + "\n\n" if text else "") + (
        f"This window's UIA tree exposes almost nothing beyond its own frame "
        f"(only {named} named element(s)) — "
        + (f"{process_name} " if process_name else "")
        + "likely doesn't support accessibility, or needs it turned on in its "
        "own settings. I can't say more without checking that app specifically."
    )


def _process_name(control) -> str | None:
    if psutil is None:
        return None
    try:
        pid = control.ProcessId
        if not pid:
            return None
        return psutil.Process(pid).name()
    except Exception:
        return None


# ------------------------------------------------------------------- act
def click_element(window: str, name: str, control_type: str | None = None) -> str:
    """Click a control by (partial, case-sensitive-regex) name inside a window — AMBER."""
    _require_uia()
    win = _find_window(window)
    if win is None:
        return f"I can't find a window called {window}."

    if control_type:
        ctor = getattr(auto, f"{control_type}Control", None)
        if ctor is None:
            return f"Unknown control type: {control_type}"
        target = ctor(searchFromControl=win, searchDepth=20, RegexName=f".*{name}.*")
    else:
        target = auto.Control(searchFromControl=win, searchDepth=20, RegexName=f".*{name}.*")

    if not target.Exists(3, 0.3):
        return f"I can't find {name!r} in {window}."
    target.Click()
    return f"Clicked {name}."


def type_text(window: str, text: str) -> str:
    """Type text into whatever's focused in a window — AMBER."""
    _require_uia()
    win = _find_window(window)
    if win is None:
        return f"I can't find a window called {window}."
    win.SetActive()
    win.SendKeys(text, waitTime=0.02)
    return f"Typed into {window}."


def keyboard_shortcut(keys: str, window: str | None = None) -> str:
    """
    Send a keyboard shortcut, e.g. keys='{Ctrl}s' — AMBER.
    window: substring of the window title to activate first. None = whatever's
    already focused.
    """
    _require_uia()
    target = _find_window(window) if window else auto.GetForegroundControl().GetTopLevelControl()
    if target is None:
        return f"I can't find a window called {window}."
    target.SetActive()
    target.SendKeys(keys)
    return f"Sent {keys}."


# --------------------------------------------------------------------- dispatch
REGISTRY: dict[str, Any] = {
    "get_window_list": get_window_list,
    "foreground_app": foreground_app,
    "running_programs": running_programs,
    "read_screen": read_screen,
    "click_element": click_element,
    "type_text": type_text,
    "keyboard_shortcut": keyboard_shortcut,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
