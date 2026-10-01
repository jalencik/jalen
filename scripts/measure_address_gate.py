r"""
Replay the real "ignored - not addressed to Jalen" rows through the CURRENT
address gate, and say how many it would act on now.

    .venv\Scripts\python.exe scripts\measure_address_gate.py
    .venv\Scripts\python.exe scripts\measure_address_gate.py --log D:\copy\audit.jsonl
    .venv\Scripts\python.exe scripts\measure_address_gate.py --show

WHY THIS EXISTS. "He pretends to be deaf" is a count, not a feeling, and the
count changes every time the gate does - so the number belongs in a script
anyone can re-run, not in a commit message nobody can check. Everything it
reports comes from data/audit.jsonl and the real Jalen.should_act_on; nothing
is estimated by a second implementation of the gate.

NINE MEASUREMENTS

    1. REPLAY. Each logged "ignored" row is put back through should_act_on
       with the state Jalen was in at that moment, rebuilt from the rows
       before it in the same session: what he last said and when, whether it
       ended in a question (an open answer window), which sound opened the
       microphone (follow-up window, answer window or barge-in) and, if a
       question was open when he BEGAN speaking, that question - see
       _started_at and _opened_by. An answer closes its window, as it does
       live: of the rows that arrive after one question, only the first the
       gate accepts is counted.
    2. FALSE ACCEPTS. The 105 rows were read by hand (LABELS below, ordinals
       only - no personal text) as plausibly for him (F), ambiguous (A) or
       background / hallucination / his own voice (B). A widening is judged by
       how many A+B rows it ALSO accepts.
    3. PER DOOR. The replay is run again with one door of router.all_doors
       switched off at a time: what that door rescues that nothing else does
       (`alone`) and what it lets in that nothing else would (`false`).
    4. ECHO AT ONCE. Every Jalen reply in the log is fed back to the gate whole,
       from its midpoint and as its last five words, in the state "he just said
       it". Anything accepted is Jalen answering himself.
    5. ECHO WHILE HE SPEAKS, AND AFTER. The same replies, every form a door
       would admit, fed back 5s and 10s after the reply began, half way
       through, and 2s and 8s after it stopped. 157 of the 823 take 12s or
       more to say. Anything accepted is Jalen obeying himself.
    6. THE CURRENT ERA. The answer window shipped on 2026-09-20 (commit
       05378c4); 97 of the 105 rows are older than it and were refused by a
       gate that no longer exists. The last 8 are the rows the gate as it
       stood just before this change really produced, and they are reported
       on their own line.
    7. THE ANSWER PATH, BY WHEN THE SOUND BEGAN. Every question of Jalen's,
       whole, from its middle and its last five words, fed back to the window
       it opens: a sound that began while he was still on air or inside the
       3s tail must never be taken for the answer, and the same words that
       began after the tail must be ("just the last message"). The gate is
       asked at several delays after the sound, because it is asked after
       the sentence, the endpoint silence and the transcription.
    8. HIS REAL REQUESTS AGAINST A REPLY IN THE ROOM. The real sentences a
       door's shape matches (103), each with the reply that came before it,
       the longest reply in the log, the one nearest 200 words and a 2,300
       word read: how many does the echo test refuse? Run the script from an
       older checkout to get the "before" - the gate is asked only what it
       knows.
    9. WHAT WAS ON AIR, COMING BACK. Every reply on a timeline; the sentence
       playing at five moments of it, that and the next, its last five words,
       each whole and mangled: how many are taken for him (the number that
       must be 0). And the cost side: how soon his real answers began.

WHAT IT CANNOT KNOW. Why a microphone window opened, and when his sentence
began, are not logged, so both are estimated from timing (see _started_at);
the label of each row is a judgement by one reader; and the log holds 12 clear
background rows, so "0 false accepts" is "0 of 12 (0 of 27 counting the
ambiguous ones)", not a rate. A line below says how much the estimate of when
he began matters (slow, medium and fast speech).

And "0 of 27" is IN-SAMPLE: the doors' regexes were written while reading
those rows, and they contain no person speaking to another person, which is
what a door without his name admits by construction. The log cannot measure
that. Every sentence a door admits is now written to the audit log as
"acted on without his name - <door>", and the ignored rows carry `opened_by`
and `vouched_by`, so the next measurement can be made on rows nobody has
labelled yet.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Hand labels for the 105 rows present in data/audit.jsonl on 2026-10-01,
# keyed by the 1-based order in which they appear. F = plausibly for him,
# A = ambiguous, B = background / hallucination / his own voice.
#   ANS answer to something he was just asked   REQ polite unnamed request
#   CONT speech to him, no name, no request     MISH his name misheard at the start
#   TRAIL name present but not first            FRAG fragment of a sentence
#   VENT complaint aimed at him                 NAME already addressed by name
#   HALL hallucination / music / TV             ECHO his own voice
LABELS = {
    1: ("F", "ANS"), 2: ("F", "ANS"), 3: ("F", "REQ"), 4: ("F", "ANS"),
    5: ("F", "REQ"), 6: ("F", "MISH"), 7: ("A", "FRAG"), 8: ("F", "ANS"),
    9: ("F", "MISH"), 10: ("F", "MISH"), 11: ("F", "FRAG"), 12: ("F", "FRAG"),
    13: ("F", "ANS"), 14: ("F", "ANS"), 15: ("F", "MISH"), 16: ("F", "ANS"),
    17: ("F", "MISH"), 18: ("F", "FRAG"), 19: ("A", "CONT"), 20: ("F", "REQ"),
    21: ("F", "MISH"), 22: ("F", "FRAG"), 23: ("F", "MISH"), 24: ("F", "ANS"),
    25: ("F", "ANS"), 26: ("F", "CONT"), 27: ("F", "REQ"), 28: ("A", "FRAG"),
    29: ("A", "FRAG"), 30: ("F", "MISH"), 31: ("A", "HALL"), 32: ("B", "HALL"),
    33: ("A", "HALL"), 34: ("B", "HALL"), 35: ("F", "MISH"), 36: ("F", "REQ"),
    37: ("F", "MISH"), 38: ("F", "ANS"), 39: ("F", "TRAIL"), 40: ("F", "VENT"),
    41: ("F", "ANS"), 42: ("F", "REQ"), 43: ("F", "ANS"), 44: ("F", "CONT"),
    45: ("F", "ANS"), 46: ("F", "ANS"), 47: ("F", "CONT"), 48: ("F", "CONT"),
    49: ("F", "CONT"), 50: ("F", "CONT"), 51: ("F", "TRAIL"), 52: ("A", "FRAG"),
    53: ("A", "FRAG"), 54: ("F", "CONT"), 55: ("F", "CONT"), 56: ("F", "TRAIL"),
    57: ("F", "REQ"), 58: ("F", "CONT"), 59: ("F", "CONT"), 60: ("F", "FRAG"),
    61: ("F", "CONT"), 62: ("F", "ANS"), 63: ("F", "TRAIL"), 64: ("F", "ANS"),
    65: ("F", "MISH"), 66: ("F", "ANS"), 67: ("F", "TRAIL"), 68: ("A", "FRAG"),
    69: ("F", "CONT"), 70: ("F", "REQ"), 71: ("F", "CONT"), 72: ("F", "CONT"),
    73: ("F", "CONT"), 74: ("B", "ECHO"), 75: ("B", "ECHO"), 76: ("B", "ECHO"),
    77: ("F", "TRAIL"), 78: ("B", "HALL"), 79: ("B", "HALL"), 80: ("F", "MISH"),
    81: ("F", "NAME"), 82: ("B", "HALL"), 83: ("B", "HALL"), 84: ("F", "REQ"),
    85: ("F", "REQ"), 86: ("F", "REQ"), 87: ("F", "ANS"), 88: ("F", "REQ"),
    89: ("F", "CONT"), 90: ("A", "HALL"), 91: ("B", "HALL"), 92: ("B", "HALL"),
    93: ("A", "CONT"), 94: ("F", "REQ"), 95: ("F", "MISH"), 96: ("F", "TRAIL"),
    97: ("F", "ANS"), 98: ("A", "HALL"), 99: ("F", "MISH"), 100: ("F", "CONT"),
    101: ("B", "HALL"), 102: ("F", "TRAIL"), 103: ("A", "HALL"), 104: ("A", "CONT"),
    105: ("A", "HALL"),
}

# The answer window shipped in commit 05378c4, 2026-09-20 14:19:12 +05:00.
ANSWER_WINDOW_SHIPPED = "2026-09-20T09:19:12"

# When did his sentence BEGIN? The log keeps when the row was written, which
# is after the sentence, the endpoint and the transcription:
#   began = written - (words / words-per-second + endpoint + transcription)
# ENDPOINT 1.4s is the `endpoint=1400ms` on every timing row of the fast path;
# TRANSCRIPTION 1.8s is the median of `heard=` over the 349 timing rows
# (p95 4.4s). WORDS PER SECOND is NOT MEASURED - the audio is not kept - so it
# is a parameter, 2.6 by default (about 155 words a minute, conversational
# English) and the report says what 2.0 and 3.4 would change. The log keeps
# 200 characters of each sentence, so a long one is undercounted and starts
# LATER than it did: the estimate errs against a rescue, not for one.
_WORDS_PER_S = 2.6
_ENDPOINT_S = 1.4
_TRANSCRIPTION_S = 1.8
# A "barge-in stopped playback" row is written the moment the sound begins, and
# the sentence that comes out of that window begins then too. So a refused
# sentence is attributed to the barge-in window when its ESTIMATED start is
# within this many seconds of the row. Measured on the log: 22 refused
# sentences follow a barge-in row with no acted-on sentence between them; 13
# have an estimated start between -1.4s and +2.6s of it (they are the ones that
# look like one window), and the other 9 are 3.9s to 18.5s later, which is a
# later, different window that a noise-triggered barge-in merely preceded.
_BARGE_ALIGNS_S = 3.0
_WORD = re.compile(r"[\w']+", re.UNICODE)


def audit_log_path(explicit: Path | None = None) -> Path:
    """--log, else $JALEN_AUDIT_LOG, else data/audit.jsonl."""
    if explicit is not None:
        return explicit
    env = os.environ.get("JALEN_AUDIT_LOG")
    return Path(env) if env else ROOT / "data" / "audit.jsonl"


def _details(row: dict) -> dict:
    raw = row.get("detail")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return raw or {}


def _epoch(row: dict) -> float:
    return datetime.fromisoformat(row["ts"]).timestamp()


def load(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


class _Clock:
    """time.monotonic and time.time, pinned to a row's own timestamp."""

    def __init__(self) -> None:
        self.now = 0.0
        self._mono, self._wall = time.monotonic, time.time

    def __enter__(self):
        time.monotonic = lambda: self.now
        time.time = lambda: self.now
        return self

    def __exit__(self, *exc):
        time.monotonic, time.time = self._mono, self._wall


def _make_gate_class():
    from jarvis.app import Jalen
    from jarvis.config import CONFIG

    class ReplayGate:
        """Just enough Jalen to ask 'would you act on this?' - the real methods."""

        def __init__(self) -> None:
            self._awaiting_confirmation = False
            self._awaiting_stop = False
            self._awaiting_reply = False
            self._pending_rating = None
            self._last_user_text = ""
            self._last_user_at = 0.0
            self._last_reply_text = ""
            self._last_reply_at = 0.0
            self._expecting = None
            self._answer_window_s = float(CONFIG.get_path("conversation.answer_window_s", 30))
            self._answer_overrun_s = _overrun_s()
            self.cfg = CONFIG
            self.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))

        RATING_EXPIRES_S = Jalen.RATING_EXPIRES_S

    # The real methods, not stand-ins - the same set tests/test_address_gate.py
    # binds into its stub. Names a checkout does not have yet (this script is
    # also run at an older commit to reproduce a "before") are skipped.
    for name in ("should_act_on", "_continues_last_utterance", "_expectation_open",
                 "_rating_is_pending", "_expect_an_answer", "_forget_expectation",
                 "_sounds_like_its_own_voice", "_open_expectation",
                 "_window_outlived_by_the_sentence"):
        if hasattr(Jalen, name):
            setattr(ReplayGate, name, getattr(Jalen, name))
    return ReplayGate


def _overrun_s() -> float:
    from jarvis import app
    from jarvis.config import CONFIG
    return float(CONFIG.get_path("vad.max_utterance_s", 30)) + getattr(app, "ANSWER_STT_SLACK_S", 0.0)


def _ask(gate, text: str, *, opened_by: str = "", vouched_by=None,
         began_at: float | None = None) -> bool:
    """Ask with whatever the checkout's gate understands (older ones know less)."""
    params = inspect.signature(type(gate).should_act_on).parameters
    kwargs = {}
    if "opened_by" in params:
        kwargs["opened_by"] = opened_by
    if "vouched_by" in params:
        kwargs["vouched_by"] = vouched_by
    if "began_at" in params and began_at is not None:
        kwargs["began_at"] = began_at
    return bool(gate.should_act_on(text, False, **kwargs))


def _started_at(ts: float, text: str, words_per_s: float) -> float:
    words = len(_WORD.findall(text))
    return ts - (words / words_per_s + _ENDPOINT_S + _TRANSCRIPTION_S)


def _opened_by(started: float, reply_end: float | None, expecting: bool,
               follow_up_s: float, barge: bool = False) -> str:
    """
    Estimate which sound-gate opened the window this sentence came out of.

    Every one of these rows was REFUSED, and the wake word and the hotkey are
    always accepted - so each came out of a window a SOUND opened: the
    follow-up window, an open question, or barge-in. Barge-in is known from
    its own log row ("barge-in stopped playback ..."); a question that was
    open and a start after the follow-up window gives "answer". The rest are
    follow-up windows, including the 10 of the 105 rows that the timing
    estimate puts 13 to 109 seconds after the reply with no question open:
    that estimate errs LATE for a long sentence (the log keeps 200 characters
    of it, so its length is undercounted), the follow-up window has been 12
    seconds since the first commit, and the microphone cannot have opened
    outside one. The first version of this script left them as "" (no window
    known), which silently refused them for every exemption that needs a
    window - and made the replay under-count the name door by 3 rows for a
    reason that lived in the estimate, not the gate. Of the 105: 76 are
    estimated inside the follow-up window, 6 in an open question, 13 in
    barge-in and these 10 by this fallback.
    """
    if barge:
        return "barge"
    if reply_end is None:
        return ""
    if started - reply_end <= follow_up_s:
        return "follow_up"
    return "answer" if expecting else "follow_up"


def replay(rows: list[dict], force_opened_by: str | None = None,
           words_per_s: float = _WORDS_PER_S) -> list[dict]:
    """
    `force_opened_by` replaces the ESTIMATED window kind with a fixed one -
    "follow_up" is the worst case for the one exemption that depends on it,
    since it asks "what if every one of these had arrived in that window?".
    """
    from jarvis.app import Expectation
    from jarvis.brain.router import solicits_an_answer
    from jarvis.config import CONFIG

    Gate = _make_gate_class()
    follow_up_s = float(CONFIG.get_path("conversation.follow_up_timeout_s", 12))
    answer_s = float(CONFIG.get_path("conversation.answer_window_s", 30))

    # Reply end = the timing row that follows the reply, when there is one.
    reply_end_of: dict[int, float] = {}
    for i, row in enumerate(rows):
        if row["kind"] == "utterance" and _details(row).get("who") != "user":
            end = _epoch(row)
            for j in range(i + 1, min(i + 8, len(rows))):
                nxt = rows[j]
                if nxt["session_id"] != row["session_id"]:
                    break
                if nxt["kind"] == "utterance":
                    break
                if nxt["summary"].startswith("timing"):
                    end = max(end, _epoch(nxt))
                    break
            reply_end_of[i] = end

    results: list[dict] = []
    state: dict[str, dict] = {}
    ordinal = 0
    with _Clock() as clock:
        for i, row in enumerate(rows):
            sid = row["session_id"]
            st = state.setdefault(sid, {"reply": "", "end": None, "last_user": "",
                                        "last_user_at": 0.0, "expect": None,
                                        "barge_at": None})
            if row["kind"] == "utterance":
                if _details(row).get("who") != "user":
                    # NOT a reset of barge_at: a streamed reply is written when
                    # it has finished playing, which is after the barge-in
                    # that cut it short.
                    st["reply"] = row["summary"]
                    st["end"] = reply_end_of.get(i, _epoch(row))
                    st["expect"] = (
                        (st["end"], row["summary"]) if solicits_an_answer(row["summary"]) else None
                    )
                else:
                    st["last_user"] = row["summary"]
                    st["last_user_at"] = _epoch(row)
                    st["expect"] = None
                    st["barge_at"] = None      # that window produced an acted-on sentence
                continue
            if row["summary"].startswith("barge-in stopped"):
                st["barge_at"] = _epoch(row)
                continue
            if not row["summary"].startswith("ignored"):
                continue

            ordinal += 1
            text = _details(row).get("heard", "")
            ts = _epoch(row)
            clock.now = ts
            started = _started_at(ts, text, words_per_s)
            def build(st=st, ts=ts, started=started):
                """The state Jalen was in when this row was written, fresh each call."""
                gate = Gate()
                gate._last_reply_text = st["reply"]
                gate._last_reply_at = min(st["end"], ts) if st["end"] else 0.0
                gate._last_user_text = st["last_user"]
                gate._last_user_at = st["last_user_at"]
                vouched = None
                if st["expect"]:
                    opened_at, question = st["expect"]
                    opened_at = min(opened_at, ts)
                    gate._expecting = Expectation(question=question, turn_id=0,
                                                  opened_at=opened_at,
                                                  expires_at=opened_at + answer_s)
                    # What run() would have remembered: the question that was
                    # open when the sound began and opened the window.
                    if opened_at <= started < gate._expecting.expires_at:
                        vouched = gate._expecting
                return gate, vouched

            gate, vouched = build()
            expecting = gate._expecting is not None and ts < gate._expecting.expires_at
            # Barge-in wrote its own row when it fired, which is when the
            # sentence that came out of that window began.
            barged = st["barge_at"] is not None and abs(started - st["barge_at"]) <= _BARGE_ALIGNS_S
            st["barge_at"] = None              # one barge-in, one sentence
            opened_by = force_opened_by if force_opened_by is not None else _opened_by(
                started, st["end"], expecting or vouched is not None, follow_up_s, barged)
            label, cause = LABELS.get(ordinal, ("?", "?"))
            accepted = _ask(gate, text, opened_by=opened_by, vouched_by=vouched)
            # The same state asked again WITHOUT the question that was open
            # when he began: what the gate said before it knew about it. That
            # is the whole contribution of the outlived-window rule.
            rescued = False
            if accepted and vouched is not None:
                before_it, _ = build()
                rescued = not _ask(before_it, text, opened_by=opened_by)
            if accepted and st["expect"] and gate._expecting is None:
                # One answer closes the window, as it does live; the rows that
                # follow it in the log would have found it shut.
                st["expect"] = None
            results.append({
                "n": ordinal, "text": text, "label": label, "cause": cause,
                "opened_by": opened_by, "accepted": accepted, "ts": row["ts"],
                "vouched": vouched is not None, "rescued": rescued,
            })
    return results


def echo_corpus(rows: list[dict]) -> tuple[int, int, int]:
    """(replies, forms tried, forms the gate would act on)."""
    from jarvis.app import Expectation
    from jarvis.brain.router import solicits_an_answer
    from jarvis.config import CONFIG

    Gate = _make_gate_class()
    answer_s = float(CONFIG.get_path("conversation.answer_window_s", 30))
    replies = tried = accepted = 0
    with _Clock() as clock:
        clock.now = 1_000_000.0
        for row in rows:
            if row["kind"] != "utterance" or _details(row).get("who") == "user":
                continue
            reply = row["summary"]
            words = reply.split()
            if len(words) < 3:
                continue
            replies += 1
            forms = {reply, " ".join(words[len(words) // 2:]), " ".join(words[-5:])}
            for form in forms:
                tried += 1
                gate = Gate()
                gate._last_reply_text = reply
                gate._last_reply_at = clock.now
                expectation = None
                if solicits_an_answer(reply):
                    expectation = Expectation(question=reply, turn_id=0, opened_at=clock.now,
                                              expires_at=clock.now + answer_s)
                    gate._expecting = expectation
                # In the follow-up window, which is where a widening is widest,
                # and WITH the window that opened it vouching for the sound.
                if _ask(gate, form, opened_by="follow_up", vouched_by=expectation):
                    accepted += 1
    return replies, tried, accepted


class _Voice:
    """The two things the gate asks of the speaker: is he on air, and since when not."""

    def __init__(self, speaking: bool = False, ended_at: float | None = None) -> None:
        self.speaking = speaking
        self.ended_at = ended_at

    def quiet_for(self) -> float:
        if self.speaking:
            return 0.0
        return float("inf") if self.ended_at is None else time.monotonic() - self.ended_at


def _forms(reply: str) -> set[str]:
    """How a reply comes back through the speakers: whole, from its middle, its last five words, and per sentence."""
    words = reply.split()
    sentence = re.compile(r"(?<=[.!?])\s+")
    return {reply, " ".join(words[len(words) // 2:]), " ".join(words[-5:]), *sentence.split(reply)}


def echo_while_speaking(rows: list[dict]) -> dict:
    """
    Every reply Jalen ever said, fed back to the gate while it is still being
    spoken and just after - at 5s and 10s after it began, half way through, and
    2s and 8s after it stopped - in the follow-up window and in barge-in. Only
    what a DOOR would admit is at stake (anything else is refused for lacking
    his name whatever the echo test says), so only those forms are asked.

    How long a reply takes to say is characters / app.SPOKEN_CHARS_PER_SECOND
    (22.4, measured). `tried` forms were asked, `on_air` of them while the
    reply was still being spoken, and `admitted` is what the gate would act on:
    the number that must be 0.
    """
    from jarvis.app import SPOKEN_CHARS_PER_SECOND
    from jarvis.brain import router

    Gate = _make_gate_class()
    replies = [r["summary"] for r in rows
               if r["kind"] == "utterance" and _details(r).get("who") != "user"]
    out = {"replies": len(replies), "long": 0, "tried": 0, "on_air": 0, "admitted": 0, "leaked": []}
    with _Clock() as clock:
        clock.now = 1_000_000.0
        for reply in replies:
            spoken_s = len(reply) / SPOKEN_CHARS_PER_SECOND
            out["long"] += spoken_s >= 12.0
            for began_s_ago in (5.0, 10.0, spoken_s / 2, spoken_s + 2.0, spoken_s + 8.0):
                for form in _forms(reply):
                    if len(form.split()) < 3:
                        continue
                    for window in ("follow_up", "barge"):
                        if router.widened_door(form, window) is None:
                            continue
                        gate = Gate()
                        gate.speaker = _Voice()
                        gate._last_reply_text = reply
                        gate._last_reply_at = clock.now - began_s_ago
                        gate.speaker.speaking = began_s_ago < spoken_s
                        if not gate.speaker.speaking:
                            gate.speaker.ended_at = clock.now - (began_s_ago - spoken_s)
                        out["tried"] += 1
                        out["on_air"] += gate.speaker.speaking
                        if _ask(gate, form, opened_by=window):
                            out["admitted"] += 1
                            out["leaked"].append((round(began_s_ago, 1), window, form[:70]))
    return out


def answer_path_echo(rows: list[dict], began_s: float, gate_s: float) -> tuple[int, int]:
    """
    The answer path - the window a QUESTION opens, which has no name and no
    door - and a question's own words coming back through the microphone.

    `began_s`  the sound began this many seconds AFTER he stopped talking; a
               negative number is a sound that began that long BEFORE he
               stopped, i.e. while he was still on air.
    `gate_s`   the sentence reached the address gate this many seconds after
               he stopped: the sentence itself, the endpoint silence and the
               transcription, so about `began_s` + 3.2 at the median.

    Returns (forms tried, forms acted on as an answer). The gate used to be
    asked only when it was reached, so a sound that began at 0.5s was judged at
    3.7s and had left the 3s tail behind: 718 of 718 acted on. It is now told
    when the sound began (should_act_on's `began_at`).
    """
    from jarvis.app import Expectation
    from jarvis.brain.router import solicits_an_answer
    from jarvis.config import CONFIG

    Gate = _make_gate_class()
    answer_s = float(CONFIG.get_path("conversation.answer_window_s", 30))
    tried = accepted = 0
    with _Clock() as clock:
        clock.now = 1_000_000.0
        for row in rows:
            if row["kind"] != "utterance" or _details(row).get("who") == "user":
                continue
            reply = row["summary"]
            if len(reply.split()) < 3 or not solicits_an_answer(reply):
                continue
            ended = clock.now - gate_s
            began = ended + began_s
            for form in {reply, " ".join(reply.split()[len(reply.split()) // 2:]),
                         " ".join(reply.split()[-5:])}:
                if len(form.split()) < 3:
                    continue
                gate = Gate()
                gate.speaker = _Voice(ended_at=ended)
                gate._last_reply_text = reply
                gate._last_reply_at = ended
                gate._expecting = Expectation(question=reply, turn_id=0, opened_at=ended,
                                              expires_at=ended + answer_s)
                tried += 1
                accepted += _ask(gate, form, opened_by="answer", vouched_by=gate._expecting,
                                 began_at=began)
    return tried, accepted


# ---------------------------------------------------------------------------
# A SPEAKER WITH A PAST. The gate asks the speaker three things: is he on air,
# how long since he stopped, and (new) which sentences were on air since a
# moment. Everything below puts a reply on a timeline at the measured speaking
# rate (app.SPOKEN_CHARS_PER_SECOND, sentence by sentence as the speaker plays
# it) and asks the gate about a sound that began at a chosen moment of it.
# ---------------------------------------------------------------------------
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# How long one of HIS sentences takes to say, endpoint and transcription: the
# same figures as _started_at (2.6 words a second NOT MEASURED, 1.4s fast
# endpoint, 1.8s median transcription over the 349 timing rows).
_PIPELINE_S = _ENDPOINT_S + _TRANSCRIPTION_S


class _Timeline:
    """A reply being spoken from `began`, sentence by sentence, as the speaker plays it."""

    def __init__(self, reply: str, began: float) -> None:
        from jarvis.app import SPOKEN_CHARS_PER_SECOND
        from jarvis.audio.tts import clean_for_speech, split_sentences

        self.began = began
        self.sentences: list[tuple[str, float, float]] = []
        at = began
        for sentence in split_sentences(clean_for_speech(reply)):
            took = len(sentence) / SPOKEN_CHARS_PER_SECOND
            self.sentences.append((sentence, at, at + took))
            at += took
        self.ends = at

    @property
    def speaking(self) -> bool:
        return self.began <= time.monotonic() < self.ends

    def quiet_for(self) -> float:
        now = time.monotonic()
        if self.speaking:
            return 0.0
        return float("inf") if now < self.began else now - self.ends

    def spoken_since(self, since: float) -> str:
        now = time.monotonic()
        return " ".join(text for text, began, ended in self.sentences
                        if began <= now and ended >= since)


_GATE_CLASS = None


def _gate_after(reply: str, timeline: "_Timeline", at: float):
    """A gate in the state it would be in at `at`, with `timeline` as its speaker."""
    global _GATE_CLASS
    if _GATE_CLASS is None:
        _GATE_CLASS = _make_gate_class()
    gate = _GATE_CLASS()
    gate.speaker = timeline
    if at < timeline.ends:
        # Still being spoken: handed over, not yet filed (the streamed path).
        gate._last_reply_text = "Okay."
        gate._last_reply_at = timeline.began - 600.0
        gate._voice_so_far = reply
    else:
        gate._last_reply_text = reply
        gate._last_reply_at = timeline.ends
        gate._voice_so_far = ""
    return gate


def paired_door_requests(rows: list[dict]) -> list[tuple[str, str]]:
    """
    (what he said, the reply Jalen said just before it) for every sentence of
    his that a door's SHAPE matches and that does not start with his name - the
    sentences the doors exist for, and so the ones an echo test must not
    refuse. All of them were acted on (they are rows of `utterance`, who=user).
    """
    from jarvis.brain import router

    last_reply: dict[str, str] = {}
    out = []
    for row in rows:
        if row["kind"] != "utterance":
            continue
        if _details(row).get("who") != "user":
            last_reply[row["session_id"]] = row["summary"]
            continue
        text = row["summary"]
        if router.widened_door(text, "follow_up") and not router.addressed_to_jalen(text):
            out.append((text, last_reply.get(row["session_id"], "")))
    return out


def _read_aloud_of(replies: list[str], how_many: int = 6) -> str:
    """The `how_many` longest replies one after the other: what 'read it all' of a long thread sounds like."""
    longest = sorted(replies, key=lambda r: len(r.split()), reverse=True)[:how_many]
    return " ".join(longest)


def requests_against_replies(rows: list[dict]) -> dict:
    """
    HOW MANY REAL REQUESTS DOES THE ECHO TEST REFUSE? Each of the sentences
    `paired_door_requests` returns is asked of the gate with a reply in the
    room, in the follow-up window, twice: while the reply is on air (the sound
    began 60% of the way through it) and after it (the sound began 2s after it
    stopped, which is inside ECHO_TAIL_S). The same request is paired with

      own        the reply that really came before it
      longest    the longest reply in the log
      200 words  the reply nearest 200 words
      read       the six longest replies one after the other - 'read it all'

    Anything refused is refused by the echo test and nothing else: every one
    of them was acted on when he said it. {pairing: (asked, refused, [examples])}.
    """
    pairs = paired_door_requests(rows)
    replies = [r["summary"] for r in rows
               if r["kind"] == "utterance" and _details(r).get("who") != "user"]
    pairings = {
        "own": None,
        "longest": max(replies, key=lambda r: len(r.split())),
        "200 words": min(replies, key=lambda r: abs(len(r.split()) - 200)),
        "read": _read_aloud_of(replies),
    }
    out: dict = {}
    with _Clock() as clock:
        for name, fixed in pairings.items():
            asked = refused = 0
            examples: list[str] = []
            for request, own in pairs:
                reply = fixed if fixed is not None else own
                if not reply:
                    continue
                for scenario in ("on air", "just after"):
                    clock.now = 1_000_000.0
                    probe = _Timeline(reply, clock.now)
                    sentence_s = len(request.split()) / _WORDS_PER_S
                    began = (probe.began + 0.6 * (probe.ends - probe.began)
                             if scenario == "on air" else probe.ends + 2.0)
                    arrives = began + sentence_s + _PIPELINE_S
                    clock.now = arrives
                    gate = _gate_after(reply, probe, arrives)
                    asked += 1
                    if not _ask(gate, request, opened_by="follow_up", began_at=began):
                        refused += 1
                        examples.append(request[:60])
            out[name] = (asked, refused, examples)
    return out


def _mangled(form: str) -> list[str]:
    """What speech recognition makes of a speaker bleeding into a microphone: a word dropped, two dropped, one replaced."""
    # WORDS, not whitespace tokens: a dash or a "+" standing alone is a token
    # and not a word, and "- no window is running" has four words to mangle,
    # not five. The first version split on whitespace and 71 of its "leaks"
    # were sentences with a dash in them whose one replaced word left 3 of 4.
    words = _WORD.findall(form)
    out = []
    # Only variants of four words or more: below that the leaky rule is not
    # applied by design (three words are echo only as an exact run), so a
    # three-word remnant measures the floor and not the rule.
    for variant in (words[:1] + words[2:],
                    words[:1] + words[2:3] + words[4:],
                    words[:2] + ["uh"] + words[3:]):
        if len(words) >= 5 and len(variant) >= 4:
            out.append(" ".join(variant))
    return out


def echoes_of_what_is_on_air(rows: list[dict]) -> dict:
    """
    HOW MANY ECHOES DOES THE GATE TAKE FOR HIM? Every reply in the log is put on
    a timeline and what is on air at five moments of it - 15%, 50% and 85% of the
    way through, and 1s and 2.5s after it stops - comes back through the
    microphone: the sentence that is playing, that and the next one, its last
    five words, each whole and with a word dropped, two dropped, one replaced.
    Forms of fewer than three words are skipped (below the floor nothing is
    echo, by design).

      doors    only the forms a door would admit (the rest are refused for lacking
               his name whatever the echo test says), in the follow-up window
      answer   the forms of a QUESTION, in the answer window that question opens
               (after it), or - while it is still on air - in barge-in with
               the previous question's window open

    {"doors": [asked, taken], "answer": [asked, taken], "named": n, "leaked": [...]}
    `named` counts door forms left out because they start with his name.
    """
    from jarvis.app import Expectation
    from jarvis.brain import router
    from jarvis.brain.router import solicits_an_answer
    from jarvis.config import CONFIG

    answer_s = float(CONFIG.get_path("conversation.answer_window_s", 30))
    replies = [r["summary"] for r in rows
               if r["kind"] == "utterance" and _details(r).get("who") != "user"]
    out = {"doors": [0, 0], "answer": [0, 0], "named": 0, "leaked": []}
    with _Clock() as clock:
        for reply in replies:
            clock.now = 1_000_000.0
            probe = _Timeline(reply, clock.now)
            if not probe.sentences:
                continue
            span = probe.ends - probe.began
            for point in (0.15 * span, 0.5 * span, 0.85 * span, span + 1.0, span + 2.5):
                moment = probe.began + point
                # The sentence playing at `moment` (the last one that had begun), and the next.
                playing = max((i for i, s in enumerate(probe.sentences) if s[1] <= moment),
                              default=0)
                heard = probe.sentences[playing:playing + 2]
                texts = [heard[0][0], " ".join(s[0] for s in heard)]
                texts.append(" ".join(_WORD.findall(texts[0])[-5:]))
                forms = []
                for text in texts:
                    forms.append(text)
                    forms.extend(_mangled(text))
                for form in forms:
                    spoken_words = len(_WORD.findall(form))
                    if spoken_words < 3:
                        continue
                    arrives = moment + spoken_words / _WORDS_PER_S + _PIPELINE_S
                    clock.now = arrives
                    if router.widened_door(form, "follow_up") is not None:
                        if router.addressed_to_jalen(form):
                            # Starts with his name, so the gate never echo-tests
                            # it: the design premise is that Jalen never begins a
                            # sentence with it, and 2 real replies ("Hey, Jarvis,
                            # not Joris...") do. Known, owner-accepted, counted.
                            out["named"] += 1
                            continue
                        gate = _gate_after(reply, probe, arrives)
                        out["doors"][0] += 1
                        if _ask(gate, form, opened_by="follow_up", began_at=moment):
                            out["doors"][1] += 1
                            out["leaked"].append(("door", round(point, 1), form[:60]))
                    if solicits_an_answer(reply):
                        gate = _gate_after(reply, probe, arrives)
                        if moment >= probe.ends:
                            # The window the question opened, after it.
                            gate._expecting = Expectation(
                                question=reply, turn_id=0, opened_at=probe.ends,
                                expires_at=probe.ends + answer_s)
                            window = "answer"
                        else:
                            # Still on air: only barge-in opens a window now,
                            # and the open one is the PREVIOUS question's.
                            gate._expecting = Expectation(
                                question="Which one did you mean?", turn_id=0,
                                opened_at=probe.began - 10.0, expires_at=probe.began + 20.0)
                            window = "barge"
                        out["answer"][0] += 1
                        vouched = gate._expecting if window == "answer" else None
                        if _ask(gate, form, opened_by=window, vouched_by=vouched, began_at=moment):
                            out["answer"][1] += 1
                            out["leaked"].append((window, round(point, 1), form[:60]))
    return out


def how_soon_real_answers_began(rows: list[dict]) -> dict:
    """
    THE COST SIDE OF JUDGING BY WHEN THE SOUND BEGAN. A real answer that begins
    within ECHO_TAIL_S of the end of the question, and that repeats three or
    more of its words in order (or four-fifths of a four-word answer's
    vocabulary), is now taken for the question coming back. How many of his
    real answers are in that position?

    For every sentence of his that follows a question of Jalen's with nothing
    said in between, within the answer window: when it began (written time
    minus its length at 2.6 words a second NOT MEASURED, the 1.4s endpoint and
    the 1.8s median transcription) after the reply ended, and what the gate
    does with it when it is asked with that onset.

    {"answers": n, "within_tail": n, "refused": n, "gaps": sorted list}
    """
    from jarvis.app import ECHO_TAIL_S, Expectation
    from jarvis.brain.router import solicits_an_answer
    from jarvis.config import CONFIG

    Gate = _make_gate_class()
    answer_s = float(CONFIG.get_path("conversation.answer_window_s", 30))
    reply_end_of: dict[int, float] = {}
    for i, row in enumerate(rows):
        if row["kind"] == "utterance" and _details(row).get("who") != "user":
            end = _epoch(row)
            for j in range(i + 1, min(i + 8, len(rows))):
                nxt = rows[j]
                if nxt["session_id"] != row["session_id"] or nxt["kind"] == "utterance":
                    break
                if nxt["summary"].startswith("timing"):
                    end = max(end, _epoch(nxt))
                    break
            reply_end_of[i] = end
    out = {"answers": 0, "within_tail": 0, "refused": 0, "gaps": [], "refused_examples": []}
    open_question: dict[str, tuple[str, float]] = {}
    with _Clock() as clock:
        for i, row in enumerate(rows):
            if row["kind"] != "utterance":
                continue
            sid = row["session_id"]
            if _details(row).get("who") != "user":
                if solicits_an_answer(row["summary"]):
                    open_question[sid] = (row["summary"], reply_end_of.get(i, _epoch(row)))
                else:
                    open_question.pop(sid, None)
                continue
            asked = open_question.pop(sid, None)
            if asked is None:
                continue
            question, ended = asked
            written = _epoch(row)
            began = _started_at(written, row["summary"], _WORDS_PER_S)
            gap = began - ended
            if not 0.0 <= gap < answer_s:
                continue
            out["answers"] += 1
            out["gaps"].append(round(gap, 1))
            if gap <= ECHO_TAIL_S:
                out["within_tail"] += 1
            clock.now = written
            gate = Gate()
            gate.speaker = _Voice(ended_at=ended)
            gate._last_reply_text = question
            gate._last_reply_at = ended
            gate._expecting = Expectation(question=question, turn_id=0, opened_at=ended,
                                          expires_at=ended + answer_s)
            if not _ask(gate, row["summary"], opened_by="answer", vouched_by=gate._expecting,
                        began_at=began):
                out["refused"] += 1
                out["refused_examples"].append((round(gap, 1), row["summary"][:60]))
    out["gaps"].sort()
    return out


DOORS = ("misheard-name", "greeting-last", "polite-request")


def door_counts(rows: list[dict], results: list[dict]) -> dict:
    """
    PER DOOR: what it rescues, and what it lets in that it should not.

    For each door the replay is run again with ONLY that door switched off.
    `alone` is the sentences meant for him that are acted on with every door
    and refused without this one: what it is worth that nothing else is worth.
    `false` is the same for the ambiguous and background rows: what it lets in
    that nothing else would. `matches` is every labelled row the door's shape
    matches in the window it was estimated to arrive in, however it was then
    acted on. All three come from the same 105 rows, so `false` cannot say more
    than "none of THESE 27" - the regexes were written while reading them.
    Speech between two other people is not in the log at all.

    {door: {"matches": Counter, "alone": [row numbers], "false": Counter}, ...,
     "no door at all": Counter of what the windows alone admit}
    """
    from jarvis import app
    from jarvis.brain import router

    if not hasattr(router, "all_doors"):
        return {}

    def accepted_of(res: list[dict]) -> dict[int, str]:
        return {r["n"]: r["label"] for r in res if r["accepted"]}

    real = app.widened_door
    out: dict = {}
    try:
        everything = accepted_of(results)
        for door in DOORS:
            def without(text, opened_by="", _skip=door):
                rest = [d for d in router.all_doors(text, opened_by) if d != _skip]
                return rest[0] if rest else None
            app.widened_door = without
            fewer = accepted_of(replay(rows))
            out[door] = {
                "matches": Counter(r["label"] for r in results
                                   if door in router.all_doors(r["text"], r["opened_by"])),
                "alone": [n for n, label in everything.items() if n not in fewer and label == "F"],
                "false": Counter(label for n, label in everything.items()
                                 if n not in fewer and label in "AB"),
            }
        app.widened_door = lambda text, opened_by="": None
        out["no door at all"] = Counter(accepted_of(replay(rows)).values())
    finally:
        app.widened_door = real
    return out


def door_table(rows: list[dict], results: list[dict]) -> list[str]:
    """door_counts, as lines for the report."""
    counts = door_counts(rows, results)
    if not counts:
        return []
    lines = [f"{'door':15s} {'matches F/A/B':>14s} {'alone':>6s} {'false A/B':>10s}"]
    for door in DOORS:
        c = counts[door]
        m = c["matches"]
        lines.append(f"{door:15s} {m['F']:>4d}/{m['A']}/{m['B']:<8d} "
                     f"{len(c['alone']):>6d} {c['false']['A']:>5d}/{c['false']['B']:<4d}  "
                     f"rows alone: {c['alone']}")
    none = counts["no door at all"]
    lines.append(f"{'no door at all':15s} {'':>14s} for him {none['F']}, A {none['A']}, B {none['B']} "
                 f"acted on (what the windows alone admit)")
    return lines


def door_report(rows: list[dict], results: list[dict]) -> list[str]:
    """
    How broad is each narrow door, on text that is NOT the gate's own corpus?

    The replay above can only say "0 new background rows" about 12 background
    rows. This asks the doors about everything else in the log: all 872
    utterances of his that were acted on, and all 823 sentences Jalen has
    said - written English, which is what a television sounds like to a
    transcript - fed in as if they had come back after the echo window.
    """
    from jarvis.brain import router

    users = [r["summary"] for r in rows
             if r["kind"] == "utterance" and _details(r).get("who") == "user"]
    ignored = [_details(r).get("heard", "") for r in rows if r["summary"].startswith("ignored")]
    replies = [r["summary"] for r in rows
               if r["kind"] == "utterance" and _details(r).get("who") != "user"]
    meant = [text for n, text in enumerate(ignored, 1) if LABELS.get(n, ("?",))[0] == "F"]
    other = [text for n, text in enumerate(ignored, 1) if LABELS.get(n, ("?",))[0] in "AB"]

    seen: dict[str, int] = {}
    for text in users + ignored:
        found = router._VOCATIVE_AT_THE_START.match(text)
        if found and found.group(1).lower() in router.MISHEARD_NAMES \
                and not router.addressed_to_jalen(text):
            seen[found.group(1).lower()] = seen.get(found.group(1).lower(), 0) + 1
    out = [f"names: {len(seen)} of the {len(router.MISHEARD_NAMES)} listed names, "
           f"{sum(seen.values())} sentences, none already addressed by his name"]
    out.append(f"polite request: opens {sum(router.opens_with_a_request(t) for t in users)} of "
               f"{len(users)} of his utterances and {sum(router.opens_with_a_request(t) for t in meant)} "
               f"of the {len(meant)} refused ones meant for him")
    out.append("'hey Jalen' not first: "
               f"{sum(router.greets_him_mid_sentence(t) and not router.addressed_to_jalen(t) for t in meant)}"
               f" of the {len(meant)} refused ones meant for him")

    def any_door(text: str) -> bool:
        return router.widened_address(text, "follow_up")

    # Each door on the 27 rows that were NOT for him, in the worst window.
    per_door = Counter()
    for text in other:
        door = getattr(router, "widened_door", lambda t, o: None)(text, "follow_up")
        if door:
            per_door[door] += 1
    out.append(f"the {len(other)} background or ambiguous rows any door admits, by door: "
               + (", ".join(f"{k} {v}" for k, v in per_door.items()) or "none"))

    sentence = re.compile(r"(?<=[.!?])\s+")
    admitted = sum(1 for reply in replies if any(any_door(p) for p in {reply, *sentence.split(reply)}))
    out.append(f"his own sentences any door's SHAPE matches, before any echo test: {admitted} of {len(replies)}")
    return out


def sensitivity(rows: list[dict]) -> str:
    """How much does the guess at his speaking rate move the answer?"""
    parts = []
    for rate in (2.0, _WORDS_PER_S, 3.4):
        res = replay(rows, words_per_s=rate)
        parts.append(f"{rate:g} w/s: for him {sum(r['accepted'] for r in res if r['label'] == 'F')}, "
                     f"ambiguous {sum(r['accepted'] for r in res if r['label'] == 'A')}, "
                     f"background {sum(r['accepted'] for r in res if r['label'] == 'B')}")
    return "acted on at his speaking rate of " + "; ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--log", type=Path, default=None,
                        help="default: $JALEN_AUDIT_LOG, else data/audit.jsonl")
    parser.add_argument("--show", action="store_true", help="list every row and its verdict")
    args = parser.parse_args()

    log = audit_log_path(args.log)
    if not log.exists():
        print(f"no audit log at {log} - pass --log")
        return 2
    rows = load(log)
    results = replay(rows)
    if len(results) != len(LABELS):
        print(f"NOTE: the log has {len(results)} ignored rows; LABELS covers the first "
              f"{len(LABELS)}. Rows past that are counted as '?'.")

    by_label = Counter(r["label"] for r in results)
    print(f"logged 'ignored - not addressed to Jalen' rows: {len(results)}  "
          f"(for him {by_label['F']}, ambiguous {by_label['A']}, background {by_label['B']})")

    acted = [r for r in results if r["accepted"]]
    dropped = [r for r in results if not r["accepted"]]
    print(f"the current gate would ACT ON {len(acted)} of them and still ignore {len(dropped)}")
    print(f"  acted on:       for him {sum(r['label'] == 'F' for r in acted)}, "
          f"ambiguous {sum(r['label'] == 'A' for r in acted)}, "
          f"background {sum(r['label'] == 'B' for r in acted)}   <- false accepts (strict / worst case "
          f"{sum(r['label'] == 'B' for r in acted)} / {sum(r['label'] in 'AB' for r in acted)})")
    print(f"  still ignored:  for him {sum(r['label'] == 'F' for r in dropped)}, "
          f"ambiguous {sum(r['label'] == 'A' for r in dropped)}, "
          f"background {sum(r['label'] == 'B' for r in dropped)}")
    causes = Counter(r["cause"] for r in dropped if r["label"] == "F")
    print("  still ignored and meant for him, by cause: "
          + ", ".join(f"{c} {n}" for c, n in causes.most_common()))
    rescued = [r for r in acted if r["rescued"]]
    print(f"  of the acted-on ones, {len(rescued)} came in ONLY because the question that was open "
          f"when he BEGAN speaking is honoured after it closed: for him "
          f"{sum(r['label'] == 'F' for r in rescued)}, ambiguous "
          f"{sum(r['label'] == 'A' for r in rescued)}, background "
          f"{sum(r['label'] == 'B' for r in rescued)}")

    era = [r for r in results if r["ts"] >= ANSWER_WINDOW_SHIPPED]
    if era and len(era) < len(results):
        e_label = Counter(r["label"] for r in era)
        e_acted = [r for r in era if r["accepted"]]
        print(f"the current era only (since the answer window shipped on 20 Sep): {len(era)} rows "
              f"(for him {e_label['F']}, ambiguous {e_label['A']}, background {e_label['B']}) - "
              f"acted on now: for him {sum(r['label'] == 'F' for r in e_acted)}, "
              f"ambiguous {sum(r['label'] == 'A' for r in e_acted)}, "
              f"background {sum(r['label'] == 'B' for r in e_acted)}")

    worst = replay(rows, force_opened_by="follow_up")
    print("worst case, every row treated as if it came out of the follow-up window: "
          f"for him {sum(r['accepted'] for r in worst if r['label'] == 'F')}, "
          f"ambiguous {sum(r['accepted'] for r in worst if r['label'] == 'A')}, "
          f"background {sum(r['accepted'] for r in worst if r['label'] == 'B')} acted on")
    print(sensitivity(rows))

    replies, tried, accepted = echo_corpus(rows)
    print(f"echo: {replies} Jalen replies, {tried} forms fed back at once, "
          f"{accepted} acted on as if he had said them")
    from jarvis.brain import router

    # The doors arrived with this report. Checked out at an older commit - to
    # reproduce the "before" figures in the commit message - the gate has no
    # `opened_by` and no doors, and this section is simply skipped.
    if hasattr(router, "widened_address"):
        for line in door_report(rows, results):
            print("doors: " + line)
        for line in door_table(rows, results):
            print("per door: " + line)
        spoken = echo_while_speaking(rows)
        print(f"echo while he speaks and after: {spoken['replies']} replies ({spoken['long']} take 12s or more "
              f"to say), every form a door would admit fed back at 5s, 10s, half way, and 2s and 8s after "
              f"it stops: {spoken['tried']} asked ({spoken['on_air']} while he was still on air), "
              f"{spoken['admitted']} acted on")
        for began_s, gate_s in ((-1.0, 3.5), (0.5, 3.7), (0.5, 6.0), (0.5, 11.0), (2.5, 6.0),
                                (3.5, 6.7), (6.0, 9.2)):
            asked, took = answer_path_echo(rows, began_s, gate_s)
            where = (f"began {-began_s:g}s before he stopped (still on air)" if began_s < 0
                     else f"began {began_s:g}s after he stopped")
            print(f"answer path: a question's own words, {where}, reached the gate "
                  f"{gate_s:g}s after he stopped, window open: {took} of {asked} taken for an answer")
        late = how_soon_real_answers_began(rows)
        if late["answers"]:
            gaps = late["gaps"]
            print(f"real answers: {late['answers']} sentences of his that followed a question of "
                  f"Jalen's within the answer window; by the timing estimate {late['within_tail']} began "
                  f"within the 3s tail (median gap {gaps[len(gaps) // 2]:g}s); the gate refuses "
                  f"{late['refused']} of them as his own voice"
                  + (f": {late['refused_examples']}" if late["refused_examples"] else ""))
        for name, (asked, refused, examples) in requests_against_replies(rows).items():
            print(f"real requests vs a reply in the room ({name}): {refused} of {asked} refused"
                  + (f" - e.g. {examples[:3]}" if examples else ""))
        taken = echoes_of_what_is_on_air(rows)
        print(f"echoes of what is on air, whole and mangled: doors {taken['doors'][1]} of "
              f"{taken['doors'][0]} taken for him, answer path {taken['answer'][1]} of "
              f"{taken['answer'][0]}" + (f" - e.g. {taken['leaked'][:3]}" if taken["leaked"] else "")
              + f" ({taken['named']} door forms begin with his name and are never echo-tested)")

    if args.show:
        for r in results:
            print(f"#{r['n']:03d} {'ACT' if r['accepted'] else '---'} {r['label']} {r['cause']:5s} "
                  f"{r['opened_by'] or '-':9s} {'V' if r['vouched'] else ' '} "
                  f"{r['text'][:80]!r}".encode("ascii", "replace").decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
