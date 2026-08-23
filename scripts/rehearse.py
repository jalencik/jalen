"""
Drive the untested flows once each, with him.  (HANDOFF item 4)

    .venv\\Scripts\\python.exe scripts\\rehearse.py
    .venv\\Scripts\\python.exe scripts\\rehearse.py --show      (read the record)

WHY THIS EXISTS
---------------
Six flows shipped built, unit-tested and reachable, and had never been driven
all the way through by the brain in a live turn. tests/test_flow_rehearsal.py
now covers the composition — the gate, the ordering, the argument shapes —
against fakes. What it cannot cover is the last inch, and the last inch is
where this project's bugs live:

    a real opportunity email, not a fixture
    a real login page, with a real password box that has focus
    a paste actually landing in Claude's input box
    a message arriving in a chat another human can read

Those need eyes. This walks him through them one at a time and writes down
what actually happened.

THE ONE RULE
------------
It records what HE OBSERVED, not what the code returned. Every check asks
what he saw and refuses a bare "yes" — because "it said it sent" and "it
arrived" are exactly the two things this project keeps confusing, and a
rehearsal script that accepts the first one has reproduced the bug it was
written to catch.

Nothing here sends anything by itself. Each step tells him what to say to
Jalen; Jalen's own safety gate then applies as normal. This script only
prompts and records.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RECORD = ROOT / "data" / "rehearsal.md"


class Step:
    def __init__(self, key: str, title: str, why: str,
                 setup: list[str], say: str, observe: str) -> None:
        self.key = key
        self.title = title
        self.why = why
        self.setup = setup
        self.say = say
        self.observe = observe


STEPS = [
    Step(
        "email_to_post",
        "Email -> research -> community post",
        "Needs a real opportunity email and your approval to post.",
        [
            "Have at least one real 'opportunity'-ish email in your inbox.",
            "Jalen running in voice mode (.\\jalen.ps1 start).",
        ],
        "Jalen, scan my inbox for opportunities, research the best one, "
        "and draft a post for my ML channel.",
        "Open Telegram and look at the channel's draft box. Is the draft "
        "THERE, and is it about the email it claimed to read?",
    ),
    Step(
        "fill_credential",
        "fill_credential on a real site",
        "Needs the vault created and a real login page. This is the flow "
        "where a wrong answer types a password into the wrong box.",
        [
            "Run scripts/vault_setup.py first if you have not (see --show).",
            "Open a real login page and CLICK INTO the password field "
            "yourself. Jalen types into whatever you have focused; it never "
            "picks the field.",
        ],
        "Jalen, fill in my password for this site.",
        "Did the password land in the box YOU had focused, and nowhere else? "
        "Check the username field and any search box on the page too.",
    ),
    Step(
        "send_posts",
        "send_posts with several posts",
        "Sends to a real chat that other people can read.",
        ["Pick a chat you do not mind three test posts appearing in."],
        "Jalen, send these three posts to my ML channel: one, two, three.",
        "Count them in the chat. Did all three arrive, and did Jalen's "
        "spoken summary match the number that actually landed?",
    ),
    Step(
        "ask_user",
        "ask_user through a live voice turn",
        "Unit-tested with a fake; the real microphone path is unverified.",
        ["Voice mode, microphone working."],
        "Jalen, rename one of the files on my desktop.",
        "When it asks which file, ANSWER OUT LOUD without saying 'hey Jalen' "
        "first. Did it hear the answer and carry on?",
    ),
    Step(
        "clear_temp_files",
        "clear_temp_files (RED, deletes files)",
        "Irreversible. Worth watching the confirmation work once.",
        ["Nothing you care about in %TEMP%."],
        "Jalen, clear my temp files.",
        "Say NO the first time. Did it actually not delete anything? Then "
        "run it again and say yes: did the number it reported match what "
        "'Jalen, how much temp file space do I have' said beforehand?",
    ),
    Step(
        "hand_off_to_cowork",
        "hand_off_to_cowork end to end",
        "The launch and the clipboard are verified. The paste landing in "
        "Claude's input box was never confirmed by eye.",
        ["Claude desktop app installed and signed in."],
        "Jalen, hand this task to cowork: draft follow-up emails to the "
        "researchers I emailed last week.",
        "Look at the Claude window. Is the brief actually IN the input box, "
        "complete, and not truncated or pasted over something else?",
    ),
]


def load_record() -> dict[str, dict]:
    """Parse the record back so a half-finished rehearsal can resume."""
    done: dict[str, dict] = {}
    if not RECORD.exists():
        return done
    for line in RECORD.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- ["):
            continue
        try:
            mark = line[3]
            rest = line.split("]", 1)[1].strip()
            key, _, note = rest.partition(" — ")
            done[key.strip()] = {"passed": mark.lower() == "x", "note": note.strip()}
        except (IndexError, ValueError):
            continue
    return done


def save_result(step: Step, passed: bool, note: str) -> None:
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    if not RECORD.exists():
        RECORD.write_text(
            "# Rehearsal record\n\n"
            "The flows that had never run end to end, driven with him present.\n"
            "Written by scripts/rehearse.py. `[x]` passed, `[ ]` failed.\n"
            "The note is what HE observed, not what the code returned.\n\n",
            encoding="utf-8",
        )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(RECORD, "a", encoding="utf-8") as fh:
        fh.write(f"- [{'x' if passed else ' '}] {step.key} — {note}  ({stamp})\n")


def ask_observation(prompt: str) -> tuple[bool, str]:
    """
    Refuse a bare yes.

    "It said it sent" and "it arrived" are the two things this project keeps
    confusing, and a rehearsal that accepts the first has reproduced the bug
    it exists to catch. So the answer has to describe something seen.
    """
    while True:
        print(f"\n  {prompt}")
        answer = input("  what did you actually see? > ").strip()
        if not answer:
            print("  (nothing recorded — describe it, or type 'skip')")
            continue
        if answer.lower() in ("skip", "s"):
            return False, "skipped"
        if answer.lower() in ("y", "yes", "ok", "fine", "good", "n", "no"):
            print("  Not enough. 'It said it worked' and 'it worked' are")
            print("  different findings, and only one of them is a test.")
            print("  Say what you saw: what arrived, where, and how many.")
            continue
        verdict = input("  did it do what it claimed? [y/n] > ").strip().lower()
        return verdict.startswith("y"), answer


def run(only: str | None = None, redo: bool = False) -> int:
    done = load_record()
    print("Rehearsal — the flows that had never run end to end.\n")
    print("One at a time, with Jalen running. Ctrl+C stops; everything")
    print(f"recorded so far is kept in {RECORD.relative_to(ROOT)}.\n")

    todo = [s for s in STEPS if only is None or s.key == only]
    if only and not todo:
        print(f"No such step: {only}. Try one of: {', '.join(s.key for s in STEPS)}")
        return 1
    if not redo:
        todo = [s for s in todo if not done.get(s.key, {}).get("passed")]
    if not todo:
        print("All six have passed already. Use --redo to run them again.")
        return 0

    for index, step in enumerate(todo, start=1):
        print("=" * 70)
        print(f"[{index}/{len(todo)}]  {step.title}")
        print("=" * 70)
        print(f"\n  why it was untested: {step.why}\n")
        print("  before you start:")
        for line in step.setup:
            print(f"    - {line}")
        print(f"\n  SAY THIS TO JALEN:\n\n      \"{step.say}\"\n")
        if input("  ready? [enter to continue, 's' to skip] > ").strip().lower() == "s":
            save_result(step, False, "skipped")
            print("  skipped.\n")
            continue

        try:
            passed, note = ask_observation(step.observe)
        except KeyboardInterrupt:
            print("\n\nStopped. What you recorded is kept.")
            return 0

        save_result(step, passed, note)
        print(f"\n  recorded: {'PASS' if passed else 'FAIL'} — {note}\n")
        if not passed:
            print("  That is a real finding. Worth adding to data/weaknesses.md")
            print("  or fixing before moving on.\n")

    print("=" * 70)
    return show()


def show() -> int:
    if not RECORD.exists():
        print("Nothing rehearsed yet. Run: python scripts\\rehearse.py")
        return 0
    print(RECORD.read_text(encoding="utf-8"))
    done = load_record()
    outstanding = [s.key for s in STEPS if not done.get(s.key, {}).get("passed")]
    if outstanding:
        print(f"Still unproven: {', '.join(outstanding)}")
    else:
        print("All six flows have been driven end to end and passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Walk through the flows that need a person present."
    )
    parser.add_argument("--show", action="store_true", help="print the record and exit")
    parser.add_argument("--only", help="run just one step, by key")
    parser.add_argument("--redo", action="store_true", help="re-run steps that already passed")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.show:
        return show()
    try:
        return run(args.only, args.redo)
    except KeyboardInterrupt:
        print("\n\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
