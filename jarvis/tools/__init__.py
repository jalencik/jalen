"""
Unified tool dispatch. system.py (Phase 0/1), desktop.py and filesystem.py
(Phase C), and memory.py (Phase E) each own a REGISTRY of their own tools;
this module merges them into one name -> function map so callers (the
router's handle_local(), and the Brain's SDK tool wrappers) have a single
place to call any tool by name without needing to know which module it
lives in.

Tool names must be unique across all registries — a collision would
silently shadow one implementation with another, so it's asserted at import
time rather than left to be discovered at call time.
"""
from __future__ import annotations

import contextlib
from typing import Any

from . import (
    coding, desktop, documents, filesystem, gcalendar, gmail, handoff,
    launcher, memory, messaging, research, selfeval, sysinfo, system,
    voice, web,
)


@contextlib.contextmanager
def com_initialized():
    """
    COM must be explicitly initialized (CoInitialize) on any thread that's
    going to touch a COM-based tool — desktop.py's UIA calls, and
    system.py's focus_window/window_state/volume_set. Python does not do
    this automatically on a freshly spawned thread; it only appears to work
    on a process's original thread. Confirmed live: calling focus_window()
    from a plain threading.Thread (exactly what app.py's per-turn dispatch
    and the SDK tool wrapper both do) raised "CoInitialize has not been
    called" — not a crash (each tool's own try/except catches it), but a
    silent functional failure: the tool always fails, with a confusing
    error, no matter how correctly it was called.

    Every place that spawns a worker thread to run a turn or a tool call
    must wrap the thread's entry point in this — once, here, rather than
    have every current and future COM-touching tool remember to do it
    itself. No-ops if uiautomation isn't installed.
    """
    try:
        import uiautomation as auto
    except ImportError:
        yield
        return
    initializer = auto.UIAutomationInitializerInThread()
    try:
        yield
    finally:
        initializer.Uninitialize()

REGISTRY: dict[str, Any] = {}
for _module in (
    system, desktop, filesystem, documents, memory, launcher, sysinfo, web,
    gmail, gcalendar, messaging, research, voice, coding, selfeval, handoff,
):
    _collisions = set(REGISTRY) & set(_module.REGISTRY)
    if _collisions:
        raise RuntimeError(f"Tool name collision across modules: {_collisions}")
    REGISTRY.update(_module.REGISTRY)


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
