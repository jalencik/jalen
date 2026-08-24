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
import time
from collections import deque
from typing import Any

from . import (
    agents,
    webagent,
    feedback,
    webforms,
    tasks,
    attachments, autofill, browsertabs, bulkmail, coding, desktop,
    devwork,
    drafting,
    documents,
    filesystem,
    gcalendar, gmail,
    handoff,
    interaction,
    launcher, memory, messaging, profile, repairs, research, selfcontrol, selfeval, sysinfo,
    system, technician, vault, voice, web,
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
    technician, repairs, vault, interaction, browsertabs, attachments,
    bulkmail, autofill, drafting, devwork, selfcontrol, agents,
    webagent, feedback, webforms, tasks, profile,
):
    _collisions = set(REGISTRY) & set(_module.REGISTRY)
    if _collisions:
        raise RuntimeError(f"Tool name collision across modules: {_collisions}")
    REGISTRY.update(_module.REGISTRY)


# WHAT RAN, AND WHEN.
#
# One ring buffer at the single choke point every tool call passes through -
# the brain's wrapper and the router's local dispatch both land here. The
# alternative was threading a turn id through the agent SDK, the tool
# wrappers and the router, to answer one question: "did this turn actually
# DO anything, or was it a lookup?"
#
# That question is what stops Jalen asking "how do you rate my work out of
# ten" after somebody asks it the time. See jarvis/tools/feedback.py.
#
# Bounded, because this runs forever. 400 entries is a few hours of heavy
# use and costs a few kilobytes.
_RECENT: "deque[tuple[float, str, dict]]" = deque(maxlen=400)


def tools_since(when: float) -> list[str]:
    """Names of the tools that ran after `when` (time.time()), in order."""
    return [name for at, name, _args in list(_RECENT) if at >= when]


def calls_since(when: float) -> list[tuple[str, dict]]:
    """(tool, args) for everything that ran after `when`, in order.

    The args are needed by jarvis.habits, which can only learn a decision it
    can replay exactly — a tool name on its own is half a decision.
    """
    return [(name, args) for at, name, args in list(_RECENT) if at >= when]


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    # Recorded BEFORE the call, not after: a tool that raises still did
    # something, and a turn whose only action failed is exactly the turn
    # worth asking him about.
    _RECENT.append((time.time(), tool, dict(args or {})))
    return fn(**(args or {}))
