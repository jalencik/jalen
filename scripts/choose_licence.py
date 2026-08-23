r"""
Decide what other people may do with this.

    .\jalen.ps1 licence

WHY THIS IS A SCRIPT AND NOT A PARAGRAPH IN A README
-----------------------------------------------------
The licence was chosen ON HIS BEHALF — All Rights Reserved — because the
choice is one-way and somebody had to pick a default. That makes it the one
outstanding item that is a decision rather than a task, and a decision handed
over as "go and read LICENSE" is one that never gets made.

So this states the two options in plain language, in terms of what actually
happens, and records the answer. It writes the licence file for him; it does
not ask him to paste legal text.

THE ASYMMETRY IS THE WHOLE POINT and is stated out loud below: closed can
become open whenever he likes, but open can never become closed again,
because every copy already distributed keeps its licence forever.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENCE = ROOT / "LICENSE"
MARKER = ROOT / "data" / "licence_decided"

MIT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

-----------------------------------------------------------------------------
THIRD-PARTY COMPONENTS. This licence covers the code in this repository. It
does not cover the dependencies in requirements.txt, which carry their own
licences and remain the property of their authors. The models under models/
come from openWakeWord, Silero and MediaPipe and carry their own terms.
Anyone intending to SELL this should read those first, in particular the
terms of the Claude, Groq and edge-tts services it depends on at runtime.
"""


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    current = LICENCE.read_text(encoding="utf-8") if LICENCE.exists() else ""
    is_mit = "MIT License" in current

    print()
    print("  Who may use this?")
    print("  " + "=" * 60)
    print(f"  Right now: {'MIT - anyone may use it' if is_mit else 'ALL RIGHTS RESERVED - nobody may'}")
    print()
    print("  Two options, in plain terms:")
    print()
    print("  1. KEEP IT CLOSED  (all rights reserved - what it says today)")
    print("     Nobody may copy, use or sell it without your written permission.")
    print("     You can still sell it, license it to people, or open it later.")
    print()
    print("  2. OPEN IT UP  (MIT)")
    print("     Anyone may use, change and sell it, as long as they keep your")
    print("     copyright notice. You keep the credit; you lose the control.")
    print()
    print("  THE ASYMMETRY, which is why closed is the default:")
    print("     closed -> open   you can do this any day you like")
    print("     open -> closed   you can NEVER do this. Every copy already")
    print("                      out there stays MIT forever.")
    print()
    print("  If you are unsure, keep it closed. Nothing breaks either way, and")
    print("  it only matters when you share or sell it.")
    print()

    try:
        answer = input("  Type 1 to keep it closed, 2 to open it, or Enter to decide later: ").strip()
    except EOFError:
        answer = ""

    if answer == "2":
        holder = input("  Name to put on the copyright: ").strip() or "O'ktam"
        LICENCE.write_text(MIT.format(year=date.today().year, holder=holder),
                           encoding="utf-8")
        _record(f"MIT, chosen {date.today()}, holder {holder}")
        print()
        print("  Done. LICENSE is now MIT. Anyone may use this.")
        print("  Remember: this cannot be undone for copies already shared.")
        return 0

    if answer == "1":
        _record(f"All rights reserved, confirmed {date.today()}")
        print()
        print("  Kept closed. LICENSE is unchanged - nobody may use it without")
        print("  your permission. Run this again any time to open it up.")
        return 0

    print()
    print("  Nothing changed. Run  .\\jalen.ps1 licence  when you have decided.")
    return 0


def _record(decision: str) -> None:
    """
    Remember that he DECIDED, not just what he decided.

    The checklist needs to distinguish "chose to keep it closed" from "has
    not looked at it yet" — and those two states leave an identical LICENSE
    file, so the file alone cannot tell them apart.
    """
    try:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(decision + "\n", encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
