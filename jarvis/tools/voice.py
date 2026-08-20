"""
His writing voice, on demand.

He has a my-voice skill — 35 KB of analysis of how he actually writes,
built from his real essays. Asked to "draft a reply in my voice", Jarvis
was writing in ITS voice and calling it his, which is worse than declining:
an email that sounds like a language model went out under his name.

WHY THIS IS A TOOL AND NOT PART OF THE SYSTEM PROMPT
----------------------------------------------------
The obvious move is to paste the skill into persona.style. That would cost
roughly nine thousand tokens on EVERY turn — including "what time is it",
"open chrome", and every one of the hundreds of daily turns that involve no
writing at all. He is on a Claude subscription with a real ceiling and has
already asked, more than once, how quickly he burns through it. The intent
router exists for exactly this reason.

As a tool it is paid for only on the turns that actually write something.

The file is looked up in the three places his skills live, because the same
skill is installed in all of them and there is no guarantee which is
canonical on a given machine.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Ordered by how authoritative each location is. ~/.claude/skills is what
# Claude Code itself reads, so it wins.
_SEARCH_PATHS = [
    Path.home() / ".claude" / "skills" / "my-voice" / "SKILL.md",
    Path.home() / ".agents" / "skills" / "my-voice" / "SKILL.md",
    Path.home() / "Desktop" / "Claude skills" / "my-voice" / "SKILL.md",
]

_cache: dict[str, str] = {}


def _find() -> Path | None:
    override = os.getenv("JARVIS_VOICE_SKILL")
    if override and Path(override).is_file():
        return Path(override)
    for path in _SEARCH_PATHS:
        if path.is_file():
            return path
    return None


def voice_guide() -> str:
    """
    Return his writing-voice guide, for anything written AS him.

    Cached after the first read: the file does not change mid-session, and
    re-reading 35 KB off disk for every draft in a conversation is waste.
    """
    path = _find()
    if path is None:
        return (
            "I couldn't find your my-voice skill. I looked in "
            + ", ".join(str(p.parent) for p in _SEARCH_PATHS)
            + ". Without it I'd be guessing at your voice, so tell me if "
            "you'd rather I just write it plainly."
        )
    key = str(path)
    if key not in _cache:
        try:
            _cache[key] = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"I found your voice skill but couldn't read it ({exc})."
    return (
        "This is how HE writes. Match it — his rhythm, his hedges, his "
        "plainness, his willingness to repeat a word instead of elegantly "
        "varying it. Do not imitate his most dramatic lines and do not wear "
        "his phrases as a costume; when unsure, write plainer. Write the "
        "text and nothing else: no preamble, no explanation of your "
        "choices.\n\n"
        + _cache[key]
    )


REGISTRY: dict[str, Any] = {
    "voice_guide": voice_guide,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
