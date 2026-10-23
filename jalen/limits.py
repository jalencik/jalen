"""
Limits: how much Jalen may DO, and who can lift them. Him, and only him.

WHY THIS EXISTS
---------------
The safety gate (safety.py) answers "is this KIND of thing allowed?". Nothing
asked "...and how MANY of them?". Searched 2026-10-23 (grep for per_hour,
rate_limit, max_per, quota, cooldown, budget under jalen/): the only caps in
the project are the brain's 30-turn runaway guard and a coding job's one-hour
timeout. So a looping model, a Telegram flood or a microphone that heard its
own voice could send fifty messages, close every window or start a dozen
coding agents, each one individually "allowed", with nothing anywhere
noticing the total. His words: "there should be limits on using, I should be
the final commander".

WHAT IT DOES
------------
  * RULES. "No more than N of these tools per window." Counted when an action
    is allowed to run, in a sliding window, and kept on disk so a restart (or
    a model calling jalen_restart) does not hand the budget back.
  * A LEASH. No more than N ACTING calls per instruction of his. Acting is
    the set the injection guard already curated (safety.acts_on_the_world):
    everything AMBER or RED plus the GREEN tools that type, click, launch,
    write or remember. Reads do not count. He speaking again starts a new
    instruction and resets it.
  * BRAIN TURNS. The same shape, for how often the model is asked to think.
  * LOCKDOWN. "Hands off": every acting tool is refused until he lifts it.
    Reads still work, so he can ask what is going on.

WHO CAN LIFT A LIMIT
--------------------
At a cap the action is not silently refused and not silently allowed: HE is
asked, through Jalen.confirm() - the same question, echo defence and timeout
a RED action gets - and his yes lets THAT ONE action through. It does not
raise the cap: the next over-cap action asks again. No tool the model can
call raises a limit, ends a lockdown or changes this file; the lockdown is
lifted only by a router rule that matched his own words (app.py checks
`his_own_words`), and a cap is raised only by editing config/safety.yaml, in
daylight, like the never-touch list. A "no" or a timeout stops further asks
about that limit until he speaks again (new_instruction()), so a model that
retries cannot turn the limit into a stream of prompts.

`on_limit: refuse` skips the question: the action is refused and only the
file can lift it.

THE FILE IS THE POLICY; THIS IS THE MECHANISM
---------------------------------------------
Numbers live in config/safety.yaml under `limits:` with the measurement each
one rests on. This module reads them once, in from_config(), never mid-call.
A rule that cannot be read is NOT silently dropped: it is listed in
`problems`, which Jalen logs and says at startup, and the shipped file is
tested to have none.

FAILURE DIRECTIONS
------------------
  * The lockdown is a FILE (LOCK_PATH): present means locked, whatever is in
    it. A half-written or corrupt file is still present, so it fails closed.
    The counts are a separate file, because a corrupt counts file may be
    thrown away (the cost is a fresh budget) while a lockdown may not.
  * Neither file can be written: the limits still hold for the session in
    memory, and `on_error` is told, so it is logged rather than silent.
  * A limit is a RATE, not a seal. Counting happens when an action is
    allowed to run, so parallel calls in one model message can overshoot a
    cap by the number of parallel calls. The gate in front of it is the seal.

Pure standard library, no imports from safety.py (which owns an instance), so
there is no cycle: the engine hands in `acting`, and a verdict is read by
duck typing (.tool, .summary).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
# Distinctive names on purpose, and both are on safety.yaml's never_touch
# patterns: a tool that could move or delete these could hand a budget back or
# lift a lockdown, and "nothing the model can call lifts a limit" would be a
# claim about the tools' names rather than about what they can reach. A name
# like limits.json would also have refused every project of his that has one.
STATE_PATH = ROOT / "data" / "jalen-usage.json"
LOCK_PATH = ROOT / "data" / "jalen-lockdown.json"

BRAIN_TURNS = "brain_turns"     # the key brain turns are counted under
LEASH = "leash"                 # ...and the leash's key in the declined set

# Counted events are pruned to their window on every read, so the file stays
# as small as the busiest rule. This only bounds a rule whose `max` is huge.
_MAX_KEPT = 5000

_ASK = "ask"
_REFUSE = "refuse"


@dataclass(frozen=True)
class Rule:
    name: str
    tools: frozenset
    max: int
    window_s: float
    on_limit: str = _ASK
    noun: str = ""

    @property
    def label(self) -> str:
        return self.noun or self.name


@dataclass(frozen=True)
class Admission:
    """
    The governor's answer for one action.

    `why` is a sentence he can hear (the router path speaks it); `for_model`
    is what the model is told when the hook denies the call. They are two
    texts because the model must not be left thinking a limit it hit was a
    refusal he never made - the mistake _deny_reason was written to stop.
    """

    allowed: bool = True
    outcome: str = "ok"         # ok | locked | refused | declined | timeout
    why: str = ""
    for_model: str = ""
    asked: bool = False


OK = Admission()


@dataclass
class _Hit:
    key: str                    # rule name, LEASH or BRAIN_TURNS
    label: str
    count: int
    max: int
    window_s: float | None      # None for the leash: it has no clock
    on_limit: str
    kind: str = "rule"          # rule | leash | brain


def _span(seconds: float | None) -> str:
    """3600 -> "hour", 86400 -> "day", 600 -> "10 minutes"."""
    if seconds is None:
        return "request"
    s = int(round(seconds))
    if s == 3600:
        return "hour"
    if s == 86400:
        return "day"
    if s == 60:
        return "minute"
    if s % 3600 == 0:
        return f"{s // 3600} hours"
    if s % 60 == 0:
        return f"{s // 60} minutes"
    return f"{s} seconds"


def _number(value: Any, minimum: float, what: str, problems: list[str],
            integer: bool = True):
    """A config number, or None after recording why it was not one."""
    if isinstance(value, bool):
        problems.append(f"{what} is {value!r}, not a number")
        return None
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        problems.append(f"{what} is {value!r}, not a number")
        return None
    if number < minimum:
        problems.append(f"{what} is {number}, below the minimum of {minimum:g}")
        return None
    return number


def _parse_rule(raw: Any, index: int, seen: set, problems: list[str]) -> Rule | None:
    where = f"limits.rules[{index}]"
    if not isinstance(raw, dict):
        problems.append(f"{where} is not a mapping")
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        problems.append(f"{where} has no name")
        return None
    where = f"limits rule '{name}'"
    if name in seen or name in (LEASH, BRAIN_TURNS):
        problems.append(f"{where} repeats a name already in use")
        return None
    tools = raw.get("tools")
    if (not isinstance(tools, (list, tuple)) or not tools
            or not all(isinstance(t, str) and t.strip() for t in tools)):
        problems.append(f"{where} needs a non-empty list of tool names")
        return None
    cap = _number(raw.get("max"), 0, f"{where}: max", problems)
    window = _number(raw.get("window_s"), 1, f"{where}: window_s", problems, integer=False)
    on_limit = str(raw.get("on_limit", _ASK)).strip().lower()
    if on_limit not in (_ASK, _REFUSE):
        problems.append(f"{where}: on_limit is {on_limit!r}, not 'ask' or 'refuse'")
        return None
    if cap is None or window is None:
        return None
    seen.add(name)
    return Rule(name=name, tools=frozenset(t.strip() for t in tools), max=cap,
                window_s=float(window), on_limit=on_limit,
                noun=str(raw.get("noun") or "").strip())


class Governor:
    def __init__(
        self,
        rules: Iterable[Rule] = (),
        *,
        brain_turns: Rule | None = None,
        leash: int = 0,
        enabled: bool = True,
        acting: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.time,
        problems: Iterable[str] = (),
        still_allowed: Iterable[str] = (),
    ) -> None:
        self.enabled = enabled
        self.problems: list[str] = list(problems)
        # Acting tools that run even in lockdown because all they do is STOP
        # work (cancel_task). "Hands off" followed by "cancel the job" must
        # not be answered "I'm in lockdown".
        self._still_allowed = frozenset(still_allowed)
        self._rules = tuple(rules)
        self._by_tool: dict[str, list[Rule]] = {}
        for rule in self._rules:
            for tool in rule.tools:
                self._by_tool.setdefault(tool, []).append(rule)
        self._brain = brain_turns
        self._leash_max = int(leash)
        self._acting = acting or (lambda _tool: True)
        self._clock = clock
        self._events: dict[str, list[float]] = {}
        self._since_instruction = 0
        self._declined: set[str] = set()
        self._locked: dict | None = None
        self._state_path: Path | None = None
        self._lock_path: Path | None = None
        self._on_error: Callable[[BaseException], None] | None = None
        self._mutex = threading.RLock()
        # What the last failed write was, for anyone who asks (tests, check).
        self.last_error: str = ""

    # ------------------------------------------------------------------ build
    @classmethod
    def from_config(cls, block: Any, *, acting: Callable[[str], bool] | None = None,
                    clock: Callable[[], float] = time.time) -> "Governor":
        """
        Build from the `limits:` mapping of safety.yaml. Never raises: a block
        that cannot be read gives a Governor with no rules and the reason in
        `problems`, because the alternative - an exception at startup - would
        keep Jalen from starting over a typo in a file he edits by hand.
        """
        problems: list[str] = []
        if block is None:
            return cls(enabled=False, acting=acting, clock=clock)
        if not isinstance(block, dict):
            return cls(enabled=False, acting=acting, clock=clock,
                       problems=["limits is not a mapping, so no limits are in force"])
        rules: list[Rule] = []
        seen: set = set()
        raw_rules = block.get("rules") or []
        if not isinstance(raw_rules, list):
            problems.append("limits.rules is not a list")
            raw_rules = []
        for index, raw in enumerate(raw_rules):
            rule = _parse_rule(raw, index, seen, problems)
            if rule is not None:
                rules.append(rule)

        brain = None
        raw_brain = block.get("brain_turns")
        if isinstance(raw_brain, dict):
            candidate = _parse_rule(
                {**raw_brain, "name": "brain turns", "tools": ["(brain)"]},
                0, set(), problems)
            if candidate is not None:
                brain = Rule(name=BRAIN_TURNS, tools=frozenset(), max=candidate.max,
                             window_s=candidate.window_s, on_limit=candidate.on_limit,
                             noun="thinking turns")
        elif raw_brain is not None:
            problems.append("limits.brain_turns is not a mapping")

        leash = 0
        raw_leash = block.get("leash")
        if isinstance(raw_leash, dict) and raw_leash.get("max_actions") is not None:
            leash = _number(raw_leash.get("max_actions"), 1,
                            "limits.leash.max_actions", problems) or 0
        elif raw_leash is not None and not isinstance(raw_leash, dict):
            problems.append("limits.leash is not a mapping")

        still_allowed: list[str] = []
        raw_lock = block.get("lockdown")
        if isinstance(raw_lock, dict):
            listed = raw_lock.get("still_allowed") or []
            if (isinstance(listed, list)
                    and all(isinstance(t, str) and t.strip() for t in listed)):
                still_allowed = [t.strip() for t in listed]
            else:
                problems.append("limits.lockdown.still_allowed is not a list of tool names")
        elif raw_lock is not None:
            problems.append("limits.lockdown is not a mapping")

        return cls(rules, brain_turns=brain, leash=leash,
                   enabled=bool(block.get("enabled", True)),
                   acting=acting, clock=clock, problems=problems,
                   still_allowed=still_allowed)

    # ---------------------------------------------------------------- storage
    def attach(self, state_path: Path | None = None, lock_path: Path | None = None,
               on_error: Callable[[BaseException], None] | None = None) -> None:
        """
        Start remembering: load what earlier runs counted and whether he left
        a lockdown on. Called once by Jalen.__init__ and never by the engine,
        so building a SafetyEngine (a test, a script) touches no file.
        """
        with self._mutex:
            self._state_path = Path(state_path) if state_path else Path(STATE_PATH)
            self._lock_path = Path(lock_path) if lock_path else Path(LOCK_PATH)
            self._on_error = on_error
            self._load_counts()
            self._load_lock()

    def _fail(self, exc: BaseException) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        if self._on_error is not None:
            try:
                self._on_error(exc)
            except Exception:  # noqa: BLE001 - reporting must not break the gate
                pass

    def _load_counts(self) -> None:
        path = self._state_path
        if path is None or not path.exists():
            return
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            events = blob.get("events") if isinstance(blob, dict) else None
            if not isinstance(events, dict):
                raise ValueError("no 'events' mapping")
        except (OSError, ValueError) as exc:
            # A budget that cannot be read is a fresh budget; set the broken
            # file aside so it can be looked at instead of overwritten.
            self._fail(exc)
            try:
                path.replace(path.with_name(path.name + f".corrupt-{int(self._clock())}"))
            except OSError:
                pass
            return
        for key, stamps in events.items():
            if isinstance(stamps, list):
                self._events[str(key)] = [
                    float(t) for t in stamps
                    if isinstance(t, (int, float)) and not isinstance(t, bool)
                ][-_MAX_KEPT:]

    def _load_lock(self) -> None:
        path = self._lock_path
        if path is None or not path.exists():
            return
        # PRESENT MEANS LOCKED. Whatever is inside is only the explanation.
        info: dict = {}
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(blob, dict):
                info = blob
        except (OSError, ValueError):
            info = {"reason": "the lockdown record was unreadable, so I stayed locked"}
        info.setdefault("at", self._clock())
        self._locked = info

    def _save_counts(self) -> None:
        path = self._state_path
        if path is None:
            return
        # Each write has a temp name of its own: two Jalens (or a test beside
        # a live one) must not replace each other's half-written file.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"v": 1, "events": self._events}), encoding="utf-8")
            last: BaseException | None = None
            for _attempt in range(4):
                try:
                    os.replace(tmp, path)
                    last = None
                    break
                except PermissionError as exc:      # a reader holds it (Windows)
                    last = exc
                    time.sleep(0.05)
            if last is not None:
                raise last
        except OSError as exc:
            self._fail(exc)
            try:
                tmp.unlink()
            except OSError:
                pass

    # --------------------------------------------------------------- lockdown
    @property
    def locked(self) -> bool:
        return self._locked is not None

    def lockdown_info(self) -> dict:
        return dict(self._locked or {})

    def lock(self, reason: str = "") -> bool:
        """
        Hands off. Returns True when the lock is also on disk, False when it
        holds only for this run (then say so: a restart would forget it).
        """
        with self._mutex:
            info = {"at": self._clock(), "reason": reason}
            self._locked = info
            if self._lock_path is None:
                return True
            try:
                self._lock_path.parent.mkdir(parents=True, exist_ok=True)
                self._lock_path.write_text(json.dumps(info), encoding="utf-8")
                return True
            except OSError as exc:
                self._fail(exc)
                return False

    def unlock(self) -> bool:
        """
        End the lockdown. Called ONLY from app.py, for a router rule that
        matched his own words. Returns False when the record could not be
        removed, in which case it stays locked (and says so): a lock that
        cannot be proven lifted is still on.
        """
        with self._mutex:
            if self._lock_path is not None:
                try:
                    self._lock_path.unlink(missing_ok=True)
                except OSError as exc:
                    self._fail(exc)
                    return False
            self._locked = None
            self._declined.clear()
            return True

    # -------------------------------------------------------------- the count
    def new_instruction(self) -> None:
        """He spoke: a fresh request. The leash resets and old refusals are forgotten."""
        with self._mutex:
            self._since_instruction = 0
            self._declined.clear()

    def _count(self, key: str, window_s: float, now: float) -> int:
        stamps = self._events.get(key)
        if not stamps:
            return 0
        horizon = now - window_s
        kept = [t for t in stamps if t > horizon]
        if len(kept) != len(stamps):
            self._events[key] = kept
        return len(kept)

    def _hits(self, tool: str, acting: bool) -> list[_Hit]:
        now = self._clock()
        hits: list[_Hit] = []
        for rule in self._by_tool.get(tool, ()):
            used = self._count(rule.name, rule.window_s, now)
            if used >= rule.max:
                hits.append(_Hit(rule.name, rule.label, used, rule.max,
                                 rule.window_s, rule.on_limit))
        if acting and self._leash_max and self._since_instruction >= self._leash_max:
            hits.append(_Hit(LEASH, "actions", self._since_instruction,
                             self._leash_max, None, _ASK, kind=LEASH))
        hits.sort(key=lambda hit: hit.on_limit != _REFUSE)   # hard limits first
        return hits

    # --------------------------------------------------------------- the gate
    def _is_acting(self, tool: str) -> bool:
        """Acting unless proven otherwise: a tool nobody can classify is not read-only."""
        try:
            return bool(self._acting(tool))
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return True

    def _screen(self, tool: str) -> tuple[Admission | None, list[_Hit]]:
        """
        Everything that can be decided WITHOUT asking him.

        (answer, [])  decided: allowed, locked, refused, or already declined
        (None, hits)  over a cap he has not been asked about yet

        Fails CLOSED. A check that broke has not checked, and "I couldn't
        tell, so I did it" is the wrong way round for the one layer that
        exists to say no (the same rule as safety._touches_forbidden_path).
        The tier gate in front of it is unchanged either way.
        """
        try:
            with self._mutex:
                acting = self._is_acting(tool)
                if (self._locked is not None and acting
                        and tool not in self._still_allowed):
                    return self._locked_admission(), []
                if not self.enabled:
                    return OK, []
                hits = self._hits(tool, acting)
                for hit in hits:
                    if hit.on_limit == _REFUSE:
                        return self._refused(hit), []
                    if hit.key in self._declined:
                        return self._already_declined(hit), []
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return Admission(
                False, "refused",
                "My limits check broke, so I didn't do that one.",
                "Refused - the limits check failed internally, so nothing was checked. "
                "Do not retry; tell him it was not done and that the limits need looking at."
            ), []
        return (None, hits) if hits else (OK, [])

    def quick(self, verdict: Any) -> Admission | None:
        """
        The answer when no question is needed, else None. This is what the
        router path asks first: it runs on every command, and a hop to the
        event loop to learn "nothing is near a limit" would be a cost paid by
        every `what time is it` for a check that almost always says yes.
        """
        answer, _hits = self._screen(getattr(verdict, "tool", "") or "")
        return answer

    async def admit(self, verdict: Any, confirm: Callable[[str], Awaitable[Any]]) -> Admission:
        """
        May this action go ahead? Asks him when it is over a cap.

        Call it AFTER classify() (a BLACK verdict never gets here) and BEFORE
        the tier's own confirm or announce; call record() when the action is
        finally allowed to run. His yes lets this one action through and
        nothing else: see the module docstring.
        """
        tool = getattr(verdict, "tool", "") or ""
        answer, hits = self._screen(tool)
        if answer is not None:
            return answer
        summary = getattr(verdict, "summary", "") or tool
        for hit in hits:
            decision = await self._ask(hit, summary, confirm)
            if not decision.allowed:
                return decision
            if hit.kind == LEASH:
                with self._mutex:
                    self._since_instruction = 0     # his yes buys another batch
        return Admission(True, "ok", asked=True)

    async def admit_brain_turn(self, confirm: Callable[[str], Awaitable[Any]]) -> Admission:
        """The same question for 'may I think about this one?' - see handle_with_brain."""
        try:
            with self._mutex:
                if not self.enabled or self._brain is None:
                    return OK
                rule = self._brain
                used = self._count(BRAIN_TURNS, rule.window_s, self._clock())
                if used < rule.max:
                    return OK
                hit = _Hit(BRAIN_TURNS, rule.label, used, rule.max, rule.window_s,
                           rule.on_limit, kind="brain")
                if hit.on_limit == _REFUSE:
                    return self._refused(hit)
                if hit.key in self._declined:
                    return self._already_declined(hit)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return Admission(False, "refused",
                             "My limits check broke, so I didn't think about that one.",
                             "Refused - the limits check failed internally. Do not retry.")
        return await self._ask(hit, "", confirm)

    @staticmethod
    def _refused(hit: _Hit) -> Admission:
        span = _span(hit.window_s)
        return Admission(
            False, "refused",
            f"I won't: that is past your limit on {hit.label}, {hit.max} in the last "
            f"{span}, and it is set to refuse rather than ask.",
            f"Refused - it is past a hard limit he set ({hit.max} {hit.label} in the "
            f"last {span}). Do not retry. Tell him it was not done and that only "
            "config/safety.yaml raises it.")

    @staticmethod
    def _already_declined(hit: _Hit) -> Admission:
        return Admission(
            False, "declined",
            f"You already said no to going past the limit on {hit.label} for this request.",
            "He already said no to going past this limit. Do not retry or ask again; "
            "wait for him to say something new.")

    async def _ask(self, hit: _Hit, summary: str,
                   confirm: Callable[[str], Awaitable[Any]]) -> Admission:
        """Put the question to him. Only his own yes is a yes."""
        question = self._question(hit, summary)
        try:
            answer = await confirm(question)
        except Exception as exc:  # noqa: BLE001 - not being able to ask is a no
            self._fail(exc)
            answer = None
        if answer:
            return Admission(True, "ok", asked=True)

        with self._mutex:
            self._declined.add(hit.key)
        outcome = getattr(answer, "outcome", "no") if answer is not None else "no"
        if outcome == "timeout":
            return Admission(
                False, "timeout",
                f"No answer, so I didn't - it was past your limit on {hit.label}.",
                "He did not answer the limit question in time - he may not have heard it. "
                "Do not say he refused. Tell him it was not done because it was past a "
                "limit, and ask once if it still matters.",
                asked=True)
        if outcome == "correction":
            words = getattr(answer, "words", "") or ""
            return Admission(
                False, "declined",
                f"Cancelled - it was past your limit on {hit.label}.",
                f'He did not approve going past the limit. His words were: "{words}". If '
                "that is a correction, do what he said instead - it goes through the gate "
                "like anything else. Do not retry the original.",
                asked=True)
        return Admission(
            False, "declined",
            f"Cancelled - it was past your limit on {hit.label}.",
            "He said no to going past the limit. Don't retry; ask what he'd prefer.",
            asked=True)

    @staticmethod
    def _question(hit: _Hit, summary: str) -> str:
        span = _span(hit.window_s)
        if hit.kind == LEASH:
            return (f"I've taken {hit.count} actions on this one request, which is as many "
                    f"as you allow. Next: {summary}. Confirm?")
        if hit.kind == "brain":
            return (f"I've used {hit.count} thinking turns in the last {span}, and your "
                    f"limit is {hit.max}. Answer this one anyway? Confirm?")
        if hit.count > hit.max:
            where = (f"You're already past your limit on {hit.label}: {hit.count} in the "
                     f"last {span}, limit {hit.max}.")
        else:
            where = f"That's your limit on {hit.label}: {hit.max} in the last {span}."
        return f"{summary}. {where} Confirm?"

    def _locked_admission(self) -> Admission:
        return Admission(
            False, "locked",
            "I'm in lockdown, so I won't do that. Say end the lockdown when you want me "
            "acting again.",
            "Refused - he put you in lockdown (hands off) and only he can lift it. Do not "
            "retry and do not look for another way. Reading and answering still work; tell "
            "him nothing was done and that he can say 'end the lockdown'.")

    # --------------------------------------------------------------- counting
    def record(self, tool: str) -> None:
        """The action is going to run: count it against every rule that lists it."""
        with self._mutex:
            if self._is_acting(tool):
                self._since_instruction += 1
            if not self.enabled:
                return
            rules = self._by_tool.get(tool, ())
            if not rules:
                return
            now = self._clock()
            for rule in rules:
                self._count(rule.name, rule.window_s, now)      # prune first
                self._events.setdefault(rule.name, []).append(now)
                del self._events[rule.name][:-_MAX_KEPT]
            self._save_counts()

    def record_brain_turn(self) -> None:
        with self._mutex:
            if not self.enabled or self._brain is None:
                return
            now = self._clock()
            self._count(BRAIN_TURNS, self._brain.window_s, now)
            self._events.setdefault(BRAIN_TURNS, []).append(now)
            del self._events[BRAIN_TURNS][:-_MAX_KEPT]
            self._save_counts()

    # ----------------------------------------------------------------- report
    def usage(self) -> list[dict]:
        """Every counted limit and where it stands, for the report and for tests."""
        with self._mutex:
            now = self._clock()
            rows = []
            for rule in self._rules:
                rows.append({"name": rule.name, "label": rule.label,
                             "used": self._count(rule.name, rule.window_s, now),
                             "max": rule.max, "window_s": rule.window_s,
                             "on_limit": rule.on_limit})
            if self._brain is not None:
                rows.append({"name": BRAIN_TURNS, "label": self._brain.label,
                             "used": self._count(BRAIN_TURNS, self._brain.window_s, now),
                             "max": self._brain.max, "window_s": self._brain.window_s,
                             "on_limit": self._brain.on_limit})
            return rows

    def report(self) -> str:
        """What he hears for 'what are my limits' - spoken length, busiest first."""
        with self._mutex:
            parts: list[str] = []
            if self._locked is not None:
                parts.append("I'm in lockdown: hands off, reading only, until you lift it.")
            if not self.enabled:
                parts.append("The usage limits are switched off in safety.yaml.")
                return " ".join(parts)
            rows = self.usage()
            if not rows and not self._leash_max:
                parts.append("There are no usage limits set in safety.yaml.")
                return " ".join(parts)
            used = [r for r in rows if r["used"] > 0]
            used.sort(key=lambda r: (r["used"] / r["max"]) if r["max"] else 99, reverse=True)
            if used:
                shown = [f"{r['label']}: {r['used']} of {r['max']} in the last "
                         f"{_span(r['window_s'])}" for r in used[:4]]
                parts.append("Used so far, busiest first. " + "; ".join(shown) + ".")
                if len(used) > 4:
                    parts.append(f"{len(used) - 4} more are lower.")
            else:
                parts.append(f"I haven't used any of my {len(rows)} limits in their windows.")
            if self._leash_max:
                parts.append(f"I stop and ask after {self._leash_max} actions on one request.")
            return " ".join(parts)


# ---------------------------------------------------------------- the install
# The tool layer gets read access to the report without being handed the app:
# the same bridge shape as tools/interaction.py. usage_report reads it; nothing
# reachable from a tool can set a limit, because there is no setter here.
_CURRENT: Governor | None = None


def install(governor: Governor | None) -> None:
    global _CURRENT
    _CURRENT = governor


def current() -> Governor | None:
    return _CURRENT
