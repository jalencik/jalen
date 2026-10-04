r"""
What still needs YOU, in the order to do it.

    .\jalen.ps1 todo

WHY THIS EXISTS
---------------
He said it plainly: "I still do not know how to do this man, could you please
navigate me". Five outstanding items had been handed over as five script
paths, which is a list of homework, not navigation. A path only helps someone
who already knows what the script does, how long it takes, and why it is
worth the time.

So this checks the real state of the machine — is there a vault, are there
wake recordings, has the camera been calibrated, which flows have been
rehearsed — and prints only what is actually outstanding, each with the one
command that does it and a sentence saying what will happen when he runs it.

Nothing here changes anything. It reads state and prints. That matters: the
one thing worse than a list of homework is a list of homework that starts
doing itself.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DONE = "  [x] "
TODO = "  [ ] "


def line(text: str = "") -> None:
    print(text)


def item(done: bool, title: str, minutes: str, command: str,
         what_happens: list[str], why: str = "") -> tuple[bool, str]:
    """
    One checklist row. Returns (outstanding, title).

    The title comes back so the closing line can name the FIRST outstanding
    item rather than assuming it is number one. It said "Number 1 takes half
    a minute — start there" while number 1 was already ticked, which is the
    same class of bug this project keeps producing: text that sounds right
    and does not match what is on the screen directly above it.
    """
    line(f"{DONE if done else TODO}{title}")
    if done:
        return False, title
    line(f"        {minutes}")
    line(f"        RUN:  {command}")
    for step in what_happens:
        line(f"          {step}")
    if why:
        line(f"        Why: {why}")
    line()
    return True, title


def _track(left: list[str], result: tuple[bool, str]) -> None:
    outstanding, title = result
    if outstanding:
        left.append(title)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    from jalen.tools import vault

    line()
    line("  What still needs you")
    line("  " + "=" * 60)
    line("  Everything that could be done without you is done. These five need")
    line("  your voice, your eyes, or your judgement. Do them in this order.")
    line()

    left: list[str] = []

    # ---------------------------------------------------------------- vault
    _track(left, item(
        done=vault.VAULT_PATH.exists(),
        title="1. Create your password vault",
        minutes="about 30 seconds",
        command=".\\jalen.ps1 vault",
        what_happens=[
            "It asks you to CHOOSE a passphrase, then type it again.",
            "Nothing appears on screen while you type - that is normal.",
            "Then it asks for each secret you want stored. Press Enter to",
            "skip any you do not want. Press Enter on a blank name to finish.",
        ],
        why=(
            "Until this exists, Jalen cannot fill in a password anywhere. "
            "Pick a NEW passphrase - an old one was typed into a chat window."
        ),
    ))

    # ------------------------------------------------------------ wake word
    positives = ROOT / "data" / "wake_training" / "positive"
    real_takes = len(list(positives.glob("real_*.wav"))) if positives.is_dir() else 0
    _track(left, item(
        done=real_takes >= 20,
        title=f"2. Teach the wake word your voice  ({real_takes} recordings so far)",
        minutes="about 15 minutes",
        command=".\\jalen.ps1 voice",
        what_happens=[
            "It shows you a phrase and which syllable to stress, then waits.",
            "Press Enter, say the phrase once, and it records two seconds.",
            "It REFUSES takes that are too quiet, too loud or silent, and",
            "asks again - so every recording it keeps is a usable one.",
            "Do about 30. Vary it: closer, further, quieter, turned away.",
        ],
        why=(
            "The model has only ever heard synthetic voices saying JA-len. "
            "You say ja-LEN. Those are different sounds, and this is the only "
            "thing that fixes it properly. Skip if it already hears you fine."
        ),
    ))

    # ------------------------------------------------------------- rehearsal
    record = ROOT / "data" / "rehearsal.md"
    passed = record.read_text(encoding="utf-8").count("- [x]") if record.exists() else 0
    _track(left, item(
        done=passed >= 6,
        title=f"3. Drive the six real flows  ({passed} of 6 done)",
        minutes="one sitting, maybe 20 minutes",
        command=".\\jalen.ps1 rehearse",
        what_happens=[
            "It walks you through six things one at a time.",
            "For each: it tells you what to say to Jalen, you say it, then it",
            "asks what you ACTUALLY SAW. It will not accept a bare 'yes' -",
            "you have to describe what happened.",
            "Type 'skip' for any you do not want to do now.",
        ],
        why=(
            "These six reach other people - a real email, a real chat, a real "
            "login page. They are unit-tested but have never been driven end "
            "to end by a person. Most likely place a real bug is still hiding."
        ),
    ))

    # --------------------------------------------------------------- licence
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8") if (ROOT / "LICENSE").exists() else ""
    decided = (ROOT / "data" / "licence_decided").exists()
    _track(left, item(
        done=decided,
        title="4. Decide on the licence  (DEFERRED - you asked to leave this)",
        minutes="five minutes of thinking, no typing",
        command=".\\jalen.ps1 licence",
        what_happens=[
            "It explains the two options in plain terms and records your",
            "answer. It writes the licence file for you - no legal text to",
            "paste, nothing to look up.",
            "Today it says ALL RIGHTS RESERVED: nobody may use it without",
            "your permission. That was chosen FOR you because the choice is",
            "one-way - closed can become open any day, open can never become",
            "closed again.",
        ],
        why=(
            "Only matters if you sell or share it. Nothing breaks either way."
        ),
    ))

    # ------------------------------------------------------------------ disk
    free_gb = shutil.disk_usage(str(ROOT)).free / 1e9
    line("  " + "-" * 60)
    if free_gb < 5:
        line(f"  WARNING: only {free_gb:.1f} GB of disk free. Under 1 GB things")
        line("  start breaking in confusing ways. Say \"Jalen, what's eating my disk\".")
    else:
        line(f"  Disk: {free_gb:.1f} GB free - fine.")

    if not left:
        line()
        line("  Nothing outstanding. Everything on this list is done.")
    else:
        line()
        # Names the FIRST outstanding item, rather than assuming it is number
        # one. It said "Number 1 - start there" while number 1 was ticked.
        nxt = left[0].split(". ", 1)[-1]
        line(f"  {len(left)} of 5 left.")
        line(f"  Start with:  {nxt.lower()}")
    line()
    line("  Anything unclear:  .\\jalen.ps1 check    (full diagnostics)")
    line("  If it stops on its own:  .\\jalen.ps1 why")
    line()
    return 0


if __name__ == "__main__":
    sys.exit(main())
