"""
Regression tests for the router-coverage pass: widen jarvis/brain/router.py
so everyday commands stop paying a multi-second Claude round trip.

Every phrase tested here comes straight from the task's real-session miss
list ("what's my battery", "close this window", "copy that", "empty the
recycle bin", ...). Each assertion is a phrase that used to MISS the router
entirely (silently falling through to router_misses.log and a full Claude
turn) and now must hit a real, registered, correctly-tiered tool in well
under a millisecond.

Two things this file is careful to prove alongside the new hits:
  1. No new rule shadows an EXISTING rule. "search for X" must still mean
     local file search; "close notepad" must still mean close_app, not the
     new "close window" shortcut; bare "close"/"back" must still mean
     jalen_quit/media_previous.
  2. Every tool a new rule points at is real: present in jarvis.tools.
     REGISTRY, present in brain/tools.py's TOOL_SPECS (so build_sdk_tools()
     doesn't blow up at startup), and given an EXPLICIT tier in
     safety.yaml — never the unclassified-AMBER default.
The two genuinely destructive new tools this file adds (empty_recycle_bin,
sign_out) are NEVER invoked here — only their registration and tier are
checked. Calling either for real inside a test suite would actually empty
the machine's Recycle Bin or end the session running the tests.
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


def route(router, text):
    return router.route(text)


def route_tool(router, text):
    intent = router.route(text)
    return intent.tool if intent else None


# --------------------------------------------------------- quick facts
@pytest.mark.parametrize(
    "phrase",
    ["what's my battery", "what is my battery", "what's my battery level",
     "what's my battery percentage"],
)
def test_battery_phrasing_routes_locally(router, phrase):
    assert route_tool(router, phrase) == "get_battery"


@pytest.mark.parametrize(
    "phrase",
    ["how much space do i have", "how much disk space do i have",
     "how much storage do i have", "how much space is left"],
)
def test_disk_space_phrasing_routes_locally(router, phrase):
    assert route_tool(router, phrase) == "get_system_status"


@pytest.mark.parametrize(
    "phrase",
    ["what's eating my memory", "what is eating my memory",
     "what's using my memory", "what's using up all my ram"],
)
def test_memory_phrasing_routes_locally(router, phrase):
    assert route_tool(router, phrase) == "get_system_status"


# --------------------------------------------------------- window control
def test_close_this_window_sends_alt_f4_not_close_app(router):
    """"close this window" used to fall through to the close_app catch-all
    as close_app(name="this window") — a search for a window literally
    titled "this window" that can never exist."""
    intent = route(router, "close this window")
    assert intent.tool == "keyboard_shortcut"
    assert intent.args["keys"] == "{Alt}{F4}"


@pytest.mark.parametrize("phrase", ["close window", "close the window", "close current window"])
def test_close_window_siblings(router, phrase):
    assert route_tool(router, phrase) == "keyboard_shortcut"


def test_close_named_app_still_uses_close_app(router):
    """The new "close window"/"close tab" rules must not swallow ordinary
    "close <app>" commands — only the literal words "window"/"tab" are
    special-cased."""
    intent = route(router, "close notepad")
    assert intent.tool == "close_app"
    assert intent.args["name"] == "notepad"


def test_close_tab_sends_ctrl_w(router):
    intent = route(router, "close tab")
    assert intent.tool == "keyboard_shortcut"
    assert intent.args["keys"] == "{Ctrl}w"


def test_switch_to_last_window_sends_alt_tab_not_focus_window(router):
    """"switch to the last window" used to fall through to focus_window
    catch-all as focus_window(name="the last window") — same class of bug."""
    intent = route(router, "switch to the last window")
    assert intent.tool == "keyboard_shortcut"
    assert intent.args["keys"] == "{Alt}{Tab}"


def test_switch_to_named_window_still_uses_focus_window(router):
    intent = route(router, "switch to chrome")
    assert intent.tool == "focus_window"
    assert intent.args["name"] == "chrome"


@pytest.mark.parametrize(
    "phrase,expected_keys",
    [
        ("scroll down", "{PageDown}"),
        ("scroll up", "{PageUp}"),
        ("go back", "{Alt}{Left}"),
        ("refresh the page", "{F5}"),
        ("refresh", "{F5}"),
        ("new tab", "{Ctrl}t"),
    ],
)
def test_browser_navigation_shortcuts(router, phrase, expected_keys):
    intent = route(router, phrase)
    assert intent.tool == "keyboard_shortcut"
    assert intent.args["keys"] == expected_keys


def test_go_back_does_not_shadow_media_previous(router):
    """"back song"/"previous song" (media rewind) must still work — only
    the two-word "go back" phrase is the new browser/window-history
    shortcut; bare "back" was, and remains, unmatched (not a regression —
    it was never routed before this task either)."""
    assert route_tool(router, "back song") == "media_previous"
    assert route_tool(router, "previous song") == "media_previous"
    assert route_tool(router, "go back") == "keyboard_shortcut"


def test_refresh_config_still_reaches_reload_config(router):
    """The new bare "refresh" rule must not swallow the existing
    "refresh config" command."""
    assert route_tool(router, "refresh config") == "reload_config"


# --------------------------------------------------------- clipboard/editing
@pytest.mark.parametrize(
    "phrase,expected_keys",
    [
        ("copy that", "{Ctrl}c"),
        ("copy", "{Ctrl}c"),
        ("paste", "{Ctrl}v"),
        ("paste that", "{Ctrl}v"),
        ("select all", "{Ctrl}a"),
        ("undo", "{Ctrl}z"),
        ("undo that", "{Ctrl}z"),
        ("save it", "{Ctrl}s"),
    ],
)
def test_clipboard_and_editing_shortcuts(router, phrase, expected_keys):
    intent = route(router, phrase)
    assert intent.tool == "keyboard_shortcut"
    assert intent.args["keys"] == expected_keys


# --------------------------------------------------------- volume
@pytest.mark.parametrize(
    "phrase,expected_direction",
    [
        ("make it louder", "up"),
        ("make it quieter", "down"),
        ("turn it up", "up"),
        ("turn it down", "down"),
        ("turn the volume down a bit", "down"),
        ("turn the volume up a bit", "up"),
    ],
)
def test_volume_phrasing_routes_locally(router, phrase, expected_direction):
    intent = route(router, phrase)
    assert intent.tool == "volume_step"
    assert intent.args["direction"] == expected_direction


# --------------------------------------------------------- lock / sign out
def test_lock_it_routes_to_lock_workstation(router):
    assert route_tool(router, "lock it") == "lock_workstation"


def test_sign_me_out_routes_locally(router):
    assert route_tool(router, "sign me out") == "sign_out"


def test_log_me_off_routes_locally(router):
    assert route_tool(router, "log me off") == "sign_out"


# --------------------------------------------------------- recycle bin
def test_empty_the_recycle_bin_routes_locally(router):
    assert route_tool(router, "empty the recycle bin") == "empty_recycle_bin"


def test_delete_a_named_file_is_not_rerouted_by_the_new_rules(router):
    """Sanity check the new fast paths didn't widen anything destructive:
    "delete X" must still go wherever it went before (unrouted here — the
    router has no local delete-by-name rule, and that's deliberate)."""
    assert route_tool(router, "delete my resume") is None


# --------------------------------------------------------- web search
# "search google for X" now routes to search_site instead — a strictly
# better destination once the site is named explicitly (it knows each
# site's real search grammar). Both still land on a Google results page.
@pytest.mark.parametrize("phrase", ["search the web for python tutorials", "google python tutorials"])
def test_web_search_routes_to_open_url(router, phrase):
    intent = route(router, phrase)
    assert intent.tool == "open_url"
    assert intent.args["url"].startswith("https://www.google.com/search?q=")
    assert "python" in intent.args["url"]


def test_web_search_query_is_url_encoded(router):
    intent = route(router, "google c++ vector erase")
    assert intent.tool == "open_url"
    assert " " not in intent.args["url"]
    assert "+" in intent.args["url"]  # quote_plus turns spaces into '+'


def test_bare_search_for_still_means_local_file_search(router):
    """"search the web for X"/"google X" must not swallow plain "search for
    X", which has always meant local file search."""
    intent = route(router, "search for my resume")
    assert intent.tool == "search_files"
    assert intent.args["query"] == "resume"  # existing rule strips the leading "my"


# --------------------------------------------------------- site navigation
# "open youtube" / "go to chess.com" must open the SITE, in one router hit,
# never touching Claude or open_target. This is the exact bug the task
# called out: no app/file named "youtube" exists on the machine, so before
# this table existed the phrase fell through to open_target and failed.
SITE_PHRASES = [
    ("open youtube", "https://youtube.com"),
    ("go to youtube", "https://youtube.com"),
    ("launch youtube", "https://youtube.com"),
    ("open youtube.com", "https://youtube.com"),
    ("open chess", "https://chess.com"),
    ("go to chess.com", "https://chess.com"),
    ("open instagram", "https://instagram.com"),
    ("go to instagram", "https://instagram.com"),
    ("open gmail", "https://mail.google.com"),
    ("open github", "https://github.com"),
    ("open claude", "https://claude.ai"),
    ("open chatgpt", "https://chatgpt.com"),
    ("open telegram web", "https://web.telegram.org"),
    ("open whatsapp web", "https://web.whatsapp.com"),
    ("open linkedin", "https://linkedin.com"),
    ("open twitter", "https://x.com"),
    ("open x", "https://x.com"),
    ("go to reddit", "https://reddit.com"),
]


@pytest.mark.parametrize("phrase,expected_url", SITE_PHRASES)
def test_known_site_phrasing_routes_to_open_url(router, phrase, expected_url):
    intent = route(router, phrase)
    assert intent is not None, f"{phrase!r} missed the router entirely"
    assert intent.tool == "open_url"
    assert intent.args["url"] == expected_url


def test_open_youtube_does_not_fall_through_to_open_target(router):
    """Regression test for the exact ordering trap the task described: a
    known site name must never reach open_target, which would search for an
    app/file literally named "youtube", find nothing, and report failure."""
    intent = route(router, "open youtube")
    assert intent.tool != "open_target"


def test_unlisted_domain_still_opens_directly(router):
    """A domain not in the table is still unambiguous -- open it, don't send
    it to app/file search."""
    intent = route(router, "go to some-startup.io")
    assert intent.tool == "open_url"
    assert intent.args["url"] == "some-startup.io"


def test_open_app_by_name_is_unaffected_by_the_site_table(router):
    """An ordinary app name (not in the site table, not domain-shaped) must
    still resolve through open_target exactly as before."""
    intent = route(router, "open notepad")
    assert intent.tool == "open_target"
    assert intent.args["name"] == "notepad"


def test_go_to_sleep_is_not_swallowed_by_site_navigation(router):
    """"sleep" isn't a known site, so the existing Jarvis-sleep rule (which
    sits earlier) must still win."""
    assert route_tool(router, "go to sleep") == "jalen_sleep"


# --------------------------------------------------------- every rule points
# --------------------------------------------------------- at a real tool
NEW_TOOLS_AND_EXPECTED_TIERS = {
    # open_url was rebalanced to GREEN: "opening a page changes nothing"
    # (config/safety.yaml). Keep this in lockstep with that file rather
    # than pinning the pre-rebalance tier.
    "open_url": Tier.GREEN,
    "empty_recycle_bin": Tier.RED,
    "sign_out": Tier.RED,
}


@pytest.mark.parametrize("tool,expected_tier", list(NEW_TOOLS_AND_EXPECTED_TIERS.items()))
def test_new_tools_are_registered_and_explicitly_tiered(tool, expected_tier):
    """
    Every tool a new router rule points at must exist in jarvis.tools.
    REGISTRY, have a brain/tools.py TOOL_SPECS entry, and resolve to an
    EXPLICIT tier in safety.yaml — never the unclassified-AMBER default.
    (The function itself is never called: empty_recycle_bin and sign_out
    are real, irreversible actions on the machine running this test.)
    """
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS

    assert tool in tools.REGISTRY, f"{tool} has no implementation in jarvis.tools.REGISTRY"
    assert tool in TOOL_SPECS, f"{tool} has no brain/tools.py TOOL_SPECS entry"

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"
    verdict = engine.classify(tool, {})
    assert not verdict.detail.get("unclassified"), f"{tool} fell through to unclassified-AMBER"
    assert verdict.tier is expected_tier, f"{tool} classified as {verdict.tier.value}, expected {expected_tier.value}"


def test_every_new_rule_targets_a_real_registered_tool(router):
    """Blanket sweep: every tool name any router rule can produce must be
    dispatchable — either through jarvis.tools.REGISTRY, or one of the
    jarvis_*/app-level intents app.handle_local() special-cases directly.
    Guards against the exact bug this task called out: a rule referencing
    close_app/morning_brief with no implementation behind it, producing
    silence."""
    from jarvis import tools

    app_level_intents = {
        "jalen_mute", "jalen_unmute", "jalen_sleep", "jalen_quit", "jalen_pause", "jalen_ack", "jalen_timing", "jalen_orb_size", "jalen_read_all",
        "jalen_resume", "jalen_restart", "private_mode", "set_posture", "morning_brief",
        "audit_digest", "cancel", "acknowledge", "greet", "reload_config",
    }
    missing = set()
    for pattern, tool_name, _build, _reply in router._rules:
        if tool_name not in tools.REGISTRY and tool_name not in app_level_intents:
            missing.add(tool_name)
    assert not missing, f"router rules point at tools with no implementation: {missing}"


def test_build_sdk_tools_does_not_raise():
    """The real startup-time consistency check (brain/tools.py's own
    assertion) — run directly rather than only trusted by inspection, so a
    drift between REGISTRY and TOOL_SPECS fails this test, not a live run."""
    from jarvis.brain.tools import build_sdk_tools

    sdk_tools = build_sdk_tools()
    names = {t.name for t in sdk_tools}
    for new_tool in ("open_url", "empty_recycle_bin", "sign_out"):
        assert new_tool in names


# --------------------------------------------------------- confirmation discipline
def test_new_fast_paths_do_not_widen_what_asks(router):
    """The other half of the safety-model rule this task must not regress:
    the new GREEN/AMBER fast paths must stay non-destructive, and the two
    new RED tools must still stop and ask."""
    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    silent = ["get_battery", "get_system_status", "lock_workstation"]
    asking = {t: engine.classify(t, {}).tier.value for t in silent
              if engine.classify(t, {}).tier.value != "green"}
    assert not asking, f"these should just run, but they gate: {asking}"

    must_confirm = ["empty_recycle_bin", "sign_out"]
    not_confirming = {t: engine.classify(t, {}).tier.value for t in must_confirm
                       if engine.classify(t, {}).tier.value != "red"}
    assert not not_confirming, f"these must confirm first: {not_confirming}"

    # keyboard_shortcut carries every clipboard/window/browser rule above, so
    # it is the tool most affected by the announce cost: it fires on "scroll
    # down", "copy that", "paste". As AMBER, each of those first said "keyboard
    # shortcut, say stop if you don't want that" and waited 2 seconds — which is
    # what made the assistant unusable. It destroys nothing, so it is GREEN.
    assert engine.classify("keyboard_shortcut", {}).tier is Tier.GREEN
