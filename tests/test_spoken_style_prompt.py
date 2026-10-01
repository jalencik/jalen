"""
How replies sound. The live QA of the real Jalen (2026-10-01, 55 requests in
typed mode) found replies that said a tool name aloud ("run
cleanup_suggestions"), an HTTP status ("just an empty 202 response"), their own
plumbing ("this list doesn't tell me", "that page is mostly navigation
boilerplate", "want me to ask it again?") and called him "Boss" in nearly every
reply.

These tests pin what the PROMPT says. They cannot show what the model does with
it: that needs the same 55 requests run again with the prompt in place, and
that has not been done (see SPOKEN_STYLE in jarvis/brain/agent.py).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.brain.agent import SPOKEN_STYLE, Brain  # noqa: E402
from jarvis.brain.tools import TOOL_SPECS  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402
from jarvis.safety import SafetyEngine  # noqa: E402


async def _noop(*_a, **_k):
    return True


@pytest.fixture(scope="module")
def prompt() -> str:
    return Brain(CONFIG, SafetyEngine(CONFIG), None, confirm=_noop, announce=_noop).system_prompt()


# ------------------------------------------------------------ the rules are in
RULES = [
    ("it is addressed to one listener", "read out loud to one person"),
    ("it wins over the sections above", "these rules win over anything above"),
    ("tool names are for the model only", "names of tools in this prompt are for you, never for him"),
    ("answer first", "The answer goes in the first sentence"),
    ("short", "Two or three sentences"),
    ("his name at most once", "at most ONCE in an answer"),
    ("not at every sentence", "Never in every sentence"),
    ("no stock openers", "Do not open with an acknowledgement"),
    ("no tool, function or file names", "Never say the name of a tool"),
    ("nothing that looks like code", "nothing with an underscore in it"),
    ("no status or error codes", "Never say a status code, an error code"),
    ("say what a code means", "Say what it means in a sentence"),
    ("no talk about its own machinery", "Do not talk about your own machinery"),
    ("no describing how a page is built", "Do not describe how a web page is built"),
    ("no offers to run a lookup again", "never offer to ask, run, try or check again"),
    ("a half-finished result is not an answer", "still working is not an answer"),
]


@pytest.mark.parametrize("why, needle", RULES, ids=[r[0] for r in RULES])
def test_the_prompt_carries_the_rule(prompt, why, needle):
    assert needle in prompt, f"the system prompt no longer says: {why}"


def test_the_section_is_the_last_thing_the_model_reads(prompt):
    """It claims to override the sections above, so nothing may follow it."""
    assert prompt.endswith(SPOKEN_STYLE)
    assert prompt.count("HOW YOU SOUND.") == 1


def test_the_failure_to_say_what_is_missing_is_about_the_thing_not_the_tools(prompt):
    section = prompt.split("WHEN SOMETHING WON'T WORK.", 1)[1].split("\n\n", 1)[0]
    assert "never about your tools" in section


# --------------------------------------------------------- his name, once
def test_the_persona_says_his_name_at_most_once_per_answer():
    style = str(CONFIG.get_path("persona.style", ""))
    assert "at most once in an answer" in style
    assert "once at the start or end of a thought" not in style, (
        "that wording produced his name in nearly every sentence"
    )
    # still interpolated: a second user's name must reach the prompt
    assert f'"{CONFIG.get_path("identity.address_user_as")}"' in style


def test_the_new_section_does_not_use_the_name_it_asks_for_less_of():
    address = str(CONFIG.get_path("identity.address_user_as", "Boss"))
    assert address not in SPOKEN_STYLE


# ------------------------------------------- it does not teach what it forbids
LEAKED = [
    "run cleanup_suggestions",
    "empty 202 response",
    "mostly navigation boilerplate",
    "want me to ask it again",
    "this list doesn't tell me",
    "doesn't tell me which one",
]


@pytest.mark.parametrize("phrase", LEAKED)
def test_none_of_the_leaked_sentences_is_in_the_prompt(prompt, phrase):
    assert phrase not in prompt.lower()


def test_the_section_names_no_tool(prompt):
    """A section that forbids saying tool names must not contain one. Tool
    names with an underscore only: a plain word that is also a tool name
    ("screenshot") is allowed to appear in a sentence."""
    spoken = SPOKEN_STYLE
    for name in TOOL_SPECS:
        if "_" in name:
            assert name not in spoken, f"the section that forbids tool names contains {name}"
    assert "_" not in re.sub(r"\s", "", spoken), "nothing that looks like code belongs in it"


def test_the_tool_names_the_model_needs_are_still_in_the_prompt(prompt):
    """The rule forbids SAYING them, not knowing them: the model chooses tools
    from this text and pinned tests rely on it."""
    for name in ("hand_off_to_cowork", "plan_folder_move", "web_search", "send_telegram_message",
                 "find_premium_emoji", "voice_guide"):
        assert name in prompt, name


# ---------------------------------- no existing test requires an old phrasing
def test_no_other_test_requires_one_of_the_old_phrasings():
    """The old wording was a habit, not a contract. If a test ever asserts it,
    this names the file so the rewrite is not quietly undone to satisfy it."""
    needles = ("ask it again", "navigation boilerplate", "doesn't tell me",
               "empty 202", "run cleanup_suggestions")
    offenders = []
    for path in (ROOT / "tests").glob("*.py"):
        if path.name in ("test_spoken_style_prompt.py",):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            if "assert" in low and any(n in low for n in needles):
                offenders.append(f"{path.name}:{number}")
    assert offenders == []


# ----------------------------------------------------- the safety fields stay
def test_the_four_safety_options_are_still_set_in_the_brain():
    """Not changed by this edit, and pinned in full by
    test_agent_sdk_configuration.py; this only proves the rewrite did not
    touch them."""
    source = (ROOT / "jarvis" / "brain" / "agent.py").read_text(encoding="utf-8")
    for needle in ("tools=[],", "setting_sources=[],", "skills=[],", 'permission_mode="bypassPermissions"'):
        assert needle in source, needle


# ---------------------------------------------------------------------------
# Round two: style rules must never override what the model DOES
# ---------------------------------------------------------------------------
def test_the_style_rules_never_override_the_do_not_resend_rules():
    """
    The independent re-check found SPOKEN_STYLE saying its rules win over
    anything above AND "if a second try could help, make it now" - a literal
    reading told the model to resend a post that came back Not confirmed, to a
    channel that has no confirmation. The retry rule is for reading only.
    """
    from jarvis.brain.agent import SPOKEN_STYLE

    assert "never change what you do" in SPOKEN_STYLE.lower().replace("\n", " ")
    assert "not confirmed" in SPOKEN_STYLE.lower()
    retry = SPOKEN_STYLE[SPOKEN_STYLE.index("For reading, searching"):]
    assert "sending, posting, moving, deleting" in retry
    assert "you do not repeat those on your own" in retry


def test_a_file_he_asked_about_may_be_named():
    from jarvis.brain.agent import SPOKEN_STYLE

    assert "a file or folder he asked about by name is fine to name" in SPOKEN_STYLE.lower().replace("\n", " ")
