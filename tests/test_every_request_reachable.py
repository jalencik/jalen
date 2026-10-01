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
    ("find_premium_emoji", "green", "premium emoji ids for a post, by character or name"),
    ("list_sticker_packs", "green", "which emoji sets and sticker packs he has"),
    ("send_sticker", "red", "a sticker as its own message"),
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
    # Handing real work to a coding agent and being there when it finishes.
    ("start_coding_job", "amber", "give Claude Code an hour-long job"),
    ("review_coding_job", "green", "judge what the agent actually changed"),
    ("list_coding_jobs", "green", "what is the agent working on"),
    ("init_git_repo", "amber", "a baseline so the diff is checkable"),
    ("open_in_vscode", "amber", "open a project in VS Code"),
    # Jalen working on Jalen.
    ("run_own_tests", "green", "test yourself"),
    ("self_diagnose", "green", "diagnose yourself"),
    ("own_health", "green", "are you alright"),
    # Typing the vault passphrase instead of saying it aloud.
    ("unlock_vault_prompt", "amber", "unlock without speaking the passphrase"),
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
    ("play we are the people", "play_media"),
    ("play timeless", "play_media"),
    # Sizing the orb with his hands, which he asked for twice with a photo.
    # Both directions are listed: the OFF phrase contains the ON phrase, so a
    # rule reordering would silently leave him with a camera he cannot switch
    # off by voice, and that is a capability going missing, not a threshold
    # changing.
    # "It is still not working on me." The camera view is the answer, and it
    # has to be askable out loud rather than found in a script name.
    # Jalen testing itself, which is the whole point of the self-control
    # tools: the loop that used to need a person and a terminal.
    ("test yourself", "run_own_tests"),
    ("are you ok", "own_health"),
    ("diagnose yourself", "self_diagnose"),
    ("list coding jobs", "list_coding_jobs"),
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


def test_bare_words_do_not_hijack_a_follow_up():
    """
    The orb-size rules that used to sit here are gone, but the hazard they
    guarded against is not: the follow-up window is open after every reply,
    so any rule matching a bare adjective would swallow an answer meant for
    something else.

    These are the words most likely to be said in answer to a question.
    """
    r = IntentRouter(CONFIG)
    for bare in ("bigger", "smaller", "normal", "huge", "the first one",
                 "the second one", "that one"):
        hit = r.route(bare)
        assert hit is None or hit.tool in ("cancel", "jalen_ack"), (
            f"a bare {bare!r} routes to {hit.tool} - it will eat follow-ups"
        )


def test_the_camera_is_not_armed_by_default():
    """
    A webcam that switches itself on because a shipped config said so is not
    a feature anyone asked for. If this ever flips to true, it was an
    accident.
    """
    assert CONFIG.get_path("ui.hand_gestures.enabled", False) is False


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
