"""
Audit trail (spec F48: "yes, and let me review it daily", F52: transcripts kept,
audio discarded).

Two sinks on purpose:
  - SQLite, so the daily review can query it ("what did you delete yesterday?")
  - JSONL, so it survives a corrupt database and you can read it in Notepad.

Audio is never written. Only text.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,        -- utterance | action | decision | error | system
    tier        TEXT,                 -- green | amber | red | black
    tool        TEXT,
    summary     TEXT,
    outcome     TEXT,                 -- executed | cancelled | blocked | failed | pending
    origin      TEXT,                 -- user | content | schedule | telegram
    detail      TEXT,                 -- json
    session_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_tier ON events(tier);
"""


class AuditLog:
    def __init__(self, cfg, session_id: str) -> None:
        self.enabled = bool(cfg.get_path("audit.enabled", True))
        self.keep_transcripts = bool(cfg.get_path("audit.keep_transcripts", True))
        root = Path(__file__).resolve().parent.parent
        self.db_path = root / cfg.get_path("audit.db_path", "data/audit.db")
        self.jsonl_path = root / cfg.get_path("audit.jsonl_path", "data/audit.jsonl")
        self.session_id = session_id
        self._lock = threading.Lock()
        # memory.private_mode_default was configurable but ignored — Jalen
        # always started logging regardless of the flag (spec G58).
        self._private = bool(cfg.get_path("memory.private_mode_default", False))
        # Set by redact_next_utterance(); consumed by the next utterance().
        self._redact_next = False

        if self.enabled:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        else:  # pragma: no cover
            self._conn = None

    # ---------------------------------------------------------------- private
    def set_private_mode(self, on: bool) -> None:
        """Spec G58. While on, nothing is written at all."""
        self._private = on
        if not on:
            self.write("system", summary="private mode ended")

    @property
    def private(self) -> bool:
        return self._private

    # ------------------------------------------------------------------ write
    def write(
        self,
        kind: str,
        *,
        tier: str | None = None,
        tool: str | None = None,
        summary: str = "",
        outcome: str | None = None,
        origin: str = "user",
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled or self._private or self._conn is None:
            return
        if kind == "utterance" and not self.keep_transcripts:
            return

        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            "tier": tier,
            "tool": tool,
            "summary": summary,
            "outcome": outcome,
            "origin": origin,
            "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
            "session_id": self.session_id,
        }
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO events (ts,kind,tier,tool,summary,outcome,origin,detail,session_id)"
                    " VALUES (:ts,:kind,:tier,:tool,:summary,:outcome,:origin,:detail,:session_id)",
                    row,
                )
                self._conn.commit()
            except sqlite3.Error:
                pass
            try:
                with open(self.jsonl_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            except OSError:
                pass

    # ------------------------------------------------------------ convenience
    #
    # Utterances that CONTAIN a secret, and must be written down as their
    # shape rather than their content.
    #
    # The vault passphrase is the case that matters. Said out loud it becomes
    # an ordinary transcript line, and this file is plain text by design so
    # that it can be read in Notepad — which would put the master passphrase
    # on disk next to the vault it opens.
    #
    # This only covers the ACCIDENT. It is not the fix: a spoken passphrase
    # has already been sent to Groq for transcription before any of this code
    # runs, so it has left the machine regardless. See unlock_vault's
    # docstring — the passphrase should be typed, never spoken.
    # WIDENED after an adversarial review found seven ordinary phrasings that
    # walked straight through the original three. Every one of these was NOT
    # redacted before, and each would have written the vault's master
    # passphrase verbatim into a file that is deliberately readable in
    # Notepad:
    #
    #     "the passphrase for the vault is hunter2"
    #     "the passphrase to unlock everything is hunter2"
    #     "my vault key is hunter2"
    #     "here is the unlock code hunter2 go ahead"
    #     "hunter2 thats the password"
    #     "the master password is hunter2"
    #     "passphrase hunter2"
    #
    # The old patterns demanded a specific word ORDER. People do not speak in
    # a fixed order, so order is no longer required — proximity is. Matching
    # too eagerly costs one blanked audit line; matching too rarely costs the
    # one secret the vault exists to protect, permanently, on disk. Those are
    # not comparable, so this errs toward blanking.
    _SECRET_SHAPES = (
        # vault/master near anything credential-shaped, in EITHER order
        re.compile(r"\b(?:vault|master)\b.{0,60}\b(?:pass\w*|key|code|phrase)\b", re.I),
        re.compile(r"\b(?:pass\w*|key|code|phrase)\b.{0,60}\b(?:vault|master)\b", re.I),
        # "the passphrase ... is X", however it is dressed up
        re.compile(r"\bpass(?:phrase|word|code)\b.{0,40}\bis\b", re.I),
        re.compile(r"\bis\b.{0,20}\b(?:the )?pass(?:phrase|word|code)\b", re.I),
        # unlocking, near anything credential-shaped
        re.compile(r"\b(?:un)?lock(?:ing)?\b.{0,60}\b(?:vault|pass\w*|code|key)\b", re.I),
        # a bare "passphrase hunter2" — the word, then a token
        re.compile(r"\bpass(?:phrase|word|code)\b\s+\S{4,}", re.I),
        re.compile(r"\bmy\s+(?:vault\s+)?(?:pass\w*|key|code)\b", re.I),
        # The secret said FIRST: "hunter2, that's the password". Word order
        # is not something a speaker commits to, and the value is already
        # past by the time the giveaway word arrives.
        re.compile(r"\bth(?:at|is)'?s?\s+(?:the\s+|my\s+)?(?:pass\w*|key|code)\b", re.I),
    )

    def utterance(self, text: str, *, who: str = "user", origin: str = "user") -> None:
        summary = text
        if who == "user" and any(p.search(text or "") for p in self._SECRET_SHAPES):
            summary = "[vault passphrase spoken - not recorded]"
        elif self._redact_next and who == "user":
            summary = "[sensitive answer - not recorded]"
        self._redact_next = False
        self.write("utterance", summary=summary, origin=origin, detail={"who": who})

    def redact_next_utterance(self) -> None:
        """
        The next thing he says is an answer to a question about a secret.

        Set by the caller that ASKED — ask_user, when the question mentions a
        passphrase or password. Without it, "what's the passphrase?" followed
        by the passphrase logs the answer with no context to catch it by: the
        utterance on its own looks like any other sentence.
        """
        self._redact_next = True

    def action(self, verdict, outcome: str, error: str | None = None) -> None:
        detail = dict(verdict.detail)
        if error:
            detail["error"] = error
        self.write(
            "action",
            tier=verdict.tier.value,
            tool=verdict.tool,
            summary=verdict.summary,
            outcome=outcome,
            origin=verdict.detail.get("origin", "user"),
            detail=detail,
        )

    def error(self, where: str, exc: BaseException) -> None:
        self.write(
            "error",
            summary=f"{where}: {type(exc).__name__}: {exc}",
            outcome="failed",
            detail={"where": where},
        )

    # ----------------------------------------------------------------- review
    def since(self, hours: int = 24) -> list[dict[str, Any]]:
        if self._conn is None:
            return []
        cutoff = time.time() - hours * 3600
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
        cur = self._conn.execute(
            "SELECT ts,kind,tier,tool,summary,outcome FROM events"
            " WHERE ts >= ? ORDER BY ts DESC",
            (cutoff_iso,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def daily_digest(self) -> str:
        """Plain-text summary for the 21:00 Telegram review (spec F48)."""
        rows = self.since(24)
        if not rows:
            return "Nothing to report in the last 24 hours."
        actions = [r for r in rows if r["kind"] == "action"]
        executed = [r for r in actions if r["outcome"] == "executed"]
        blocked = [r for r in actions if r["outcome"] in ("blocked", "cancelled")]
        errors = [r for r in rows if r["kind"] == "error"]
        turns = len([r for r in rows if r["kind"] == "utterance"])

        lines = [
            "Jalen — last 24 hours",
            "",
            f"{turns} things you said, {len(executed)} actions taken, "
            f"{len(blocked)} stopped, {len(errors)} errors.",
        ]
        red = [r for r in executed if r["tier"] == "red"]
        if red:
            lines += ["", "Irreversible actions you approved:"]
            lines += [f"  - {r['summary']}" for r in red[:20]]
        if blocked:
            lines += ["", "Stopped or refused:"]
            lines += [f"  - {r['summary']} ({r['outcome']})" for r in blocked[:20]]
        if errors:
            lines += ["", "Errors:"]
            lines += [f"  - {r['summary']}" for r in errors[:10]]
        return "\n".join(lines)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
