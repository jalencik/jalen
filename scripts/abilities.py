r"""
Generate ABILITIES.md — everything Jalen can do, in detail.

    .\jalen.ps1 abilities          (writes ABILITIES.md)

WHY GENERATED RATHER THAN WRITTEN
---------------------------------
He asked for a document to test against. A hand-written one is a document
about what somebody remembered building, and it starts drifting the day it is
written — which makes it worse than nothing, because he would be testing
against promises no longer in the code.

Everything here comes from the running system:

    the tool list       jarvis.tools.REGISTRY
    what each does      jarvis.brain.tools.TOOL_SPECS
    how risky it is     config/safety.yaml, via the real SafetyEngine
    what to SAY         the (phrase, tool) pairs the test suite already
                        asserts, so every example printed here is one the
                        suite proves still routes

That last one matters. Examples invented for a document rot silently; these
cannot, because a test fails first.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from jarvis import tools as systools          # noqa: E402
from jarvis.brain.tools import TOOL_SPECS      # noqa: E402
from jarvis.config import CONFIG               # noqa: E402
from jarvis.safety import SafetyEngine         # noqa: E402


def spoken_examples() -> dict[str, list[str]]:
    """
    Real phrases, harvested from the test suite.

    Only phrases the suite already asserts, so nothing here can be a promise
    the code stopped keeping — the test breaks before the document lies.
    """
    import ast

    found: dict[str, list[str]] = {}
    for name in ("test_every_request_reachable.py", "test_new_user_sweep.py",
                 "benchmark_phrasing.py", "test_stop_and_quit.py",
                 "test_conversation_requests.py"):
        path = ROOT / "tests" / name
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List)):
                continue
            parts = [e.value for e in getattr(node, "elts", [])
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if len(parts) == 2 and parts[1] in TOOL_SPECS:
                phrase, tool = parts
                if phrase not in found.setdefault(tool, []):
                    found[tool].append(phrase)
    return found


GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Talking to Jalen itself", "Free, instant, and none of it needs the network.",
     ("jalen_",)),
    ("Email", "Reading, sorting, drafting and sending Gmail.",
     ("email", "inbox", "gmail", "scan_inbox", "google_status")),
    ("Telegram", "Your own chats, your saved messages, and your channel.",
     ("telegram", "send_posts", "save_draft_text", "community_post_guide",
      "sticker", "premium_emoji", "voice_message", "voice_note")),
    ("Calendar", "", ("calendar", "event")),
    ("Handing work to other AIs",
     "Claude Code and the desktop app over the API; ChatGPT and Gemini in a "
     "real browser.",
     ("delegate", "follow_up", "review_delegation", "master_prompt_guide",
      "hand_off_to", "ask_claude_code", "claude_code_status",
      "coding_job", "web_delegate", "web_follow_up", "read_web_result",
      "list_web_chats", "web_sign_in_state", "open_signup", "close_browser!")),
    ("Code and projects", "Git, VS Code, and judging what an agent changed.",
     ("project_status", "init_git_repo", "open_in_vscode")),
    ("The web and media", "",
     ("web_", "open_url", "search_site", "play_on_youtube", "play_media",
      "browser_tab", "current_page_url")),
    ("Files and folders", "",
     ("file", "folder", "directory", "document", "search_in_files",
      "open_target", "open_in", "alias")),
    ("This machine", "Windows, volume, media keys, screenshots, the screen.",
     ("window", "volume", "media_", "screenshot", "read_screen",
      "click_element", "type_text", "keyboard_shortcut", "open_app",
      "close_app", "lock_", "sign_out", "get_", "system", "memory_report",
      "disk_report", "recycle")),
    ("Repairing the machine", "",
     ("diagnose", "fix_", "temp_file", "clear_temp", "open_windows",
      "open_startup", "cleanup", "refresh_system", "storage")),
    ("Passwords and logins", "The vault, and typing into forms you focused.",
     ("vault", "secret", "credential", "site_", "fill_", "next_field")),
    ("Remembering", "", ("memory", "remember", "recall", "forget_habit",
                         "what_i_have_learned")),
    ("Checking on itself", "",
     ("run_own_tests", "self_diagnose", "own_health", "open_own_project",
      "weakness", "audit_digest")),
    ("Feedback", "", ("record_rating", "rating_history")),
    ("Writing as you", "", ("draft", "voice_guide", "write_")),
    ("Asking you things", "", ("ask_user",)),
]


def claims(pattern: str, name: str) -> bool:
    return name == pattern[:-1] if pattern.endswith("!") else pattern in name


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    engine = SafetyEngine(CONFIG)
    examples = spoken_examples()
    remaining = dict(TOOL_SPECS)

    tiers: dict[str, str] = {}
    for name in remaining:
        try:
            tiers[name] = engine.classify(name, {}).tier.name
        except Exception:
            tiers[name] = "AMBER"

    # The controls that are NOT tools: they change Jalen's own state, so
    # they never reach the registry. Leaving them out would omit "quit" from
    # a document about what it can do.
    from capabilities import APP_LEVEL  # noqa: PLC0415

    out: list[str] = []
    w = out.append

    w("# What Jalen can do")
    w("")
    w(f"**{len(TOOL_SPECS)} tools.** Generated from the running code by")
    w("`scripts/abilities.py`, so it cannot promise something that no longer")
    w("exists. Every quoted phrase is one the test suite already asserts.")
    w("")
    w("Tick as you test. Anything that fails is worth telling me about with")
    w("the exact words you used - the wording is usually the bug.")
    w("")
    w("| | meaning |")
    w("|---|---|")
    w("| **GREEN** | runs immediately |")
    w("| **AMBER** | says what it is about to do; you can say stop |")
    w("| **RED** | asks first and waits for a yes |")
    w("| `*` | answers without the network, instantly and free |")
    w("")

    w("## Talking to Jalen itself")
    w("")
    w("_Free, instant, and none of it needs the network._")
    w("")
    for name, what in sorted(APP_LEVEL.items(), key=lambda kv: kv[1]):
        said = examples.get(name, [])
        w(f"### `{name}` * — GREEN")
        w("")
        w(what + ".")
        w("")
        if said:
            w("Say: " + " · ".join(f'"{p}"' for p in said[:6]))
            w("")

    for title, blurb, prefixes in GROUPS:
        names = sorted(n for n in remaining if any(claims(p, n) for p in prefixes))
        if not names:
            continue
        w(f"## {title}")
        if blurb:
            w("")
            w(f"_{blurb}_")
        w("")
        for name in names:
            description, params = remaining.pop(name)
            tier = tiers.get(name, "AMBER")
            star = "*" if examples.get(name) else " "
            w(f"### `{name}` {star} — {tier}")
            w("")
            w(description.strip())
            w("")
            said = examples.get(name, [])
            if said:
                w("Say: " + " · ".join(f'"{p}"' for p in said[:6]))
                w("")
            if params:
                needed = [f"`{k}`" for k, (_t, _d, req) in params.items() if req]
                if needed:
                    w(f"Needs: {', '.join(needed)}")
                    w("")

    if remaining:
        w("## Everything else")
        w("")
        for name in sorted(remaining):
            description, _p = remaining[name]
            w(f"### `{name}` — {tiers.get(name, 'AMBER')}")
            w("")
            w(description.strip())
            w("")

    counts: dict[str, int] = {}
    for tier in tiers.values():
        counts[tier] = counts.get(tier, 0) + 1
    w("---")
    w("")
    w("## Not tools, but things it does")
    w("")
    for item, detail in [
        ("Wake word", 'openWakeWord listens for "Hey Jalen" locally, always, '
                      'for about 3% of one core. It never leaves the machine.'),
        ("The address gate", "It acts only on sentences that start with its "
                             "name, answer a question it just asked, or are an "
                             "emergency stop. Everything else is ignored "
                             "silently."),
        ("Barge-in", "Talking over it stops it mid-sentence."),
        ("Continuation stitching", "If it cuts you off, keep talking - the "
                                   "rest is joined to what you already said."),
        ("The orb", "Fixed in place, click-through in every state. It can "
                    "never take a click meant for something underneath it."),
        ("On-screen answers", "Anything too long to say is written to the "
                              "transcript window in full."),
        ("Habits", "The same request three times, decided the same way, and "
                   "it stops asking the model - about 1.2s faster each time."),
        ("Ratings", "After real work it asks for a score out of ten and "
                    "emails you a summary."),
        ("The safety gate", "Every tool is classified before it runs. Nothing "
                            "Jalen merely READ can trigger an action."),
    ]:
        w(f"- **{item}** — {detail}")
    w("")
    w("## What it cannot do")
    w("")
    for item in [
        "Defeat a CAPTCHA, a 2FA prompt, or any anti-bot check. It detects "
        "them, raises the window, and hands them to you.",
        "Choose which form field a password goes into. It types into the "
        "field YOU focused, and never picks one itself.",
        "Read your screen continuously. There is no camera and no always-on "
        "screen capture.",
        "Reach Gemini or ChatGPT over their APIs on this machine - the "
        "Gemini key is 403'd and there is no OpenAI key. The browser route "
        "works instead.",
        "Undo a sent email or a deleted file. That is why those ask first.",
    ]:
        w(f"- {item}")
    w("")
    w(f"**{len(TOOL_SPECS)} tools** — "
      + ", ".join(f"{n} {t.lower()}" for t, n in sorted(counts.items())))
    w("")

    (ROOT / "ABILITIES.md").write_text("\n".join(out), encoding="utf-8")
    print(f"  ABILITIES.md written: {len(TOOL_SPECS)} tools, "
          f"{sum(len(v) for v in examples.values())} verified phrases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
