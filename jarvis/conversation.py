r"""
What we are currently talking about, and what is currently being done.

THREE COMPLAINTS, ONE MISSING PIECE
-----------------------------------
    9.  "I said it to send it to my saved messages" - and it made a draft
        somewhere else instead.
    11. "I said yes go on, but it has stopped man, it should have a
        consistent memory."
    6C. a task must not silently disappear.

These look like three problems. They are one: nothing in Jalen remembered
what the current subject WAS. Every turn arrived as if it were the first,
so "it" referred to nothing, "yes" agreed to nothing, and a task that had
started existed only inside the thread running it.

WHY NOT OBSIDIAN OR A GRAPH DATABASE
------------------------------------
He offered both. `jarvis/tools/memory.py` already has real semantic
long-term memory - fastembed + sqlite-vec, local, with a secret filter that
refuses credential-shaped text before it reaches the database. That layer
works and is not the gap.

The gap is the SHORT term: the last thing Jalen offered to do, the thing
"it" refers to, and which task "continue" means. That is a handful of
fields with a lifetime measured in minutes. Storing it in a graph database
would add a service to run, a schema to migrate and a failure mode where
the assistant cannot answer "yes" because a database is down.

So: long-term memory stays where it is, and this holds the present tense.

WHAT LIVES HERE AND WHAT DOES NOT
---------------------------------
Here:   the last proposal, the current subject, the active tasks
Not:    anything worth keeping past today, which belongs in memory.py
Never:  a passphrase, a password, a code. `remember_subject` refuses
        credential-shaped values for the same reason memory.py does - this
        is written to disk, and disk is readable.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

# ---------------------------------------------------------------------------
# THE STATE MACHINE (his requirement 3)
#
# Named states rather than booleans scattered across the app. "Is it doing
# something?" was previously answerable only by checking four unrelated
# flags, which is why a task could look finished while a thread was still
# working on it.
# ---------------------------------------------------------------------------
IDLE = "IDLE"
THINKING = "THINKING"
EXECUTING = "EXECUTING"
WAITING_FOR_USER = "WAITING_FOR_USER"
WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
WAITING_FOR_EXTERNAL_AI = "WAITING_FOR_EXTERNAL_AI"
WAITING_FOR_BROWSER = "WAITING_FOR_BROWSER"
RUNNING_BACKGROUND_JOB = "RUNNING_BACKGROUND_JOB"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

LIVE_STATES = frozenset({
    THINKING, EXECUTING, WAITING_FOR_USER, WAITING_FOR_CONFIRMATION,
    WAITING_FOR_EXTERNAL_AI, WAITING_FOR_BROWSER, RUNNING_BACKGROUND_JOB,
})
WAITING_STATES = frozenset({
    WAITING_FOR_USER, WAITING_FOR_CONFIRMATION, WAITING_FOR_EXTERNAL_AI,
    WAITING_FOR_BROWSER,
})

# How long the present tense lasts. Long enough to fetch a coffee mid-task,
# short enough that "yes" tomorrow morning does not agree to yesterday's
# email.
CONTEXT_LIFE_S = 900.0

_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# THE LAST PROPOSAL — what "yes" agrees to
# ---------------------------------------------------------------------------
@dataclass
class Proposal:
    """
    Something Jalen offered to do and has not yet done.

    Recorded BEFORE the question is asked, not after the answer, because the
    answer is the moment it is needed and by then the turn that knew has
    usually ended.
    """

    action: str = ""          # send | draft | save | open | play | delete
    destination: str = ""     # "Saved Messages", "antonis@gmu.edu", a path
    summary: str = ""         # what it is, in his words
    tool: str = ""
    args: dict = field(default_factory=dict)
    at: float = field(default_factory=time.monotonic)

    def alive(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return bool(self.action or self.tool) and (now - self.at) <= CONTEXT_LIFE_S

    def describe(self) -> str:
        parts = [p for p in (self.action, self.summary) if p]
        where = f" to {self.destination}" if self.destination else ""
        return (" ".join(parts) + where).strip()


@dataclass
class Subject:
    """What "it", "that" and "there" currently point at."""

    thing: str = ""           # "the draft", "the email from Antonis"
    place: str = ""           # "Saved Messages", "your ML channel"
    person: str = ""          # "Antonis"
    at: float = field(default_factory=time.monotonic)


_proposal = Proposal()
_subject = Subject()
_tasks: "dict[str, Task]" = {}


# ---------------------------------------------------------------------------
@dataclass
class Task:
    """One thing Jalen is doing, that he can ask about later."""

    id: str
    objective: str
    state: str = THINKING
    current_step: str = ""
    waiting_reason: str = ""
    progress: str = ""
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    @property
    def live(self) -> bool:
        return self.state in LIVE_STATES

    def age(self) -> str:
        seconds = max(0, int(time.time() - self.created_at))
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


# ---------------------------------------------------------------------------
# Recording what is going on
# ---------------------------------------------------------------------------
def propose(action: str = "", destination: str = "", summary: str = "",
            tool: str = "", args: dict | None = None) -> None:
    """Jalen is about to ask him to approve something. Remember what."""
    global _proposal
    with _LOCK:
        _proposal = Proposal(action=action, destination=destination,
                             summary=summary, tool=tool, args=dict(args or {}))


def last_proposal() -> Proposal | None:
    with _LOCK:
        return _proposal if _proposal.alive() else None


def clear_proposal() -> None:
    global _proposal
    with _LOCK:
        _proposal = Proposal()


_CREDENTIALISH = re.compile(
    r"\b(pass\w*|secret|token|api[_ ]?key|credential|pin|otp)\b", re.I)


def remember_subject(thing: str = "", place: str = "", person: str = "") -> None:
    """
    Update what "it"/"there"/"they" point at.

    REFUSES credential-shaped values, for the same reason memory.py does:
    this is held in a process that writes an audit log, and "it" should
    never expand into a password.
    """
    global _subject
    for value in (thing, place, person):
        if value and _CREDENTIALISH.search(value):
            return
    with _LOCK:
        _subject = Subject(
            thing=thing or _subject.thing,
            place=place or _subject.place,
            person=person or _subject.person,
        )


def current_subject() -> Subject | None:
    with _LOCK:
        if time.monotonic() - _subject.at > CONTEXT_LIFE_S:
            return None
        return _subject if (_subject.thing or _subject.place or _subject.person) else None


def forget_context() -> None:
    """A new, unrelated request. Used by tests and by an explicit reset."""
    global _proposal, _subject
    with _LOCK:
        _proposal = Proposal()
        _subject = Subject()


# ---------------------------------------------------------------------------
# Pronouns
# ---------------------------------------------------------------------------
_IT = re.compile(r"\b(it|that|this|them|those)\b", re.I)
# "to there" and a bare "there" both become "to <place>". Without absorbing
# the optional "to", the expansion reads "send the draft Saved Messages" —
# not a sentence, and not what he said.
_THERE = re.compile(r"\b(?:to\s+)?(?:there|that place)\b", re.I)

# "go on" / "carry on" / "continue" said on its own is a CONTINUATION, not a
# new request. Kept separate from the yes/no parser because "yes" answers a
# question and "go on" resumes a task, and conflating them is how "yes go
# on" turned into a new empty turn.
_CONTINUE = re.compile(
    r"^\s*(?:yes[,\s]+)?(?:please\s+)?"
    r"(?:go on|carry on|continue|keep going|carry on with (?:it|that)"
    r"|go ahead|proceed|resume|and then\?*|what next\??)\s*$", re.I)


def is_continuation_request(text: str) -> bool:
    """"Yes, go on." - resume, do not start something new."""
    return bool(_CONTINUE.match(text or ""))


def expand_references(text: str) -> str:
    """
    Replace "it" and "there" with what they refer to, when we know.

    Conservative on purpose: it only expands when there IS a remembered
    subject, and it never invents one. A wrong expansion sends the right
    message to the wrong place, which is worse than asking.
    """
    subject = current_subject()
    if not subject or not text:
        return text
    out = text
    if subject.thing:
        out = _IT.sub(subject.thing, out, count=1)
    if subject.place:
        out = _THERE.sub(f"to {subject.place}", out, count=1)
    return out


def what_are_we_talking_about() -> str:
    """One line for the brain's context, or ''."""
    bits = []
    proposal = last_proposal()
    if proposal:
        bits.append(f"You just offered to: {proposal.describe()}")
    subject = current_subject()
    if subject:
        if subject.thing:
            bits.append(f'"it" means: {subject.thing}')
        if subject.place:
            bits.append(f'"there" means: {subject.place}')
        if subject.person:
            bits.append(f"the person in question is {subject.person}")
    live = [t for t in _tasks.values() if t.live]
    if live:
        bits.append("in progress: "
                    + "; ".join(f"{t.objective} ({t.state})" for t in live[:3]))
    return " | ".join(bits)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def start_task(objective: str, state: str = THINKING) -> str:
    task_id = f"t-{uuid.uuid4().hex[:6]}"
    with _LOCK:
        _tasks[task_id] = Task(id=task_id, objective=objective.strip(), state=state)
        # Bounded: this is the present tense, not a history.
        if len(_tasks) > 40:
            for old in sorted(_tasks.values(), key=lambda t: t.last_activity)[:10]:
                if not old.live:
                    _tasks.pop(old.id, None)
    return task_id


def update_task(task_id: str, *, state: str = "", step: str = "",
                waiting: str = "", progress: str = "", result: str = "",
                error: str = "") -> None:
    with _LOCK:
        task = _tasks.get(task_id)
        if task is None:
            return
        if state:
            task.state = state
        if step:
            task.current_step = step
        if waiting:
            task.waiting_reason = waiting
        if progress:
            task.progress = progress
        if result:
            task.result = result
        if error:
            task.error = error
        task.last_activity = time.time()


def get_task(task_id: str) -> Task | None:
    with _LOCK:
        return _tasks.get(task_id)


def live_tasks() -> list[Task]:
    with _LOCK:
        return sorted((t for t in _tasks.values() if t.live),
                      key=lambda t: t.last_activity, reverse=True)


def all_tasks() -> list[Task]:
    with _LOCK:
        return sorted(_tasks.values(), key=lambda t: t.last_activity, reverse=True)


def which_task(text: str = "") -> "Task | str":
    """
    The task he means, or a question to ask him.

    Returns a Task, or a STRING to say out loud. Guessing between two live
    tasks is how "cancel that" cancels the wrong one, so with more than one
    running it asks - which is the one thing his spec is explicit about.
    """
    live = live_tasks()
    if not live:
        return "I'm not working on anything right now."
    if len(live) == 1:
        return live[0]
    words = (text or "").lower()
    matches = [t for t in live if t.objective and
               any(w in words for w in t.objective.lower().split()[:4] if len(w) > 3)]
    if len(matches) == 1:
        return matches[0]
    listing = "; ".join(f"{t.objective}" for t in live[:4])
    return f"I've got {len(live)} going: {listing}. Which one?"


def describe_progress(text: str = "") -> str:
    """"What are you doing?" / "how far are you?" — a real answer."""
    live = live_tasks()
    if not live:
        recent = [t for t in all_tasks() if t.state in (COMPLETED, FAILED)][:1]
        if recent:
            task = recent[0]
            verb = "finished" if task.state == COMPLETED else "failed at"
            return f"Nothing running. I last {verb} {task.objective}."
        return "Nothing running."
    lines = []
    for task in live[:4]:
        detail = task.progress or task.current_step or task.state.lower().replace("_", " ")
        waiting = f" — waiting for {task.waiting_reason}" if task.waiting_reason else ""
        lines.append(f"{task.objective}: {detail}{waiting} ({task.age()})")
    return "; ".join(lines)


def snapshot() -> dict[str, Any]:
    """Everything, for tests and for the audit log."""
    return {
        "proposal": asdict(_proposal) if _proposal.alive() else None,
        "subject": asdict(_subject) if current_subject() else None,
        "tasks": [asdict(t) for t in all_tasks()],
    }
