r"""
Answering "what are you doing?"

    "it should work without becoming idle and asking the question on the way
     while it is doind its work"
    "a task must not randomly disappear"

Before this there was no way to ask. The only evidence that a turn was still
working was the orb being yellow, which is not an answer to "how far are
you" — and a task that failed inside its own thread left nothing behind at
all.

WHY THIS IS NOT IN selfcontrol.py
---------------------------------
It was, briefly, and a test caught it: `selfcontrol` is enumerated and
asserted to be read-only with respect to Jalen's own SOURCE. `cancel_task`
changes task state, which is a different boundary — the test's own docstring
says "a new tool added here without thinking about the boundary should fail
this rather than slip through", and that is exactly what happened.

The state itself lives in jalen/conversation.py. This is only the door.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def what_are_you_doing(text: str = "") -> str:
    """Everything currently running, and how far along — GREEN."""
    from .. import conversation

    return conversation.describe_progress(text)


# Ending a background job is irreversible - up to an hour of an agent's work
# - so only three things may do it:
#
#   a phrase that can only mean a job     "the coding job", "the background
#                                         job", "the claude code job", "the
#                                         background run"
#   the job's number, as a whole word     "cancel job 3f9a2b1c"
#   its folder, as whole words, WITH a    "stop the job in web" - never
#   job word                              "website"
#
# The first version matched the folder as a raw substring of the sentence
# and treated bare job/agent/claude as naming the job, so a job in "app" was
# ended by "cancel my appointment", and with one job live "cancel the job
# application email" ended it.
_A_JOB_BY_NAME = re.compile(
    r"\b(?:coding|background|claude(?:\s+code)?)\s+(?:jobs?|runs?)\b", re.I)

# Words that might be about a job and might not: "the job application
# email", "stop the agent from emailing". Enough to let a folder name count,
# and to be asked about - never enough on their own to end anything.
_JOB_WORD = re.compile(r"\b(?:jobs?|agents?|claude|background|coding)\b", re.I)

# "don't stop the coding job" contains the coding job too. A VETO is a
# negation that directly governs the stop verb ("don't stop", "do not
# cancel", "never kill", with a filler word or two: "don't you stop").
# It used to be the bare words "don't"/"do not" anywhere in the sentence, so
# "cancel the coding job now, don't let it keep running" and "stop the coding
# job, I don't need it" were refused as if he wanted the job left running -
# reproduced by an independent review of 6d34421. The typographic apostrophe
# (U+2019) is built with chr() so this source line stays ASCII.
_NEGATED = re.compile(
    r"\b(?:don(?:'|" + chr(0x2019) + r")?t|do\s+not|never)\s+"
    r"(?:(?:you|please|ever|just|go\s+ahead\s+and)\s+)*"
    r"(?:stop|cancel|kill|end|quit|abort|halt|terminate|shut)\b", re.I)

_TOKEN = re.compile(r"[^\W_]+")

# Folder names made only of these cannot single a job out: in "stop the
# coding job" the folder "coding" is not what he named.
_NOT_A_NAME = frozenset({
    "a", "an", "the", "my", "in", "on", "of", "for", "it", "that", "this",
    "stop", "cancel", "kill", "end", "coding", "code", "claude", "job", "jobs",
    "agent", "agents", "background", "run", "runs",
})


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _said_in_order(said: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return n > 0 and any(said[i:i + n] == phrase for i in range(len(said) - n + 1))


def _names(said: list[str], job: dict, about_a_job: bool) -> bool:
    """Does what he said single out this job - by its number, or its folder?"""
    job_id = str(job.get("id", "")).lower()
    if job_id and job_id in said:
        return True
    if not about_a_job:
        return False
    folder = _tokens(Path(str(job.get("folder", ""))).name)
    if len("".join(folder)) < 3 or set(folder) <= _NOT_A_NAME:
        return False
    return _said_in_order(said, folder)


def _how_to_name(job: dict) -> str:
    """The words that end this job next time."""
    return ("stop the background job" if job.get("kind") == "command"
            else "stop the coding job")


def _listed(job: dict) -> str:
    from . import devwork

    what = devwork._describe(job)
    where = Path(str(job.get("folder", ""))).name
    if job.get("kind") == "command" and where:
        what = f"{what}, in {where}"
    return f"{what} (job {job.get('id')})"


def _which_one(jobs: list[dict]) -> str:
    listing = "; ".join(_listed(job) for job in jobs[:4])
    return (
        f"There are {len(jobs)} background jobs running: {listing}. Which one? "
        "Say its job number, or the folder it's working in."
    )


def _hint(live: list[dict]) -> str:
    from . import devwork

    if not live:
        return ""
    if len(live) == 1:
        return (f" If you meant {devwork._describe(live[0])}, say "
                f"{_how_to_name(live[0])} and I'll end it.")
    return (f" There are also {len(live)} background jobs running; say stop "
            "the background job and I'll ask which.")


def cancel_task(text: str = "") -> str:
    """
    Stop a running task — GREEN.

    Asks WHICH when more than one is live rather than guessing. Guessing
    between two tasks is how "cancel that" cancels the wrong one, and his
    specification is explicit about it.

    AND IT ONLY SAYS "STOPPED" WHEN SOMETHING STOPPED. This used to write
    CANCELLED into the conversation state and answer "Stopped X." - but
    nothing reads CANCELLED, so the turn ran on and the sentence was false,
    out loud. The one kind of work that can genuinely be ended from here is a
    background job whose process this Jalen holds (devwork.stop_coding_job);
    everything else is marked cancelled and the reply says it cannot be
    interrupted.
    """
    from .. import conversation
    from . import devwork

    words = text or ""
    said = _tokens(words)
    by_name = bool(_A_JOB_BY_NAME.search(words))
    about_a_job = by_name or bool(_JOB_WORD.search(words))
    live = devwork.live_jobs()
    # A job the store still calls running but no process here holds outlived
    # a restart. Named, it gets an honest "I can't stop that from here"
    # rather than "nothing is running".
    orphans = devwork.orphaned_jobs()
    named = [job for job in live + orphans if _names(said, job, about_a_job)]

    if (by_name or named) and (live or orphans) and _NEGATED.search(words):
        job = (named or live or orphans)[0]
        return (
            f"I haven't stopped anything - that sounded like you want "
            f"{devwork._describe(job)} left running. Say {_how_to_name(job)} "
            "if you do want it ended."
        )
    if len(named) == 1:
        return devwork.stop_coding_job(named[0]["id"])
    if len(named) > 1:
        return _which_one(named)
    if by_name:
        if len(live) == 1:
            return devwork.stop_coding_job(live[0]["id"])
        if len(live) > 1:
            return _which_one(live)
        if len(orphans) == 1:
            return devwork.stop_coding_job(orphans[0]["id"])
        if orphans:
            return (
                f"{len(orphans)} background jobs are recorded as running, but "
                "they were started before I last restarted, so I can't stop "
                "them from here. End them in Task Manager if they're still "
                "going."
            )
        return "No background job is running, so there's nothing to stop."

    # Nothing he said singles out a job. If a job is all that is running,
    # ask about it - never end it on a guess.
    if live and not conversation.live_tasks():
        if len(live) > 1:
            return _which_one(live)
        return (
            f"Do you mean {devwork._describe(live[0])}? I won't end it on a "
            f"guess - say {_how_to_name(live[0])} and I will."
        )

    # Otherwise a turn - or nothing at all.
    hint = _hint(live)
    chosen = conversation.which_task(words)
    if isinstance(chosen, str):
        return chosen + hint
    conversation.update_task(chosen.id, state=conversation.CANCELLED)
    return (
        f"I've marked {chosen.objective} as cancelled, but I can't interrupt "
        "that work from here, so it will run to the end on its own." + hint
    )


REGISTRY: dict[str, Any] = {
    "what_are_you_doing": what_are_you_doing,
    "cancel_task": cancel_task,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
