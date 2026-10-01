r"""
Asking how it went, and telling him about it.

    "after the work has been done, it should ask the user, Hey boss, 'How do
     you rate my work out of 10', once the user gives feedback about the
     process, that feedback should come to my email
     jaloliddin2009applicant@gmail.com in simple words, summary format"

THE HARD PART IS NOT THE ASKING
-------------------------------
It is not asking. An assistant that requests a score after "what's the time"
is not collecting feedback, it is collecting resentment — and the ratings it
gets back are worthless, because nobody thinks about a number they are asked
for forty times a day.

So `worth_asking_about` is the whole design. It says no by default and yes
only when three things line up:

    the turn actually DID something         a tool with an effect ran
    it was not trivial                      it took real time, or real steps
    he has not been asked recently          a cooling-off period

Everything else is plumbing.

WHY THE RATING GOES TO EMAIL AND NOT JUST A FILE
------------------------------------------------
He asked for email, and he was right to. A rating in data/ is a rating
nobody reads. A short mail arriving in an inbox he checks is one he can act
on weeks later, when the pattern in them is the useful part rather than any
single score.

It is written in "simple words, summary format" as asked: what he wanted,
what Jalen did, what he said about it, and the score. Not a log dump.

WHAT IT NEVER SENDS
-------------------
The utterance is summarised, never quoted wholesale, and it passes through
the same redaction the audit log uses. A feedback mail that helpfully
included the sentence "unlock my vault, the passphrase is..." would be a
credential leak with a friendly subject line.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RATINGS_PATH = ROOT / "data" / "ratings.jsonl"

# Where the summaries go. His address, given explicitly.
FEEDBACK_TO = "jaloliddin2009applicant@gmail.com"

# Don't ask twice in the same stretch of work. Twenty minutes is long enough
# that being asked feels occasional rather than habitual.
COOLDOWN_S = 20 * 60

# Below this, the turn was a question and not a job. Measured against the
# real audit log: ordinary lookups finish in 3-9s, while the things he calls
# "work" - delegations, coding jobs, inbox sweeps - run far longer.
MIN_WORK_S = 25.0

# Tools that mean something actually happened in the world. A turn that only
# READ something is not work he has an opinion about.
_REAL_WORK = frozenset({
    "web_delegate", "web_follow_up", "delegate_task", "follow_up_task",
    "start_coding_job", "review_coding_job", "hand_off_to_cowork",
    "hand_off_to_claude_code", "send_email", "draft_email", "send_posts",
    "send_telegram_message", "send_telegram_file", "send_sticker",
    "draft_telegram_post",
    "scan_inbox", "research_topic", "clear_temp_files", "edit_file",
    "write_file", "rename_file", "delete_file", "move_file",
})


def _now() -> float:
    return time.time()


def _last_asked_at() -> float:
    """When he was last asked. 0 if never."""
    try:
        lines = RATINGS_PATH.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return 0.0
    for line in reversed(lines):
        try:
            return float(json.loads(line).get("asked_at", 0))
        except ValueError:
            continue
    return 0.0


def worth_asking_about(
    tools_used: "list[str] | tuple[str, ...]" = (),
    seconds: float = 0.0,
    *,
    now: float | None = None,
    last_asked: float | None = None,
) -> bool:
    """
    Should Jalen ask for a score after this turn?

    Says NO by default, and the default is doing most of the work here. The
    failure mode of getting this wrong is not a missing data point, it is an
    assistant that interrupts you for a number after every sentence — and
    the scores it collects that way mean nothing anyway, because nobody
    considers a question they are asked constantly.

    `now` and `last_asked` are injectable so the cooling-off period is
    testable without sleeping through it.
    """
    used = {str(t) for t in (tools_used or ())}
    if not used & _REAL_WORK:
        return False
    if float(seconds) < MIN_WORK_S:
        return False
    now = _now() if now is None else now
    last = _last_asked_at() if last_asked is None else last_asked
    return (now - last) >= COOLDOWN_S


def the_question() -> str:
    """His words, so it sounds like the thing he asked for."""
    return "Hey boss — how do you rate my work out of ten?"


def parse_rating(said: str) -> "int | None":
    """
    Pull a score out of whatever he said. None if there isn't one.

    THE BUG THIS EXISTS TO NOT REPEAT. He said:

        "Jalen I rate your work out of 10 is 5 because you didn't properly
         connected webdelegate"

    and the email that reached his inbox said 10/10. The old version fell
    through to "first number in the sentence", and the first number was the
    SCALE, not the score. A wrong rating is worse than no rating: it reads
    as data and it is fiction, and it was fiction that flattered itself.

    So the scale is removed BEFORE any number is looked for. Three passes:

        1. "8 out of 10" / "8/10"  - a number bound to the scale IS the score
        2. strip every remaining mention of the scale
        3. only then, look for a number or a number-word in what is left
    """
    import re

    text = " " + (said or "").lower().strip() + " "

    # 1. The score stated WITH the scale, in that order.
    match = re.search(r"\b(10|[0-9])\s*(?:/|out of|outta)\s*(?:10|ten)\b", text)
    if match:
        return int(match.group(1))

    # 2. Otherwise the scale is noise, and it must not be mistaken for the
    #    score. "out of 10 is 5" has to leave "is 5".
    text = re.sub(r"\b(?:out of|outta|over|on a scale of)\s*(?:10|ten)\b", " ", text)
    text = re.sub(r"/\s*(?:10|ten)\b", " ", text)
    text = re.sub(r"\bout of\b", " ", text)

    # 3. A digit first - people say "5" more often than "five" when scoring.
    match = re.search(r"\b(10|[0-9])\b", text)
    if match:
        return int(match.group(1))

    words = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    for word, value in words.items():
        if re.search(rf"\b{word}\b", text):
            return value
    return None


# Things that must never travel in an email. The audit log's own shapes are
# reused for the phrases, plus token shapes it does not need to care about —
# a local log is on his machine either way, but this text LEAVES it.
_CREDENTIAL_SHAPES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),          # OpenAI-style
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"),         # Google
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),     # GitHub
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),   # Slack
    re.compile(r"\b[0-9]{6,10}:[A-Za-z0-9_-]{30,}"), # Telegram bot
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),     # long base64 blobs
)


def _redact(text: str) -> str:
    """
    Scrub anything credential-shaped before it leaves the machine.

    Two layers, because they answer different questions. The audit log's
    shapes detect a SENTENCE about a secret ("my password is...") and the
    right response to those is to drop the whole field — a partial redaction
    of "my password is hunter2" that leaves "my password is" still tells a
    reader what the next word was. The token shapes below are substitutions,
    because a key embedded in an otherwise useful sentence should not cost
    the sentence.

    Stricter than the audit log on purpose: that file sits on his machine
    whatever happens to it, and this text is put into an email and sent.
    """
    body = text or ""
    try:
        from ..audit import AuditLog
        for pattern in AuditLog._SECRET_SHAPES:
            if pattern.search(body):
                return "[mentioned a credential - not included]"
    except (ImportError, AttributeError):
        pass
    for pattern in _CREDENTIAL_SHAPES:
        body = pattern.sub("***redacted***", body)
    return body


def _summary(about: str, did: str, said: str, score: "int | None") -> tuple[str, str]:
    """(subject, body) — simple words, as asked. Not a log dump."""
    headline = f"Jalen feedback: {score}/10" if score is not None else "Jalen feedback"
    stamp = time.strftime("%d %B %Y, %H:%M")
    body = (
        f"{stamp}\n\n"
        f"WHAT HE ASKED FOR\n{_redact(about).strip() or '(not recorded)'}\n\n"
        f"WHAT JALEN DID\n{_redact(did).strip() or '(not recorded)'}\n\n"
        f"WHAT HE SAID ABOUT IT\n{_redact(said).strip() or '(no comment)'}\n\n"
        f"SCORE\n{score if score is not None else 'not given'} out of 10\n"
    )
    return headline, body


def record_rating(score: Any = None, comment: str = "",
                  about: str = "", did: str = "") -> str:
    """
    Store the rating and mail the summary. Returns what to say back.

    Written to disk BEFORE the mail is attempted, and deliberately: the
    record is the thing that must not be lost, and Gmail can be down,
    unauthorised, or offline. A rating that only exists if the network was up
    is a rating that quietly stops existing.
    """
    value = score if isinstance(score, int) else parse_rating(str(score or comment))
    if value is not None:
        value = max(0, min(10, int(value)))

    RATINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": _now(), "asked_at": _now(), "score": value,
        "comment": _redact(comment)[:600], "about": _redact(about)[:400],
        "did": _redact(did)[:600],
    }
    try:
        with open(RATINGS_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass

    subject, body = _summary(about, did, comment, value)
    sent, why = _mail(subject, body)

    thanks = "Thanks — that's noted."
    if value is not None and value <= 5:
        thanks = ("Thanks for saying so. Low scores are the useful ones — "
                  "I've written down what you said.")
    elif value is not None and value >= 9:
        thanks = "Thanks, boss. Noted."

    return thanks if sent else f"{thanks} (I couldn't email the summary: {why})"


def _mail(subject: str, body: str) -> tuple[bool, str]:
    """Send it. (ok, why not)."""
    try:
        from . import gmail
    except ImportError as exc:
        return False, str(exc)
    try:
        result = gmail.send_email(FEEDBACK_TO, subject, body)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    low = str(result).lower()
    if "couldn't" in low or "not connected" in low or "error" in low:
        return False, str(result)[:120]
    return True, ""


def rating_history(limit: int = 10) -> str:
    """What he has thought of it lately."""
    try:
        lines = RATINGS_PATH.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return "You haven't rated anything yet."
    rows = []
    for line in lines[-max(1, int(limit)):]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if not rows:
        return "You haven't rated anything yet."

    scored = [r["score"] for r in rows if isinstance(r.get("score"), int)]
    out = [f"Your last {len(rows)} ratings:"]
    for row in rows:
        when = time.strftime("%d %b %H:%M", time.localtime(row.get("at", 0)))
        score = row.get("score")
        out.append(f"  {when}  {str(score) + '/10' if score is not None else '--':6s}"
                   f"  {(row.get('about') or '')[:60]}")
    if scored:
        out.append(f"\nAverage: {sum(scored) / len(scored):.1f} out of 10 "
                   f"across {len(scored)} rated jobs.")
    return "\n".join(out)


def what_i_have_learned(limit: int = 12) -> str:
    """The habits Jalen has picked up. Lives in jarvis/habits.py."""
    from .. import habits

    return habits.what_i_have_learned(limit)


def forget_habit(text: str = "") -> str:
    """Drop one habit, or all of them."""
    from .. import habits

    return habits.forget(text)


REGISTRY: dict[str, Any] = {
    "record_rating": record_rating,
    "rating_history": rating_history,
    "what_i_have_learned": what_i_have_learned,
    "forget_habit": forget_habit,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
