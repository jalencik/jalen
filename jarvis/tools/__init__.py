"""
Unified tool dispatch. system.py (Phase 0/1), desktop.py and filesystem.py
(Phase C) each own a REGISTRY of their own tools; this module merges them
into one name -> function map so callers (the router's handle_local(), and
the Brain's SDK tool wrappers) have a single place to call any tool by name
without needing to know which module it lives in.

Tool names must be unique across all three registries — a collision would
silently shadow one implementation with another, so it's asserted at import
time rather than left to be discovered at call time.
"""
from __future__ import annotations

from typing import Any

from . import desktop, filesystem, system

REGISTRY: dict[str, Any] = {}
for _module in (system, desktop, filesystem):
    _collisions = set(REGISTRY) & set(_module.REGISTRY)
    if _collisions:
        raise RuntimeError(f"Tool name collision across modules: {_collisions}")
    REGISTRY.update(_module.REGISTRY)


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
