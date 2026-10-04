"""
Tests for jalen/tools/desktop.py (Phase C).

Live window interaction (real Notepad/Chrome/VS Code/File Explorer trees,
real typing, real clicks) was verified manually this session — reproducible
here would mean depending on whatever windows happen to be open on the
machine at test time, which isn't stable enough for a real suite. What's
tested here instead:
  - the error paths when a named window genuinely doesn't exist (real,
    deterministic — "a window that certainly isn't open" is a safe thing to
    assert on any machine)
  - the sparse-tree named-node heuristic against a fake control tree, since
    that's the actual logic under test and doesn't need a real window to
    exercise correctly
  - registry dispatch
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.tools import desktop  # noqa: E402

NO_SUCH_WINDOW = "Zzz_This_Window_Certainly_Does_Not_Exist_12345"


# ------------------------------------------------------------- fake control
class _FakeControl:
    """Enough of uiautomation.Control's shape for _tree_as_text to walk."""

    def __init__(self, control_type: str, name: str, children: list["_FakeControl"] | None = None):
        self.ControlTypeName = control_type
        self.Name = name
        self._children = children or []
        for i, child in enumerate(self._children):
            child._next = self._children[i + 1] if i + 1 < len(self._children) else None

    def GetFirstChildControl(self):
        return self._children[0] if self._children else None

    def GetNextSiblingControl(self):
        return getattr(self, "_next", None)


def _chrome_like_tree() -> _FakeControl:
    """Mirrors what was actually observed live: a WindowControl with a
    handful of anonymous panes and four named window-chrome buttons —
    structurally several nodes deep, but almost nothing named."""
    buttons = [
        _FakeControl("ButtonControl", "Minimize"),
        _FakeControl("ButtonControl", "Maximize"),
        _FakeControl("ButtonControl", "Restore"),
        _FakeControl("ButtonControl", "Close"),
    ]
    inner = _FakeControl("PaneControl", "", [_FakeControl("PaneControl", "", buttons)])
    root = _FakeControl("WindowControl", "Welcome - Visual Studio Code", [inner])
    return root


def _rich_tree() -> _FakeControl:
    """Mirrors real Notepad: many named, meaningful nodes."""
    names = ["Text Editor", "Line up", "Line down", "Column left", "Column right",
             "Ln 1, Col 1", "100%", "Windows (CRLF)", "UTF-8", "File", "Edit",
             "Format", "View", "Help", "Minimize", "Maximize", "Close"]
    children = [_FakeControl("Control", n) for n in names]
    return _FakeControl("WindowControl", "Untitled - Notepad", children)


def test_sparse_tree_heuristic_flags_window_chrome_only():
    text, count, named = desktop._tree_as_text(_chrome_like_tree())
    assert named <= desktop.SPARSE_TREE_NAMED_NODE_THRESHOLD
    assert count > named  # the anonymous panes are real nodes, just unnamed


def test_sparse_tree_heuristic_does_not_flag_a_real_app():
    text, count, named = desktop._tree_as_text(_rich_tree())
    assert named > desktop.SPARSE_TREE_NAMED_NODE_THRESHOLD


def test_tree_as_text_truncates_long_names():
    long_name = "x" * 200
    tree = _FakeControl("WindowControl", long_name, [])
    text, count, named = desktop._tree_as_text(tree)
    assert "…" in text
    assert long_name not in text


# ------------------------------------------------------- live error paths
def test_read_screen_missing_window():
    result = desktop.read_screen(window=NO_SUCH_WINDOW)
    assert "can't find" in result.lower()


def test_click_element_missing_window():
    result = desktop.click_element(NO_SUCH_WINDOW, "OK")
    assert "can't find" in result.lower()


def test_type_text_missing_window():
    result = desktop.type_text(NO_SUCH_WINDOW, "hello")
    assert "can't find" in result.lower()


def test_keyboard_shortcut_missing_window():
    result = desktop.keyboard_shortcut("{Ctrl}s", window=NO_SUCH_WINDOW)
    assert "can't find" in result.lower()


def test_get_window_list_returns_something_real():
    """There's always at least the desktop/taskbar on a real Windows session."""
    result = desktop.get_window_list()
    assert result and result != "No visible windows found."


# ------------------------------------------------------------------ dispatch
def test_registry_covers_all_five_desktop_tools():
    for name in ("get_window_list", "read_screen", "click_element", "type_text", "keyboard_shortcut"):
        assert name in desktop.REGISTRY


def test_call_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError):
        desktop.call("not_a_real_tool", {})


def test_process_name_returns_none_for_bogus_pid():
    class _Bogus:
        ProcessId = 999_999_999  # exceedingly unlikely to be a real running pid

    assert desktop._process_name(_Bogus()) is None
