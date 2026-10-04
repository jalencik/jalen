r"""
Everything Jalen can do, generated from the code.

    .\jalen.ps1 can              a readable list on screen
    .\jalen.ps1 can > list.md    the same thing as a file to mark up

He asked: "could you please list me out all of the things that Jalen could do
so far, and I will be testing that and finding rooms for improvements".

GENERATED, NOT WRITTEN, and that is the point. A hand-written feature list is
out of date the day after it is written, and this project has already shipped
documentation that promised things the code no longer did. This reads the
live tool registry, the safety tiers and the router rules, so it cannot claim
a capability that does not exist — and cannot miss one that does.

What it shows for each tool: what it does, what tier it sits in (whether it
asks first), and — where one exists — a phrase that reaches it without
spending a single token.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Human headings, in the order they are worth reading. Anything not matched
# lands in "Everything else", which is itself useful: a tool nobody could
# categorise is usually one nobody remembers exists.
GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Checking on itself", "It can test and diagnose itself now.",
     ("run_own_tests", "self_diagnose", "own_health", "open_own_project",
      "review_weaknesses", "log_weakness", "audit_digest")),
    ("Handing work to other AIs", "Gemini, ChatGPT, Hermes, Claude Code, the desktop app.",
     ("delegate_task", "follow_up_task", "list_delegations", "review_delegation",
      "master_prompt_guide", "hand_off_to", "ask_claude_code",
      "claude_code_status", "start_coding_job", "list_coding_jobs",
      "review_coding_job")),
    ("Driving ChatGPT and Gemini in a browser",
     "Opens Chrome, submits a work order, waits, reads the answer, and judges "
     "it against what you actually asked for.",
     ("web_delegate", "web_follow_up", "read_web_result", "list_web_chats",
      "web_sign_in_state", "open_signup", "close_browser!")),
    ("Learning and feedback",
     "Getting faster at what you repeat, and asking how it did.",
     ("what_i_have_learned", "forget_habit", "record_rating", "rating_history")),
    ("Code and projects", "Git, VS Code, and looking at what an agent changed.",
     ("project_status", "init_git_repo", "open_in_vscode")),
    ("Email", "Reading, drafting and sending Gmail.",
     ("email", "inbox", "gmail", "scan_inbox", "google_status")),
    ("Telegram", "Your own chats and your channel.",
     ("telegram", "send_posts", "save_draft_text", "community_post_guide",
      "sticker", "premium_emoji")),
    ("Calendar", "", ("calendar",)),
    ("The web", "", ("web_", "open_url", "search_site", "play_on_youtube",
                     "browser_tab", "current_page_url")),
    ("Files and folders", "",
     ("file", "folder", "directory", "document", "search_in_files", "open_target",
      "open_in", "alias")),
    ("This machine", "Windows, windows, volume, media, screenshots.",
     ("window", "volume", "media_", "screenshot", "read_screen", "click_element",
      "type_text", "keyboard_shortcut", "open_app", "close_app", "lock_",
      "sign_out", "get_", "system", "memory_report", "disk_report", "recycle")),
    ("The technician", "Diagnosing and repairing the machine.",
     ("diagnose", "fix_", "temp_file", "clear_temp", "open_windows",
      "open_startup", "cleanup", "refresh_system")),
    ("Passwords and logins", "The vault, and typing into forms.",
     ("vault", "secret", "credential", "site_", "fill_")),
    ("Remembering", "", ("remember", "recall", "memory")),
    ("Writing as you", "", ("voice_guide", "draft")),
    ("Asking you things", "", ("ask_user",)),
]

TIER_NOTE = {
    "green": "runs straight away",
    "amber": "tells you first, you can say stop",
    "red": "stops and waits for you to say yes",
    "black": "always refused",
}


def routed_tools() -> set[str]:
    """
    Every tool the router can reach without spending a token.

    Read off the router's own rules rather than a maintained list, so a rule
    that gets deleted stops being advertised.
    """
    from jalen.brain.router import IntentRouter
    from jalen.config import CONFIG

    return {tool for _pattern, tool, _build, _reply in IntentRouter(CONFIG)._rules}


# Things Jalen can do that are NOT tools: they are handled inside app.py
# because they need the microphone, the orb, or the process itself, and
# handing them to the model would mean "be quiet" needed a network call.
#
# Written here rather than derived, because there is nowhere to derive a
# DESCRIPTION from — but the NAMES are cross-checked against the router in
# tests, so this list cannot quietly drift out of date.
APP_LEVEL = {
    "jalen_quit": "Stop Jalen entirely",
    "jalen_pause": "Stop listening but stay running",
    "jalen_resume": "Start listening again",
    "jalen_sleep": "Go quiet until you say the wake word",
    "jalen_mute": "Stop talking, keep working",
    "jalen_unmute": "Talk again",
    "jalen_restart": "Restart itself",
    "jalen_ack": "Answer to its own name",
    "jalen_timing": "How long the last turn actually took, broken down",
    "jalen_read_all": "Speak the rest of an answer that was cut short",
    "morning_brief": "The day's summary on demand",
    "greet": "Say hello",
    "cancel": "Stop what you are doing",
    "acknowledge": "Acknowledge without acting",
    "reload_config": "Re-read config without restarting",
    "private_mode": "Stop writing anything to the audit log",
    "set_posture": "Change how cautious the safety gate is",
    "audit_digest": "What Jalen did today",
}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    from jalen import tools
    from jalen.brain.tools import TOOL_SPECS
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    routed = routed_tools()
    remaining = dict(TOOL_SPECS)

    print()
    total = len(tools.REGISTRY) + len(APP_LEVEL)
    print(f"# What Jalen can do  ({total} things)")
    print()
    print("Generated from the code, not written by hand - so it cannot promise")
    print("something that no longer exists. Tick what works, note what doesn't.")
    print()
    print("`*` means you can say it out loud and it answers instantly, for free,")
    print("without asking Claude.")
    print()

    print("## Talking to Jalen")
    print("_Its own controls. All free, all instant, none need the network._")
    print()
    for name, what in sorted(APP_LEVEL.items(), key=lambda kv: kv[1]):
        star = "*" if name in routed else " "
        print(f"- [ ] {star} **{name.replace('jalen_', '')}** - {what}")
    print()

    def claims(pattern: str, name: str) -> bool:
        """
        Substring by default; a trailing "!" means EXACTLY this tool.

        Needed because "close_browser" also matches "close_browser_tab", and
        those are different browsers: one is the window Jalen drives ChatGPT
        in, the other is a tab of his own. Filing them together would tell
        him that closing a YouTube tab ends a delegation.
        """
        return name == pattern[:-1] if pattern.endswith("!") else pattern in name

    for title, blurb, prefixes in GROUPS:
        names = sorted(
            n for n in remaining
            if any(claims(p, n) for p in prefixes)
        )
        if not names:
            continue
        print(f"## {title}")
        if blurb:
            print(f"_{blurb}_")
        print()
        for name in names:
            description = remaining.pop(name)[0]
            first = description.split(". ")[0].rstrip(".")
            if len(first) > 150:
                first = first[:147] + "..."
            tier = engine.classify(name, {}).tier.value
            star = "*" if name in routed else " "
            note = "" if tier == "green" else f"  [{TIER_NOTE[tier]}]"
            print(f"- [ ] {star} **{name}** - {first}{note}")
        print()

    if remaining:
        print("## Everything else")
        print()
        for name in sorted(remaining):
            description = remaining[name][0].split(". ")[0].rstrip(".")
            tier = engine.classify(name, {}).tier.value
            star = "*" if name in routed else " "
            note = "" if tier == "green" else f"  [{TIER_NOTE[tier]}]"
            print(f"- [ ] {star} **{name}** - {description[:150]}{note}")
        print()

    counts = {}
    for name in tools.REGISTRY:
        tier = engine.classify(name, {}).tier.value
        counts[tier] = counts.get(tier, 0) + 1
    print("---")
    print()
    print(f"{len(tools.REGISTRY)} tools: "
          + ", ".join(f"{n} {t}" for t, n in sorted(counts.items())))
    print(f"{len(routed)} of them also answer to a spoken phrase, for free.")
    print()
    print("Not in this list, because they are not tools:")
    print("  - the orb: size it by voice or Ctrl+Alt+B / Ctrl+Alt+S")
    print("  - the wake word, barge-in, and the follow-up window")
    print("  - the safety gate itself")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
