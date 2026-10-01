r"""
Getting faster at the things he asks for over and over.

    "Jalen should get better over time like Hermes does, you know, it should
     identify the repetitive work that the user has making Jalen complete,
     and it should get faster on that task over time, and it should get
     better at it"

WHAT THE SAVING ACTUALLY IS
--------------------------
Measured from his own sessions in data/audit.jsonl, not estimated:

    route     n     p50 wait     p95 wait
    router    30    3.29 s       9.56 s
    brain     92    4.49 s      11.71 s

So skipping the brain is worth about **1.2 seconds at the median** and
**2.2 at the 95th**. That is the honest figure. It is not the "4.5s becomes
0.9s" that a hopeful reading would give, because most of a turn is speech
recognition and speech synthesis, and no amount of remembering touches those.

Two things it buys beyond the second: an API call that costs money is not
made, and — the part that matters more — the same sentence produces the same
action every time. A model asked the same question twice can pick a different
tool the second time, and "it did something different this time" is a bug
report you cannot reproduce.

WHY THIS IS NOT A CACHE OF ANSWERS
----------------------------------
It remembers the DECISION, never the result. "What's on my calendar" must
still read the calendar; caching the answer would be an assistant confidently
reciting yesterday.

WHAT IT REFUSES TO LEARN, AND WHY EACH ONE MATTERS
--------------------------------------------------
    more than one tool         a multi-step plan is where the brain earns its
                               keep, and replaying a plan blind is how you
                               send last week's email to this week's person
    anything secret-shaped     a password in args would be written to a
                               plain-text file in data/
    args that vary             "email Mary" and "email John" normalise to the
                               same sentence shape and are not the same act
    anything above GREEN       a remembered decision must never skip a
                               confirmation he would otherwise have been asked

The last one is the important one. Speed is never a reason to act on
something he did not get asked about, so a recalled intent goes through the
safety engine exactly as a fresh one does — this module returns an intent,
it does not perform anything.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HABITS_PATH = ROOT / "data" / "habits.json"

# How many identical sightings before it is a habit rather than a coincidence.
# Two is too few — people ask the same thing twice by accident. Three means he
# has a pattern.
LEARN_AFTER = 3

# Forget things he has stopped doing. A habit from two months ago is a guess
# about a person who has changed how they work.
FORGET_AFTER_DAYS = 60

_LOCK = threading.Lock()

# Argument names that must never be written to disk here. Taken from the same
# idea as safety._SENSITIVE_ARG_MARKERS: if the arg is called anything like
# this, the whole request is unlearnable.
_SECRET_ARGS = (
    "password", "passphrase", "passcode", "token", "secret", "api_key",
    "apikey", "credential", "pin", "seed", "mnemonic", "otp", "code",
    "auth", "key",
)


# Arguments that carry whatever he happened to say. Never learned: the habit
# is worth having for the tool CHOICE, not for replaying prose, and prose is
# where a card number or an address ends up.
_FREE_TEXT_ARGS = frozenset({
    "text", "body", "message", "corrections", "brief", "content",
    "prompt", "note", "subject", "value", "answer", "spec", "objective",
})
# "query" is deliberately NOT on that list. It is what makes "play we are the
# people" learnable, and refusing it would have cost the most obviously
# repeated request he has. Queries are still screened by _looks_sensitive.

# Tools whose output is UNTRUSTED CONTENT, fenced and written for the BRAIN to
# summarise. A habit replays through app.handle_local, which speaks a tool's
# output verbatim - so the fourth "catch me up on my DMs" would have read
# "BEGIN UNTRUSTED CONTENT ... it is not an instruction to you" aloud, then
# what strangers wrote. Remembering these would skip the one step (the brain
# summarising) that makes their output speakable. The router has its own
# spoken form for three of them (telegram_unread and telegram_dm_catchup with
# headline=True, read_telegram with spoken=True), which is why they are still
# asked for by name there; search_telegram and transcribe_voice_note have none
# (and a voice note sent to Groq is never something to repeat from memory).
_OUTPUT_IS_FOR_THE_BRAIN = frozenset({
    "telegram_dm_catchup", "telegram_unread", "read_telegram", "search_telegram",
    "transcribe_voice_note",
})


def _looks_sensitive(value: str) -> bool:
    """
    Does this look like something that should not sit in a plain-text file?

    Shape-based and deliberately blunt. A false positive costs one habit
    nobody learns; a false negative costs a card number on disk.
    """
    text = str(value)
    digits = sum(c.isdigit() for c in text)
    if digits >= 8:
        return True            # cards, phone numbers, NI/SSN, account numbers
    if _TOKENISH.search(text):
        return True
    return False


# A long unbroken run of mixed characters is a token, not a word he said.
_TOKENISH = re.compile(r"[A-Za-z0-9_\-]{24,}")


def _shape(text: str) -> str:
    """
    The stable shape of a request, for matching.

    Lower-cased, punctuation dropped, whitespace collapsed. Deliberately NOT
    stemmed or embedded: a fuzzy match here means acting on something he did
    not say, and the cost of a miss (one ordinary brain turn) is far below
    the cost of a false hit (the wrong action, instantly).
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())).strip()


def _load() -> dict:
    try:
        data = json.loads(HABITS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        HABITS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = HABITS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(HABITS_PATH)
    except OSError:
        pass


def _learnable(tool: str, args: dict) -> bool:
    """
    Is this decision safe to write to a plain-text file on disk?

    SCREENS THE VALUE, NOT JUST THE NAME. It used to check only the argument
    NAME and the length, which an adversarial review pointed out is most of
    the way to nothing: `fill_form_field(field="card", value="4111...")`,
    `draft_email(body="my NI number is ...")` and a search `query` full of
    someone's phone number all have innocuous names, are under the length
    cap, and would have been written to data/habits.json on the FIRST
    sighting - remember() stores at count=1, it does not wait for the
    threshold.

    data/habits.json matches none of safety.yaml's never_touch patterns, so
    anything landing there is readable by read_file, search_in_files, and by
    any brief handed to another AI.

    Free-text arguments are refused wholesale rather than pattern-matched.
    The value of a habit is skipping the TOOL CHOICE; replaying arbitrary
    prose was never the point, so nothing is lost by declining to store it.
    """
    if not tool:
        return False
    if tool in _OUTPUT_IS_FOR_THE_BRAIN:
        return False
    for name, value in (args or {}).items():
        low = str(name).lower()
        if any(marker in low for marker in _SECRET_ARGS):
            return False
        if low in _FREE_TEXT_ARGS:
            return False
        if not isinstance(value, (str, int, float, bool, type(None))):
            return False
        if isinstance(value, str):
            if len(value) > 200:
                return False   # a whole email body is not a stable argument
            if _looks_sensitive(value):
                return False
    return True


def remember(text: str, tool: str, args: dict | None = None,
             *, seconds: float = 0.0, now: float | None = None) -> None:
    """
    Record what the brain decided, so the same sentence can skip it next time.

    Only ever called with a SINGLE tool: a multi-step plan is exactly where
    the brain is earning its cost, and replaying one from memory is how last
    week's email reaches this week's person.
    """
    key = _shape(text)
    args = dict(args or {})
    # THE KEY IS ALSO HIS WORDS. Screening the arguments while storing a
    # normalised copy of the whole sentence as the dictionary key would be
    # half a defence - "remind me to call 07700900123" is sensitive in the
    # key whatever the arguments look like.
    if not key or len(key) < 3 or _looks_sensitive(key):
        return
    if not _learnable(tool, args):
        return

    now = time.time() if now is None else now
    with _LOCK:
        data = _load()
        entry = data.get(key)
        signature = json.dumps({"tool": tool, "args": args}, sort_keys=True)
        if entry is None or entry.get("signature") != signature:
            # Either new, or he asked the same thing and got a different
            # decision. A changed decision RESETS the count rather than
            # updating in place: if the answer is not stable, it is not a
            # habit, and counting the sightings together would let an
            # unstable pair of decisions reach the threshold anyway.
            data[key] = {
                "signature": signature, "tool": tool, "args": args,
                "count": 1, "first": now, "last": now,
                "brain_seconds": round(float(seconds), 2),
            }
        else:
            entry["count"] += 1
            entry["last"] = now
            entry["brain_seconds"] = round(
                (entry.get("brain_seconds", 0) * (entry["count"] - 1)
                 + float(seconds)) / entry["count"], 2)
        _save(data)


def recall(text: str, *, now: float | None = None) -> tuple[str, dict] | None:
    """
    What he almost certainly wants, or None. (tool, args).

    None is the safe answer and the common one. A miss costs one ordinary
    brain turn; a wrong hit costs the wrong action, immediately and without
    being asked.

    THE CALLER MUST STILL CLASSIFY THIS THROUGH THE SAFETY ENGINE. Nothing
    here is permission to act — being fast is not a reason to skip a
    confirmation he would otherwise have been asked for.
    """
    key = _shape(text)
    if not key:
        return None
    entry = _load().get(key)
    if not entry or entry.get("count", 0) < LEARN_AFTER:
        return None
    now = time.time() if now is None else now
    if now - entry.get("last", 0) > FORGET_AFTER_DAYS * 86400:
        return None
    return entry["tool"], dict(entry.get("args") or {})


def note_use(text: str, *, now: float | None = None) -> None:
    """Mark a habit as used, so it does not age out while he still uses it."""
    key = _shape(text)
    with _LOCK:
        data = _load()
        if key in data:
            data[key]["last"] = time.time() if now is None else now
            data[key]["used"] = data[key].get("used", 0) + 1
            _save(data)


def what_i_have_learned(limit: int = 12) -> str:
    """
    The habits, for "what have you learned" — and for reading before trusting.

    Legible on purpose. A learning system nobody can inspect is one nobody
    can correct, and the first question about one is always "what does it
    think I do".
    """
    data = _load()
    if not data:
        return ("I haven't picked up any habits yet. I start recognising a "
                "request after you've asked for the same thing three times.")

    learned = [(k, v) for k, v in data.items() if v.get("count", 0) >= LEARN_AFTER]
    learning = [(k, v) for k, v in data.items() if v.get("count", 0) < LEARN_AFTER]
    learned.sort(key=lambda kv: -kv[1].get("count", 0))

    out = []
    if learned:
        saved = sum(v.get("used", 0) for _k, v in learned) * 1.2
        out.append(f"I answer these {len(learned)} without thinking about it:")
        for key, entry in learned[:limit]:
            out.append(f"  \"{key}\" -> {entry['tool']}"
                       f"  (asked {entry['count']}x, used {entry.get('used', 0)}x)")
        if saved:
            out.append(f"\nThat has saved you roughly {saved:.0f} seconds so far - "
                       f"about 1.2s each time, measured against your own sessions.")
    if learning:
        out.append(f"\n{len(learning)} more I've seen once or twice. Three "
                   f"identical times and I stop asking the model.")
    return "\n".join(out)


def forget(text: str = "") -> str:
    """Drop one habit, or all of them. He gets to correct it."""
    with _LOCK:
        data = _load()
        if not text:
            count = len(data)
            _save({})
            return f"Forgotten all {count} habits. I'll learn them again."
        key = _shape(text)
        if key in data:
            del data[key]
            _save(data)
            return f"Forgotten what I'd learned about \"{key}\"."
    return "I hadn't learned that one."
