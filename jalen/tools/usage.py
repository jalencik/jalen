"""
What has been used against each limit - the read side of jalen/limits.py.

Its own module, and not part of selfcontrol.py, on purpose: selfcontrol is
"Jalen working on Jalen" and tests/test_selfcontrol.py pins its exports as
exactly the things that read diagnostics or open an editor. A report on
limits is neither, and loosening that boundary to fit it in would have been
the wrong way round.

READ ONLY, and with no argument that could be turned into anything else. The
governor's setters (lock, unlock, attach, record, new_instruction) are never
imported here: this module reaches the governor only through
limits.current(), and tests/test_limits_are_enforced_on_both_paths.py fails if
any module in jalen/tools or jalen/brain/tools.py touches another name. That
is what "nothing the model can call raises a limit or ends a lockdown" means
in code.
"""
from __future__ import annotations

from typing import Any


def usage_report() -> str:
    """
    How much of each usage limit has been used, and whether he has put me in
    lockdown - GREEN, read-only, spoken length.
    """
    from .. import limits

    governor = limits.current()
    if governor is None:
        return "No usage limits are loaded in this session."
    return governor.report()


REGISTRY: dict[str, Any] = {
    "usage_report": usage_report,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
