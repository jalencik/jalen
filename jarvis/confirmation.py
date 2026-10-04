"""
One confirmation, as one object.

THE OWNER'S RULE (2026-10-04): only two kinds of action stop and ask him -
deleting something, and anything that goes OUT through Telegram. Everything
else he asks for runs.

A confirmation used to be a sentence. The hook built "send ... Confirm?",
app.confirm() spoke it and waited, and whatever "yes" came back approved
whatever the tool did next. That cannot bind anything: the tool re-resolved
the name after the yes, so the person he heard named was not necessarily the
person the message went to, and a yes that began before the question did
could still be taken for its answer.

This object is the question AND what it is about. The same instance is
spoken, shown, waited on and checked: `question` is the exact sentence that
reaches the speakers and the transcript window, and `binding` is what a yes to
it approves (a frozen Telegram peer id and the hash of the exact words, or the
exact list of paths to delete). A yes approves this id and nothing else.

Timestamps are time.monotonic(), stamped by app.confirm() as the question
moves through its life, so the audit row can say where the time went:

    created_at            the hook decided to ask
    question_started_at   the first word of the question left the speakers
    spoken_at             the last word did (the echo window counts from here)
    listening_started_at  the microphone was handed back to him
    first_sound_at        a sound opened a window while it was waiting
    decided_at            an answer, a timeout or a kill switch closed it

Never put a secret in `question` or `binding`: both are written to the audit
log and the question is read aloud.
"""
from __future__ import annotations

import hashlib
import itertools
import time
from dataclasses import dataclass, field
from typing import Any

DELETE = "delete"
TELEGRAM = "telegram"
OTHER = "other"

_ids = itertools.count(1)


def content_digest(text: Any) -> str:
    """A short, stable fingerprint of exactly these words (or bytes)."""
    if isinstance(text, bytes):
        data = text
    else:
        data = str(text if text is not None else "").encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest()[:16]


@dataclass
class Confirmation:
    question: str
    kind: str = OTHER
    action: str = ""                 # the tool name, e.g. "send_telegram_message"
    target: str = ""                 # what he hears it is about, normalised
    binding: dict = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_ids))
    created_at: float = field(default_factory=time.monotonic)
    question_started_at: float = 0.0
    spoken_at: float = 0.0
    listening_started_at: float = 0.0
    first_sound_at: float = 0.0
    decided_at: float = 0.0
    outcome: str = ""                # yes / no / timeout / correction / unspoken

    def timings_ms(self) -> dict:
        """Milliseconds from creation to each stamp that happened - for the audit row."""
        out = {}
        for name in ("question_started_at", "spoken_at", "listening_started_at",
                     "first_sound_at", "decided_at"):
            stamp = getattr(self, name)
            if stamp:
                out[name.replace("_at", "_ms")] = round((stamp - self.created_at) * 1000)
        return out

    def audit_detail(self) -> dict:
        """What the audit row records. The question is his words to himself; no secrets."""
        return {
            "confirmation_id": self.id,
            "kind": self.kind,
            "action": self.action,
            "target": self.target[:200],
            "outcome": self.outcome,
            **self.timings_ms(),
        }


def as_confirmation(question_or_confirmation: "str | Confirmation") -> Confirmation:
    """Callers that still pass a bare sentence get a generic confirmation for it."""
    if isinstance(question_or_confirmation, Confirmation):
        return question_or_confirmation
    return Confirmation(question=str(question_or_confirmation or ""))
