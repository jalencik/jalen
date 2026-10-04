"""
Do premium emoji, stickers and voice messages work on HIS real Telegram?

Everything built for them (jalen/tools/stickers.py, jalen/tools/messaging.py)
was tested against fakes made from Telethon's own types. His real account was
never touched, and the limits in stickers.py are labelled [NOT MEASURED] for
that reason. This script is the one real run, done by HIM, from the main Jalen
folder, with Jalen stopped:

    .venv\\Scripts\\python.exe scripts\\live_telegram_check.py
    .venv\\Scripts\\python.exe scripts\\live_telegram_check.py --no-send

  1. Look up three premium emoji from his post format, by character and by name.
  2. List his emoji sets and sticker packs: names and counts.
  3. Send ONE test post with a premium emoji to Saved Messages, and read it back.
  4. Send ONE short voice message to Saved Messages, and read it back.
  5. Print a table with the time of each step, to paste back.

--no-send stops after step 2: nothing is sent and nothing is asked.

THE RULES IT KEEPS
* One Telegram client at a time. It takes the same single-instance lock as
  run.py (jalen/runtime.py), so it refuses to start while Jalen runs, and
  Jalen refuses to start while it runs. Two clients on one session file can
  get the session thrown out. The lock does NOT cover `jalen.ps1 check`, and
  Jalen's stop/restart (hotkey, run.py --stop, jalen.ps1 restart) ends whatever
  holds the lock - so the intro asks him not to use those meanwhile, and a stop
  that arrives between steps ends the run cleanly.
* It runs only from the main Jalen folder. A work copy under .claude\\worktrees
  has its own data folder: no Telegram sign-in, and a lock Jalen never reads.
* Saved Messages only. The destination is a constant, there is no option for
  another one, and before each send the name has to resolve, the way the send
  resolves it, to his own account.
* It asks before each send. Only "y" or "yes" sends; anything else, an empty
  line included, skips that send.
* It never prints a message, a message id, an emoji id, his name, his handle or
  his phone number. The tool replies quote the post and name the account, so
  each one is reduced to what happened (sent / kept / dropped) before printing.
  Pack titles are printed because he asked for them, with control characters
  taken out, since strangers write them. Every step label is plain ASCII, so a
  table saved with `> file.txt` (cp1251 here) still says which emoji is which.
* Ctrl+C stops it at any point: the table so far is printed, the Telegram
  connection is closed and the lock is released. If a send had started, it
  says the message may have arrived. Ctrl+C during a Telegram step takes effect
  when that step returns (at most about a minute).

It calls the same tool functions Jalen calls, so it checks what Jalen does, not
a copy of it. Those functions do not ask for confirmation themselves (the
brain's safety hook does that); here the typed "yes" before each send is the
confirmation. The voice line is rendered by the same online text-to-speech
Jalen uses; no chat but his Saved Messages receives anything.

Exit codes: 0 every step did what it should, 1 something did not (or the main
question - does a premium emoji survive a post - could not be tested), 2 it
could not start or could not reach Telegram at all, 130 stopped.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The only place this script sends to. messaging._resolve turns this exact
# name into his own account (client.get_me()), never into a chat search.
DESTINATION = "Saved Messages"

# How run.py --status names this process while it holds the lock.
LOCK_MODE = "telegram-check"

# Three emoji from his post format, each looked up by its character and by a
# name Telegram files it under. Counted in docs/community_post_format.md on
# 2026-10-01: down-pointing hand 6, clap 5, pen 4, speaking head 4, rocket 4,
# pushpin 3. Rocket, clap and pushpin are the three with plain English names.
EMOJI = (("\U0001f680", "rocket"), ("\U0001f44f", "clap"), ("\U0001f4cc", "pin"))

VOICE_WORDS = "This is Jalen's voice check. You can delete this message."

_TAG = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)
_LONG_NUMBER = re.compile(r"\d{5,}")
_SETS_CAPPED = re.compile(r"Only (\d+) of (\d+) emoji sets were searched")
_SETS_FAILED = re.compile(r"(\d+) of his (\d+) emoji sets couldn't be read")
_SECTION = re.compile(r"^(Custom emoji sets|Sticker packs) \((\d+)\):$")
_PACK = re.compile(r"^- (.*) \((\d+) (emoji|stickers)\)$")
_ALL_KEPT = re.compile(r"Read it back: all \d+ premium emoji arrived")
_SOME_DROPPED = re.compile(r"Read it back: (?:only )?\d+ of \d+ premium emoji arrived")
_VOICE_LENGTH = re.compile(r", (\d+:\d\d(?::\d\d)?) long,")
# messaging.send_voice_message's two read-back sentences. The FAILURE one also
# contains "Telegram shows it as a voice message" ("...to check that Telegram
# shows it as..."), so it is matched first and the success one in full.
_VOICE_UNCHECKED = "couldn't read it back"
_VOICE_ARRIVED = "I read it back: Telegram shows it as a voice message."

# Outcomes that mean "this did not do what it should" - any of them makes the
# exit code 1. NOT TESTED is the post that could not run because no premium
# emoji was found: the main question went unanswered. SKIPPED (he said no, or
# --no-send) is his choice and is not a failure.
_BAD = frozenset({"FAILED", "NOT CONFIRMED", "NOTHING SENT", "WRONG CHAT", "DROPPED",
                  "NOT CHECKED", "WRONG KIND", "NO READ-BACK", "NOT TESTED"})


class _WrongChat(Exception):
    """A send would go, or reported going, somewhere other than Saved Messages."""


class _StopAsked(Exception):
    """Jalen's stop or restart was asked for while the check held the lock."""


def _clean(text: object, limit: int = 120) -> str:
    """
    A line that is safe to print: no premium-emoji tag, no long number (ids,
    phone numbers), no control or format characters (an escape sequence in a
    pack title would otherwise reach his terminal), one line, capped.
    """
    words = _TAG.sub("[premium emoji]", str(text or ""))
    words = _LONG_NUMBER.sub("#", words)
    words = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in words)
    words = " ".join(words.split())
    return words if len(words) <= limit else words[: limit - 3].rstrip() + "..."


def _ask(question: str) -> bool:
    try:
        answer = input(f"\n{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _in_a_worktree() -> bool:
    parts = [p.lower() for p in ROOT.parts]
    return any(a == ".claude" and b == "worktrees" for a, b in zip(parts, parts[1:]))


def _check_stop() -> None:
    from jalen import runtime

    if runtime.stop_requested():
        raise _StopAsked


@dataclass
class Row:
    step: str
    result: str
    seconds: "float | None"
    detail: str = ""


@dataclass
class Report:
    rows: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    sending: bool = False      # True from the moment a send tool is called until it returns

    def add(self, step: str, result: str, seconds: "float | None" = None,
            detail: str = "") -> Row:
        row = Row(step, result, seconds, _clean(detail))
        self.rows.append(row)
        took = f" ({seconds:.1f}s)" if seconds is not None else ""
        tail = f" - {row.detail}" if row.detail else ""
        print(f"  {result:<13} {step}{took}{tail}")
        return row

    def ok(self) -> bool:
        return not any(row.result in _BAD for row in self.rows)

    def table(self) -> str:
        width = max([len(r.step) for r in self.rows] + [4])
        lines = [f"{'STEP':<{width}}  {'RESULT':<13}  {'SECONDS':>7}  DETAIL"]
        for r in self.rows:
            took = f"{r.seconds:7.2f}" if r.seconds is not None else "      -"
            lines.append(f"{r.step:<{width}}  {r.result:<13}  {took}  {r.detail}")
        return "\n".join(lines)

    def paste_block(self) -> str:
        lines = ["--- paste this back to Jalen's builders ---",
                 f"run at: {datetime.now():%Y-%m-%d %H:%M}"]
        lines += [f"{key}: {value}" for key, value in self.facts.items()]
        lines += [f"{r.step}: {r.result}"
                  + (f" in {r.seconds:.2f}s" if r.seconds is not None else "")
                  + (f" ({r.detail})" if r.detail else "") for r in self.rows]
        lines.append("--- end ---")
        return "\n".join(lines)


# --------------------------------------------------------------------- steps
def check_account(report: Report) -> bool:
    """Signed in, and is the account Premium? Neither his name nor his handle is printed."""
    from jalen.tools import messaging

    started = time.perf_counter()
    status = messaging.telegram_status()
    took = time.perf_counter() - started
    if not status.startswith("Personal Telegram signed in"):
        # These sentences carry the fix and no account detail.
        report.add("signed in to Telegram", "FAILED", took, status)
        return False
    report.add("signed in to Telegram", "OK", took)
    try:
        me = messaging.RUNTIME.run(lambda client: client.get_me())
        premium = getattr(me, "premium", None)
    except Exception:  # noqa: BLE001 - a fact for the table, not worth stopping over
        premium = None
    report.facts["account has Telegram Premium"] = {True: "yes", False: "no"}.get(premium, "unknown")
    return True


def look_up_emoji(report: Report) -> str:
    """
    Step 1. Returns a premium-emoji tag to post with, or "". The tag is never
    printed: it carries the emoji's id.
    """
    from jalen.tools import stickers

    tag = ""
    found = {"character": 0, "name": 0}
    answered: list[float] = []       # times of lookups that got an answer, in order
    for char, name in EMOJI:
        code = f"U+{ord(char):04X}"
        for kind, query, label in (("character", char, f"premium {name} ({code}) by character"),
                                   ("name", name, f"premium emoji named '{name}'")):
            _check_stop()
            started = time.perf_counter()
            out = stickers.find_premium_emoji(query)
            took = time.perf_counter() - started
            if out.startswith(("I couldn't", "Telegram didn't answer")):
                # The lookup itself failed. "N of his M sets couldn't be read"
                # is a note under an answer, not this.
                report.add(label, "FAILED", took, out.splitlines()[0])
                continue
            answered.append(took)
            tags = list(_TAG.finditer(out))
            right = [t for t in tags if stickers._bare(t.group(1)) == stickers._bare(char)]
            if right:
                found[kind] += 1
                tag = tag or right[0].group(0)
                report.add(label, "FOUND", took)
            elif tags:
                # A name is matched as a substring of Telegram's keywords
                # ("pin" is inside "spinning"), so the tag can be another emoji.
                report.add(label, "OTHER EMOJI", took, "a different emoji is filed under that name")
            else:
                report.add(label, "NONE", took, "not in his emoji sets")
            if capped := _SETS_CAPPED.search(out):
                report.facts["emoji sets searched"] = f"{capped.group(1)} of {capped.group(2)} (the limit)"
            if broken := _SETS_FAILED.search(out):
                report.facts["emoji sets unreadable"] = f"{broken.group(1)} of {broken.group(2)}"
            if "no custom emoji sets installed" in out:
                report.facts["emoji sets searched"] = "0 (none installed)"
    if answered:
        # The first answered lookup reads every set. stickers keeps the index
        # only when every set could be read, so later lookups are cache hits
        # only then.
        report.facts["lookup, first (reads every set)"] = f"{answered[0]:.2f}s"
    if len(answered) > 1:
        later = sum(answered[1:]) / (len(answered) - 1)
        key = ("lookup, cached (average)" if "emoji sets unreadable" not in report.facts
               else "lookup, later (average; not cached - some sets unreadable)")
        report.facts[key] = f"{later:.2f}s"
    report.facts["found by character"] = f"{found['character']} of {len(EMOJI)}"
    report.facts["found by name"] = f"{found['name']} of {len(EMOJI)}"
    return tag


def list_packs(report: Report) -> None:
    """Step 2. His emoji sets and sticker packs: titles and counts only."""
    from jalen.tools import stickers

    _check_stop()
    started = time.perf_counter()
    out = stickers.list_sticker_packs("all")
    took = time.perf_counter() - started
    sections: dict[str, int] = {}
    listed: dict[str, list] = {}
    current = ""
    for line in out.splitlines():
        line = line.strip()
        if section := _SECTION.match(line):
            current = section.group(1)
            sections[current] = int(section.group(2))
            listed[current] = []
        elif (pack := _PACK.match(line)) and current:
            listed[current].append(pack.groups())
    if not sections:
        if "no emoji sets or sticker packs" in out:
            report.facts["emoji sets"] = report.facts["sticker packs"] = "0"
            report.add("list his emoji sets and sticker packs", "OK", took, "he has none")
        else:
            report.add("list his emoji sets and sticker packs", "FAILED", took,
                       out.splitlines()[0] if out else "no answer")
        return
    report.facts["emoji sets"] = str(sections.get("Custom emoji sets", 0))
    report.facts["sticker packs"] = str(sections.get("Sticker packs", 0))
    report.add("list his emoji sets and sticker packs", "OK", took,
               f"{report.facts['emoji sets']} emoji sets, {report.facts['sticker packs']} sticker packs")
    for name, packs in listed.items():
        print(f"    {name}:")
        for title, count, unit in packs:
            print(f"      {_clean(title, 60)} - {count} {unit}")
        if len(packs) < sections[name]:
            print(f"      (showing {len(packs)} of {sections[name]})")


def _only_saved_messages(to: str) -> None:
    """
    Before every send: the name must be Saved Messages, AND it must resolve -
    through the same messaging._resolve the send uses - to his own account.
    Today _resolve maps that name straight to get_me(); if that shortcut ever
    changed, the name would be searched among his chats, and a group someone
    titled "Saved Messages" would be a real destination. That is refused here.
    """
    from jalen.tools import messaging

    if to != DESTINATION:
        raise _WrongChat(f"refusing to send to {to!r}: this script sends to {DESTINATION} only")

    async def is_his_own(client):
        entity = await messaging._resolve(client, to)
        return bool(getattr(entity, "is_self", False))

    if not messaging.RUNTIME.run(is_his_own):
        raise _WrongChat(f"{to!r} did not resolve to your own account")


def _send(report: Report, tool, text: str, to: str = DESTINATION) -> "tuple[str, float]":
    _check_stop()
    _only_saved_messages(to)
    started = time.perf_counter()
    report.sending = True
    out = tool(to=to, text=text)
    report.sending = False
    return out, time.perf_counter() - started


def _not_sent(report: Report, step: str, out: str, took: float) -> None:
    """The replies both send tools share when nothing (or nothing known) arrived."""
    if out.startswith("Not confirmed"):
        report.add(step, "NOT CONFIRMED", took,
                   "Telegram didn't confirm it - look at Saved Messages before running this again")
    elif out.startswith("Nothing sent"):
        report.add(step, "NOTHING SENT", took, out.split("—", 1)[-1])
    else:
        report.add(step, "FAILED", took, out)


def send_test_post(report: Report, tag: str, ask) -> None:
    """Step 3. One post with a premium emoji, to Saved Messages, read back."""
    from jalen.tools import messaging

    step = "test post with a premium emoji"
    if not tag:
        report.add(step, "NOT TESTED", None, "no premium emoji was found to test with")
        return
    if not ask(f"Send ONE test post with a premium emoji to your {DESTINATION} now?"):
        report.add(step, "SKIPPED", None, "you said no")
        return
    text = f"Jalen live check, {datetime.now():%Y-%m-%d %H:%M}: premium emoji {tag} - safe to delete."
    out, took = _send(report, messaging.send_telegram_message, text)
    sent = f"Sent to {DESTINATION}"
    if out.startswith("Sent to ") and not out.startswith(sent):
        report.add(step, "WRONG CHAT", took, "the reply named a chat other than Saved Messages")
        raise _WrongChat("a send reported another destination")
    if not out.startswith(sent):
        _not_sent(report, step, out, took)
    elif messaging.PREMIUM_DROPPED in out:
        report.add(step, "DROPPED", took, "Telegram refused the premium emoji, so it went as an ordinary one")
    elif "WITHOUT formatting" in out:
        report.add(step, "DROPPED", took, "it went without its formatting")
    elif _ALL_KEPT.search(out):
        if report.facts.get("account has Telegram Premium") == "yes":
            report.add(step, "KEPT", took, "read back: the premium emoji arrived")
        else:
            # Telegram lets an account without Premium use custom emoji in
            # Saved Messages; a channel post may still drop them.
            report.add(step, "KEPT", took, "read back: it arrived in Saved Messages; "
                       "a channel post was not tested and may differ without Premium")
    elif _SOME_DROPPED.search(out):
        report.add(step, "DROPPED", took,
                   "read back: Telegram dropped the premium emoji (it does that for an account without Premium)")
    elif "couldn't read it back" in out:
        report.add(step, "NOT CHECKED", took, "sent, but the read-back failed - look at Saved Messages")
    else:
        report.add(step, "NO READ-BACK", took, "sent, but the reply had no read-back sentence")


def send_test_voice(report: Report, ask) -> None:
    """Step 4. One short voice message, to Saved Messages, read back."""
    from jalen.tools import messaging

    step = "test voice message"
    if not ask(f"Send ONE short voice message to your {DESTINATION} now?"):
        report.add(step, "SKIPPED", None, "you said no")
        return
    out, took = _send(report, messaging.send_voice_message, VOICE_WORDS)
    sent = f"Sent a voice message to {DESTINATION}"
    if out.startswith("Sent a voice message to ") and not out.startswith(sent):
        report.add(step, "WRONG CHAT", took, "the reply named a chat other than Saved Messages")
        raise _WrongChat("a send reported another destination")
    if not out.startswith(sent):
        _not_sent(report, step, out, took)
        return
    if length := _VOICE_LENGTH.search(out):
        report.facts["voice message length"] = length.group(1)
    if _VOICE_UNCHECKED in out:
        report.add(step, "NOT CHECKED", took, "sent, but the read-back failed - look at Saved Messages")
    elif _VOICE_ARRIVED in out:
        report.add(step, "OK", took, "read back: it arrived as a voice message")
    elif "not as a voice" in out:
        report.add(step, "WRONG KIND", took, "read back: it arrived, but not as a voice message")
    else:
        report.add(step, "NO READ-BACK", took, "sent, but the reply had no read-back sentence")


# ---------------------------------------------------------------------- main
INTRO = f"""
Jalen's live Telegram check
---------------------------
This uses YOUR Telegram account, the same way Jalen does. Jalen must be stopped
(only one program may use the Telegram session at a time). While it runs, do
NOT use Jalen's hotkeys, `run.py --stop/--restart` or `jalen.ps1 check`.

  1. look up three premium emoji (by character and by name)
  2. list your emoji sets and sticker packs (names and counts)
  3. send ONE test post with a premium emoji to {DESTINATION}  - asks first
  4. send ONE short voice message to {DESTINATION}             - asks first
  5. print a table to paste back

No chat gets anything except your {DESTINATION}. The voice line is made by the
same online text-to-speech Jalen uses. Ctrl+C stops it at any time.
"""


def _parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Check premium emoji, stickers and voice on your real Telegram ({DESTINATION} only).")
    parser.add_argument("--no-send", action="store_true",
                        help="steps 1 and 2 only: nothing is sent and nothing is asked")
    return parser.parse_args(argv)


def main(argv=None, ask=_ask) -> int:
    args = _parse(argv)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        # A redirected console here is cp1251: an emoji in a pack title would crash the print.
        reconfigure(errors="replace")
    print(INTRO)
    if _in_a_worktree():
        print(f"This is a work copy of Jalen ({ROOT}). Run the check from the main Jalen "
              "folder: that is where Jalen's lock and your Telegram sign-in are.")
        return 2

    report = Report()
    code = 0
    held = False
    try:
        from jalen import runtime
        from jalen.integrations import telegram_user
        from jalen.tools import messaging

        already = runtime.acquire(LOCK_MODE)
        if already is not None:
            if already.mode == LOCK_MODE:
                print(f"Another live Telegram check is already running (pid {already.pid}). "
                      "Let it finish, or press Ctrl+C in its window.")
            else:
                print(f"Jalen is running (pid {already.pid}, mode {already.mode}), and only one "
                      "program may use the Telegram session at a time.")
                print("Stop it first:  .venv\\Scripts\\python.exe run.py --stop")
            return 2
        held = True
        try:
            messaging._enabled()
        except RuntimeError as exc:
            print(exc)
            return 2
        if not telegram_user.have_session():
            print("Telegram isn't signed in on this laptop. Run:  "
                  ".venv\\Scripts\\python.exe scripts\\connect_telegram.py")
            return 2
        if not check_account(report):
            code = 1
        else:
            print("\nStep 1 - premium emoji")
            tag = look_up_emoji(report)
            print("\nStep 2 - emoji sets and sticker packs")
            list_packs(report)
            if args.no_send:
                report.add("test post with a premium emoji", "SKIPPED", None, "--no-send")
                report.add("test voice message", "SKIPPED", None, "--no-send")
            else:
                print("\nStep 3 - one test post")
                send_test_post(report, tag, ask)
                print("\nStep 4 - one voice message")
                send_test_voice(report, ask)
            code = 0 if report.ok() else 1
    except KeyboardInterrupt:
        report.add("the rest", "STOPPED", None, "stopped with Ctrl+C")
        code = 130
    except _StopAsked:
        report.add("the rest", "STOPPED", None, "Jalen's stop or restart was asked for")
        code = 130
    except _WrongChat as exc:
        print(f"\nSTOPPED: {exc}. Nothing more was sent.")
        code = 1
    except Exception as exc:  # noqa: BLE001 - TelegramNotConnected, an offline laptop, a timeout
        # Every send path turns its own errors into sentences, so anything
        # that gets here was not handed over as a send. The type name only:
        # an exception's text is not ours to print.
        ran_a_step = bool(report.rows)
        detail = (_clean(exc) if type(exc).__name__ == "TelegramNotConnected"
                  else f"{type(exc).__name__} - check the internet, then run it again")
        report.add("Telegram", "FAILED", None, detail)
        code = 1 if ran_a_step else 2
    finally:
        if held:
            try:
                messaging.RUNTIME.shutdown()
            except BaseException:  # noqa: BLE001 - closing is best effort, even on a second Ctrl+C
                pass
            finally:
                runtime.release()

    if report.sending:
        print(f"\nA send had started, so the message may have reached Telegram. "
              f"Look at {DESTINATION} before running this again.")
    if report.rows:
        print("\nStep 5 - the table\n")
        print(report.table())
        print()
        print(report.paste_block())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
