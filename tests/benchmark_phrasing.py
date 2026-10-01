"""
Router phrasing benchmark: real utterances a person actually says, asserted
to route LOCALLY (jarvis/brain/router.py) to the right tool.

Why this file exists: every phrase that misses the router pays a full
Claude round trip — 3 to 18 seconds instead of well under a millisecond,
and it costs part of the Claude Pro allowance the whole build is budgeted
against (see router.py's own docstring). This file is the measurement harness
for that: PHRASES below is a list of (utterance, expected_tool) pairs pulled
from the task's stated real-world scenarios (media, windows, web, files,
system, documents, self-commands, and messy/polite real speech — "umm open
capcut", "hey can you like open chrome"), and test_phrase_routes_locally
fails loudly, phrase by phrase, on anything that still misses or lands on
the wrong tool.

This is NOT a duplicate of test_router_coverage.py, which proves specific
regex-ordering bug fixes (why "close this window" isn't close_app, why
"search for X" still means local file search). This file is coverage
breadth: as many real phrasings as reasonably collected, checked in bulk,
so a future regression anywhere in router.py shows up as one exact phrase
failing here rather than being noticed a week later in data/router_misses.log.

Every tool any phrase below expects is proven, once, to actually exist and
be dispatchable: present in jarvis.tools.REGISTRY (or one of the app-level
jarvis_*/greet/cancel/acknowledge/audit_digest intents app.handle_local()
special-cases directly, same as test_router_coverage.py's sweep), with a
brain/tools.py TOOL_SPECS entry, and an EXPLICIT tier in config/safety.yaml.
A rule pointing at a tool that doesn't exist produces total silence — a bug
that has already shipped once — so this is checked directly, not assumed.

The two destructive tools reachable from this list (empty_recycle_bin,
sign_out) are RED and are never actually invoked here, same discipline as
test_router_coverage.py: only routing (tool name + registration + tier) is
checked, never execution.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.brain.router import IntentRouter  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.safety import SafetyEngine, Tier  # noqa: E402


@pytest.fixture
def router():
    return IntentRouter(CONFIG)


def route_tool(router, text):
    intent = router.route(text)
    return intent.tool if intent else None


# ============================================================================
# The benchmark: (utterance, expected tool). Grouped by the task's own
# scenario categories purely for readability — the test below runs them all
# as one flat, unordered set.
# ============================================================================
PHRASES: list[tuple[str, str]] = [
    # ---- media -------------------------------------------------------------
    ("play timeless", "play_media"),
    ("put on some music", "media_play_pause"),
    ("put on the music", "media_play_pause"),
    ("pause the music", "media_play_pause"),
    ("stop the music", "media_play_pause"),
    ("skip this", "media_next"),
    ("skip", "media_next"),
    ("next song", "media_next"),
    ("next track", "media_next"),
    ("previous song", "media_previous"),
    ("turn it down", "volume_step"),
    ("turn it up", "volume_step"),
    ("volume up", "volume_step"),
    ("volume down", "volume_step"),
    ("volume 40", "volume_set"),
    ("set volume to 70", "volume_set"),
    ("mute the sound", "volume_mute_toggle"),
    ("unmute the volume", "volume_mute_toggle"),
    ("play spotify", "open_app"),

    # ---- windows -------------------------------------------------------------
    ("close this", "keyboard_shortcut"),
    ("close this window", "keyboard_shortcut"),
    ("close the tab", "keyboard_shortcut"),
    ("minimize", "window_state"),
    ("maximize", "window_state"),
    ("minimize this window", "window_state"),
    ("maximize the window", "window_state"),
    ("switch to chrome", "focus_window"),
    ("go to capcut", "focus_window"),
    ("show me my windows", "get_window_list"),
    ("what windows are open", "get_window_list"),
    ("close notepad", "close_app"),
    ("new tab", "keyboard_shortcut"),

    # ---- web -----------------------------------------------------------------
    ("open youtube", "open_url"),
    ("go to chess.com", "open_url"),
    ("open instagram", "open_url"),
    ("search for eco pulse", "search_files"),
    ("google eco pulse", "open_url"),
    # "search the web for X" is no longer a router phrase: it opened a Google
    # tab and read the address aloud (live QA 2026-10-01). It reaches the
    # brain, which reads pages and answers; see test_research_phrasing_routing.py.
    ("open gmail", "open_url"),
    ("open github", "open_url"),

    # ---- files ---------------------------------------------------------------
    ("make me a new folder called Projects", "create_folder"),
    ("create a folder called Archive", "create_folder"),
    ("create a file called notes.txt", "create_file"),
    ("make a file called todo.txt", "create_file"),
    ("rename notes.txt to notes-old.txt", "rename_file"),
    ("copy notes.txt to desktop", "copy_file"),
    ("copy report.docx to documents", "copy_file"),

    # ---- system ---------------------------------------------------------------
    ("what's my battery", "get_battery"),
    ("how much space", "disk_report"),
    ("how much space do i have", "disk_report"),
    ("what's eating my memory", "memory_report"),
    ("lock the pc", "lock_workstation"),
    ("lock it", "lock_workstation"),
    ("take a screenshot", "screenshot"),
    ("screenshot", "screenshot"),
    ("what time is it", "get_time"),
    ("what's the date", "get_date"),

    # ---- documents ---------------------------------------------------------------
    ("read changes.pdf", "read_document"),
    ("what's in my CV", "read_document"),
    ("find files about eco pulse", "search_in_files"),
    ("find files containing budget", "search_in_files"),

    # ---- self (Jarvis's own commands) ------------------------------------------
    ("quit", "jalen_quit"),
    ("pause", "jalen_pause"),
    ("resume", "jalen_resume"),
    ("be quiet", "jalen_mute"),
    ("mute", "jalen_mute"),
    ("shut up", "jalen_mute"),
    ("unmute", "jalen_unmute"),
    ("go to sleep", "jalen_sleep"),
    ("wake up", "jalen_resume"),
    ("what did you do today", "audit_digest"),

    # ---- polite / messy real speech --------------------------------------------
    ("hey can you like open chrome", "open_target"),
    ("jarvis please open my cv", "open_target"),
    ("umm open capcut", "open_target"),
    ("can you open chrome", "open_target"),
    ("could you please close notepad", "close_app"),
    ("please take a screenshot", "screenshot"),
    ("so open chrome", "open_target"),
    ("uh open capcut", "open_target"),
    ("hey jarvis open chrome", "open_target"),
    ("jarvis, mute", "jalen_mute"),

    # ---- clipboard / editing (existing coverage, locked in here too) -----------
    ("select all", "keyboard_shortcut"),
    ("copy that", "keyboard_shortcut"),
    ("undo", "keyboard_shortcut"),
    ("save it", "keyboard_shortcut"),

    # ---- RED tools: routing checked, never executed (see module docstring) ----
    ("empty the recycle bin", "empty_recycle_bin"),
    ("sign me out", "sign_out"),

    # ---- conversation control ----------------------------------------------------
    ("good morning", "greet"),
    ("thanks jarvis", "acknowledge"),
    ("never mind", "cancel"),
]


@pytest.mark.parametrize("phrase,expected_tool", PHRASES, ids=[p for p, _ in PHRASES])
def test_phrase_routes_locally(router, phrase, expected_tool):
    intent = router.route(phrase)
    assert intent is not None, (
        f"{phrase!r} fell through to Claude (a 3-18s round trip) instead of "
        f"routing locally to {expected_tool!r}"
    )
    assert intent.tool == expected_tool, (
        f"{phrase!r} routed to {intent.tool!r}, expected {expected_tool!r}"
    )


def test_measured_pass_rate(router):
    """
    The number this task asks to be reported: what fraction of PHRASES
    route locally, and to the tool actually expected. Fails with the full
    list of misses/mismatches rather than pytest's usual one-at-a-time
    parametrize output, so a regression is legible as a single number plus
    exactly which phrases broke it.
    """
    failures = []
    for phrase, expected_tool in PHRASES:
        intent = router.route(phrase)
        got = intent.tool if intent else None
        if got != expected_tool:
            failures.append(f"  {phrase!r}: got {got!r}, expected {expected_tool!r}")

    total = len(PHRASES)
    passed = total - len(failures)
    pass_rate = passed / total
    assert pass_rate == 1.0, (
        f"router phrasing benchmark: {passed}/{total} ({pass_rate:.0%}) passed\n"
        + "\n".join(failures)
    )


# ============================================================================
# Every tool any phrase above expects must be real — present in
# jarvis.tools.REGISTRY (or a special-cased app-level intent), have a
# brain/tools.py TOOL_SPECS entry, and an EXPLICIT safety.yaml tier. Mirrors
# test_router_coverage.py's equivalent sweep so this file stands on its own.
# ============================================================================
APP_LEVEL_INTENTS = {
    "jalen_mute", "jalen_unmute", "jalen_sleep", "jalen_quit", "jalen_pause",
    "jalen_resume", "jalen_restart", "private_mode", "set_posture", "morning_brief",
    "audit_digest", "cancel", "acknowledge", "greet", "reload_config",
}

# Router-only tools (never touch a live machine when this file runs) get an
# explicit expected tier here; every OTHER tool is only checked for
# existence + an explicit (non-default) tier, whatever it is, since this
# file's job is coverage breadth, not re-litigating the whole tier table.
EXPECTED_TIERS = {
    "empty_recycle_bin": Tier.RED,
    "sign_out": Tier.RED,
}


def test_every_expected_tool_is_registered_and_dispatchable():
    from jarvis import tools

    expected_tools = {tool for _phrase, tool in PHRASES}
    missing_registry = {
        t for t in expected_tools
        if t not in tools.REGISTRY and t not in APP_LEVEL_INTENTS
    }
    assert not missing_registry, (
        f"these tools have no implementation in jarvis.tools.REGISTRY and "
        f"aren't an app-level intent: {missing_registry}"
    )


def test_every_expected_tool_has_a_tool_spec():
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS

    expected_tools = {tool for _phrase, tool in PHRASES}
    registry_tools = expected_tools & set(tools.REGISTRY)
    missing_specs = registry_tools - set(TOOL_SPECS)
    assert not missing_specs, f"no brain/tools.py TOOL_SPECS entry for: {missing_specs}"


def test_every_expected_tool_has_an_explicit_safety_tier():
    from jarvis import tools

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"

    expected_tools = {tool for _phrase, tool in PHRASES}
    registry_tools = expected_tools & set(tools.REGISTRY)

    unclassified = []
    wrong_tier = []
    for tool in sorted(registry_tools):
        verdict = engine.classify(tool, {})
        if verdict.detail.get("unclassified"):
            unclassified.append(tool)
            continue
        if tool in EXPECTED_TIERS and verdict.tier is not EXPECTED_TIERS[tool]:
            wrong_tier.append(f"{tool}: got {verdict.tier.value}, expected {EXPECTED_TIERS[tool].value}")

    assert not unclassified, f"these tools fell through to the unclassified-AMBER default: {unclassified}"
    assert not wrong_tier, f"tier mismatch: {wrong_tier}"


def test_destructive_tools_stay_gated_never_a_fast_path_around_confirmation():
    """The two RED tools this benchmark routes to (empty_recycle_bin,
    sign_out) must still stop and ask — routing locally only skips the
    Claude round trip for interpreting the phrase, never the confirmation
    gate itself."""
    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    for tool in ("empty_recycle_bin", "sign_out"):
        assert engine.classify(tool, {}).tier is Tier.RED
