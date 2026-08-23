"""
His writing voice, on demand.

He has a my-voice skill — 35 KB of analysis of how he actually writes,
built from his real essays. Asked to "draft a reply in my voice", Jalen
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
    """
    Locate the writing-voice guide, in order of authority.

    Three sources, and the config one is what makes this usable by anyone
    other than its author. The default search paths are where HIS skill is
    installed; a second user has no my-voice skill and no reason to keep
    their writing sample in a Claude skills folder. `personal.voice_guide`
    in config/user.yaml points at any file they like — an essay, a folder of
    old emails pasted together, anything that is actually their writing.
    """
    override = os.getenv("JARVIS_VOICE_SKILL")
    if override and Path(override).is_file():
        return Path(override)

    try:
        from ..config import CONFIG

        configured = CONFIG.get_path("personal.voice_guide", "") or ""
    except Exception:
        configured = ""
    if configured:
        # Expanded so it can be written as ~/writing.md or {home}/writing.md
        # without a user having to know which one this project uses.
        path = Path(str(configured)).expanduser()
        if path.is_file():
            return path

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
        # Names the fix, not just the failure. The original message listed
        # three paths and stopped, which is only actionable if you already
        # know a my-voice skill is a thing that exists — true for exactly one
        # person.
        return (
            "I don't have a sample of your writing, so I'd be guessing at "
            "your voice. Point me at one: put the path in config/user.yaml "
            "under personal.voice_guide — any file that is genuinely your "
            "writing will do, an essay or a few old emails. Until then, say "
            "the word and I'll write it plainly instead of pretending."
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


_POST_FORMAT = Path(__file__).resolve().parent.parent.parent / "docs" / "community_post_format.md"

# The handle written into the worked examples in that file. Replaced with
# personal.channel_handle when a second user sets one — see below.
DEFAULT_HANDLE = "@Iht_student"


def community_post_guide() -> str:
    """
    The house style for his AI Engineering & Machine Learning channel.

    Same reasoning as voice_guide: a tool, not part of the system prompt.
    It is only relevant on the handful of turns that write a post, and
    carrying it on every "what time is it" would cost him allowance for
    nothing.

    Separate FROM voice_guide, though, because they answer different
    questions. voice_guide is how he sounds; this is how a channel post is
    laid out — the bullet character, where bold goes, the expandable Q&A
    block, and the two sign-off lines that never change. A post can be
    perfectly in his voice and still look wrong in the channel.
    """
    try:
        body = _POST_FORMAT.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (
            f"I couldn't read the post format guide at {_POST_FORMAT} ({exc}). "
            "Tell me the layout you want and I'll follow it."
        )
    # The sign-off handle belongs to whoever owns the channel, and the guide
    # has it written into four worked examples. Substituted here rather than
    # in the file so the examples stay readable as examples — a guide full of
    # {placeholders} teaches the model to emit placeholders.
    try:
        from ..config import CONFIG

        handle = CONFIG.get_path("personal.channel_handle", "") or ""
    except Exception:
        handle = ""
    if handle and handle != DEFAULT_HANDLE:
        body = body.replace(DEFAULT_HANDLE, handle)

    return (
        "This is the format for his channel. Follow the STRUCTURE exactly and "
        "vary the words. Output Telegram HTML — only the tags listed below are "
        "allowed, and anything else makes the post fail to save. Write the post "
        "and nothing else: no preamble, no explanation.\n\n" + body
    )


REGISTRY: dict[str, Any] = {
    "voice_guide": voice_guide,
    "community_post_guide": community_post_guide,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
