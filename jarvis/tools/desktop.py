"""
Desktop control via UIA — the accessibility tree as text (Phase C).

uiautomation gives Claude the screen as text: far cheaper and faster than
screenshots into a vision model, and it's what lets Jarvis actually operate
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
    "Jarvis can turn on after the fact). Close Chrome and relaunch it with "
    "that flag if you need Jarvis to read the page — otherwise UIA only sees "
    "the window chrome (tabs, address bar), not page content."
)


def _require_uia() -> None:
    if auto is None:
        raise RuntimeError("uiautomation isn't installed. Run: pip install uiautomation")


def _find_window(name: str):
    win = auto.WindowControl(searchDepth=1, RegexName=f".*{name}.*")
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


# ---------------------------------------------------------------------- read
def get_window_list() -> str:
    """List of visible top-level windows — GREEN, read-only."""
    _require_uia()
    root = auto.GetRootControl()
    names = []
    for child in root.GetChildren():
        try:
            if child.Name and child.BoundingRectangle.width() > 0 and child.BoundingRectangle.height() > 0:
                names.append(f"{child.Name} ({child.ClassName})")
        except Exception:
            continue
    if not names:
        return "No visible windows found."
    return "Open windows: " + "; ".join(names[:40])


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
