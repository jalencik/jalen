"""
The passphrase box, and why it shows what you type.

    "we gotta do something like that passwords should be visible the fact
     that it is invisible is so uncomfortable you know? maybe we gotta come
     up with some better approach"

He is right, and the discomfort is not irrational. Masking defends against
exactly one thing — somebody reading your screen. On a personal laptop, alone,
that threat is usually absent, while the cost is present every single time:
you cannot tell whether you fat-fingered a character, so you retype the whole
passphrase or guess.

So the choice is HIS, made visible, rather than a default with an invisible
rationale. Shown by default, one click to hide, and the honest consequence
printed next to the control.

WHAT DOES NOT CHANGE
--------------------
The protections that actually matter here are untouched, and this file exists
mainly to keep them that way. Visibility on screen is a different question
from whether the passphrase is transcribed, transmitted, or written down — and
loosening the first must never quietly loosen the others.
"""
from __future__ import annotations

import inspect

import pytest

from jalen.config import CONFIG
from jalen.tools import vault

SOURCE = inspect.getsource(vault._ask_passphrase_on_screen)


def test_it_is_visible_by_default():
    assert CONFIG.get_path("vault.hide_passphrase", False) is False
    assert 'hide_passphrase", False' in SOURCE, (
        "the default flipped back to masked - he asked for the opposite"
    )


def test_he_can_still_hide_it():
    """
    Visible by default is a default, not a rule. Someone in an office, on a
    call, or sharing a screen needs the other behaviour, and it has to be one
    click rather than a config file.
    """
    assert "Hide what I type" in SOURCE
    assert "Checkbutton" in SOURCE


def test_the_trade_off_is_shown_rather_than_assumed():
    """
    A control whose consequence is invisible is a control people click
    without deciding anything.
    """
    assert "Anyone looking at your" in SOURCE
    assert "screen can read it" in SOURCE


def test_it_counts_the_characters():
    """
    Even hidden, the count answers the question masking creates: "did that
    keystroke register?"
    """
    assert "character" in SOURCE
    assert "len(entry.get())" in SOURCE


def test_caps_lock_is_reported():
    """
    A masked box plus Caps Lock is a wrong passphrase with no visible cause,
    and people retype it three times before looking at the keyboard.
    """
    assert "CAPS LOCK IS ON" in SOURCE
    assert isinstance(vault.caps_lock_on(), bool)


# ---------------------------------------------------------------------------
# The invariants that visibility must not have loosened
# ---------------------------------------------------------------------------
def test_the_passphrase_is_never_returned_to_the_caller():
    """
    unlock_vault_prompt hands the passphrase straight into unlock_vault and
    lets the local name die with the frame. It is never a tool RESULT, because
    a tool result reaches the model, the transcript window and the audit log.
    """
    source = inspect.getsource(vault.unlock_vault_prompt)
    assert "del passphrase" in source
    assert "return passphrase" not in source


def test_it_still_says_what_it_does_not_do():
    assert "Never transcribed" in SOURCE
    assert "never logged" in SOURCE.lower()


def test_the_box_cannot_be_left_open_forever():
    """
    An unattended machine with a passphrase box waiting on it is a worse
    outcome than a timeout he has to repeat.
    """
    assert "root.after(int(timeout_s * 1000), cancel)" in SOURCE


def test_passphrase_is_still_a_redacted_argument():
    """
    It is the ONLY argument unlock_vault takes, and it was missing from the
    redaction list once already — which wrote the vault's master passphrase
    into a plain-text file sitting next to the vault.
    """
    from jalen.safety import SafetyEngine

    verdict = SafetyEngine(CONFIG).classify("unlock_vault",
                                            {"passphrase": "SUPERSECRET"})
    assert verdict.detail["args"]["passphrase"] == "***redacted***"


def test_the_typed_prompt_is_still_refused_to_content():
    """
    A passphrase box any web page can summon is a phishing primitive.
    """
    from jalen.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    assert engine.classify("unlock_vault_prompt", {}).tier is Tier.AMBER
    assert engine.classify("unlock_vault_prompt", {},
                           origin="content").tier is Tier.BLACK
