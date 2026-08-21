"""
Every capability he asked for, still reachable.

Not a unit test — a coverage ledger. Each row is a thing he asked for
somewhere in the build, checked by REACHABILITY: the tool is registered, has
a spec the brain can see, carries a tier, and where he'd say it out loud,
routes. A module nothing can call is not a feature, and this file exists
because that happened twice: run.py's imports broke while 1,200 tests passed,
and the vault shipped with no way to type anything it held.

When a row here fails, a capability has gone missing. That is different from
an ordinary test failure and should be read that way.
"""
from __future__ import annotations

import pytest

from jarvis import tools
from jarvis.brain.router import IntentRouter
from jarvis.brain.tools import TOOL_SPECS
from jarvis.config import CONFIG
from jarvis.safety import SafetyEngine


@pytest.fixture(scope="module")
def router() -> IntentRouter:
    return IntentRouter(CONFIG)


@pytest.fixture(scope="module")
def engine() -> SafetyEngine:
    return SafetyEngine(CONFIG)


# Tools that must exist, be visible to the brain, and carry the right tier.
CAPABILITIES = [
    ("diagnose", "green", "technician: find what's wrong"),
    ("diagnose_wifi", "green", "technician: wifi dropouts"),
    ("fix_wifi_power_saving", "amber", "technician: repair"),
    ("temp_file_report", "green", "technician: measure temp files"),
    ("clear_temp_files", "red", "technician: delete temp files"),
    ("scan_inbox", "green", "read 100+ emails in one call"),
    ("telegram_unread", "green", "what did I miss"),
    ("save_telegram_draft", "green", "post as a draft"),
    ("send_telegram_message", "red", "telegram X saying Y"),
    ("send_telegram_file", "red", "send a file to Telegram"),
    ("draft_email_with_file", "green", "attach a file to an email"),
    ("community_post_guide", "green", "the channel post format"),
    ("voice_guide", "green", "write in his voice"),
    ("save_draft_text", "green", "essays to a file and clipboard"),
    ("send_posts", "red", "many posts in one call"),
    ("unlock_vault", "red", "credentials vault"),
    ("list_secrets", "green", "what's stored, names only"),
    ("site_permission", "green", "may I fill this site"),
    ("remember_site_decision", "red", "once or always, remembered"),
    ("fill_credential", "amber", "type a secret into a form"),
    ("fill_field", "green", "type ordinary text into a form"),
    ("current_page_url", "green", "which page is this"),
    ("ask_user", "green", "ask him and wait"),
    ("list_browser_tabs", "green", "which browser windows are open"),
    ("close_browser_tab", "amber", "close the YouTube window"),
    ("hand_off_to_cowork", "amber", "hand this task to cowork"),
    ("hand_off_to_code", "amber", "hand this off to code"),
    ("log_weakness", "green", "what it cannot do"),
    ("review_weaknesses", "green", "read the weakness log"),
    ("play_on_youtube", "green", "play a real video"),
]


@pytest.mark.parametrize("name, tier, what", CAPABILITIES)
def test_capability_is_reachable(engine, name, tier, what):
    assert name in tools.REGISTRY, f"{what}: {name} is not dispatchable"
    assert name in TOOL_SPECS, f"{what}: {name} is invisible to the brain"
    assert engine.classify(name, {}).tier.value == tier, f"{what}: wrong tier"


# Things he says out loud, and what they must reach.
SPOKEN = [
    ("jalen quit", "jalen_quit"),
    ("hey jarvis quit", "jalen_quit"),
    ("what time is it", "get_time"),
    ("tell me the time", "get_time"),
    ("my wifi keeps dropping", "diagnose_wifi"),
    ("what's wrong with my computer", "diagnose"),
    ("run a diagnostic", "diagnose"),
    ("clear the temp files", "clear_temp_files"),
    ("telegram sat talk saying hello", "send_telegram_message"),
    ("what did I miss", "telegram_unread"),
    ("go to youtube and play dreamcore", "play_on_youtube"),
    ("read it all", "jalen_read_all"),
    ("close the youtube window", "close_browser_tab"),
    ("what tabs are open", "list_browser_tabs"),
    ("make the orb bigger", "jalen_orb_size"),
    ("how fast was that", "jalen_timing"),
    ("what can't you do", "review_weaknesses"),
    ("open chrome", "open_target"),
    ("close the window", "keyboard_shortcut"),
]


@pytest.mark.parametrize("phrase, expected", SPOKEN)
def test_spoken_command_still_routes(router, phrase, expected):
    hit = router.route(phrase)
    assert hit is not None, f"{phrase!r} now falls through to Claude"
    assert hit.tool == expected, f"{phrase!r} -> {hit.tool}, expected {expected}"


CONFIGURED = [
    ("identity.name", "Jalen"),
    ("identity.wake_word", "hey jalen"),
    ("ui.orb_position", "center"),
]


@pytest.mark.parametrize("path, expected", CONFIGURED)
def test_configuration_holds(path, expected):
    assert str(CONFIG.get_path(path)) == expected


def test_both_wake_models_are_configured():
    """He asked for both names everywhere, and the acoustic layer needs two."""
    models = CONFIG.get_path("wake.model")
    joined = " ".join(models if isinstance(models, list) else [str(models)]).lower()
    assert "jalen" in joined and "jarvis" in joined


def test_the_orb_is_big_enough_to_see():
    assert int(CONFIG.get_path("ui.orb_size", 84)) >= 300


def test_the_handoff_jargon_is_in_the_prompt():
    """
    Neither phrase is routed — "this task" is the conversation, which only
    the brain has. So the prompt is the only place it can live.
    """
    from jarvis.brain.agent import Brain

    async def noop(*a, **k):
        return True

    prompt = Brain(
        CONFIG, SafetyEngine(CONFIG), None, confirm=noop, announce=noop
    ).system_prompt().lower()
    assert "hand_off_to_cowork" in prompt
    assert "hand_off_to_code" in prompt
    assert "cloudcork" in prompt, "the mis-transcription is no longer explained"
