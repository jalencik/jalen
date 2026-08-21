"""
What Jalen cannot do yet, written down by Jalen, in a file you can open.

He asked for this directly: after a task, say what you couldn't do, and keep
it somewhere he can read, so that using it and improving it become the same
activity.

WHY A TOOL AND NOT A HEURISTIC. The obvious implementation scans replies for
"I can't" and logs those. It does not work. "I can't tell from here whether
that meeting moved" is an honest answer to a question, not a missing
capability; "I can't send Telegram messages because there's no tool for it"
is a gap worth building. No amount of pattern-matching separates them,
because the difference is in what the model knows about WHY, and only the
model has that. So it is a tool the brain calls deliberately, and the system
prompt tells it when.

WHY IT DEDUPLICATES. A log that appends unconditionally becomes forty copies
of the same sentence within a week, and forty copies of one problem read as
forty problems. Entries are keyed on the missing CAPABILITY, so asking the
same impossible thing five times produces one entry with a count of five —
which is also the only honest way to rank what to build next.

WHY IT NAMES THE CAPABILITY, NOT THE EXCUSE. "I was unable to complete that
request" is worthless six weeks later. "No tool can set a Windows service to
delayed start" is a work item. The prompt insists on the second shape.

The file is Markdown so Notepad shows something readable, and it is never
truncated or rotated — the whole point is the accumulated record.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
WEAKNESS_PATH = ROOT / "data" / "weaknesses.md"

HEADER = """# What Jalen cannot do yet

Written by Jalen, as he hits each limit. Newest at the bottom.

Each entry names the MISSING CAPABILITY, not the excuse — "no tool can read
a PDF's page count" is a work item, "I was unable to help" is not. The count
is how many times it has come up, which is the honest way to decide what to
build next.

Say "what can't you do yet" to hear the top of this list out loud.
"""

# One writer at a time. Turns run on their own threads and two of them can
# be in flight at once (MAX_IN_FLIGHT_TURNS), so an unguarded
# read-modify-write here would interleave and corrupt the file.
_LOCK = threading.Lock()

_ENTRY_RE = re.compile(
    r"^## (?P<missing>.+?)\n"
    r"(?P<body>(?:(?!^## ).*\n?)*)",
    re.M,
)
_COUNT_RE = re.compile(r"^- Seen: (\d+) times?", re.M)


def _key(text: str) -> str:
    """Normalise a capability description so near-identical ones collapse."""
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def log_weakness(missing: str, asked: str = "", happened: str = "") -> str:
    """
    Record something Jalen could not do. Call this ONCE, after answering.

    missing:  the capability that does not exist, in one concrete sentence.
              "No tool can change a Windows service's startup type."
    asked:    what he actually said, so the entry has context later.
    happened: what you did instead — usually "said so and stopped".
    """
    missing = (missing or "").strip()
    if not missing:
        return "Nothing recorded — a weakness entry needs a concrete missing capability."

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _LOCK:
        WEAKNESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = WEAKNESS_PATH.read_text(encoding="utf-8") if WEAKNESS_PATH.exists() else ""
        if not existing.strip():
            existing = HEADER

        wanted = _key(missing)
        for match in _ENTRY_RE.finditer(existing):
            if _key(match.group("missing")) != wanted:
                continue
            # Seen before: bump the count and the date, leave the rest.
            block = match.group(0)
            count_match = _COUNT_RE.search(block)
            count = int(count_match.group(1)) + 1 if count_match else 2
            updated = _COUNT_RE.sub(f"- Seen: {count} times, last {stamp}", block) \
                if count_match else block.rstrip() + f"\n- Seen: {count} times, last {stamp}\n"
            WEAKNESS_PATH.write_text(
                existing.replace(block, updated), encoding="utf-8"
            )
            return f"Noted again ({count} times now): {missing}"

        entry = [f"\n## {missing}\n"]
        if asked:
            entry.append(f"- Asked: {asked.strip()}\n")
        if happened:
            entry.append(f"- Happened: {happened.strip()}\n")
        entry.append(f"- Seen: 1 time, last {stamp}\n")
        WEAKNESS_PATH.write_text(existing.rstrip() + "\n" + "".join(entry), encoding="utf-8")
    return f"Noted: {missing}"


def _entries() -> list[tuple[int, str, str]]:
    """(count, missing, last-seen) for every entry, most frequent first."""
    if not WEAKNESS_PATH.exists():
        return []
    text = WEAKNESS_PATH.read_text(encoding="utf-8")
    found = []
    for match in _ENTRY_RE.finditer(text):
        block = match.group(0)
        count_match = _COUNT_RE.search(block)
        count = int(count_match.group(1)) if count_match else 1
        last = ""
        if count_match:
            tail = block[count_match.end():].split("\n", 1)[0]
            last = tail.replace(", last ", "").strip()
        found.append((count, match.group("missing").strip(), last))
    return sorted(found, key=lambda row: -row[0])


def review_weaknesses(limit: int = 5) -> str:
    """
    What Jalen still can't do, most frequently hit first. Read this out when
    he asks "what can't you do yet".
    """
    found = _entries()
    if not found:
        return (
            "Nothing recorded yet — either I haven't hit a wall, or I haven't "
            "been honest about hitting one."
        )
    lines = [f"{len(found)} things I can't do yet. The ones that come up most:"]
    for count, missing, _last in found[:limit]:
        times = "once" if count == 1 else f"{count} times"
        lines.append(f"  - {missing} ({times})")
    if len(found) > limit:
        lines.append(f"  ...and {len(found) - limit} more, in data/weaknesses.md.")
    return "\n".join(lines)


def weakness_file_path() -> str:
    """Where the file is, for opening it in Notepad."""
    return str(WEAKNESS_PATH)


REGISTRY: dict[str, Any] = {
    "log_weakness": log_weakness,
    "review_weaknesses": review_weaknesses,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
