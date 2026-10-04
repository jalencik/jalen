"""
web_sign_in had no tier at all.

CLAUDE.md: "Adding a tool is three edits: the module's REGISTRY, TOOL_SPECS
... and a tier in config/safety.yaml. The first two hard-fail at startup
when they drift; the third fails at nothing and silently degrades to
unclassified-AMBER." web_sign_in is the one that did: in TOOL_SPECS, in
REGISTRY, in no tier. Every "sign me in" was logged as an unclassified
action and announced like an overwrite.

Nothing checked the third edit, so nothing will catch the next one either.
Now this does: every tool the brain can call has exactly one tier. And a
tool listed in TWO tiers is its own silent failure - SafetyEngine builds its
tier map GREEN, AMBER, RED, BLACK in that order, so the later one wins
without a word.
"""
from __future__ import annotations

import collections

import yaml

from jalen.config import CONFIG
from jalen.safety import SafetyEngine, Tier

_Y = yaml.safe_load(open("config/safety.yaml", encoding="utf-8"))


def _tiers_of() -> dict[str, list[str]]:
    where: dict[str, list[str]] = collections.defaultdict(list)
    for tier in ("green", "amber", "red", "black"):
        for tool in (_Y.get(tier) or {}).get("tools") or []:
            where[tool].append(tier)
    return where


def test_every_tool_the_brain_can_call_has_a_tier():
    from jalen.brain.tools import TOOL_SPECS

    where = _tiers_of()
    untiered = sorted(t for t in TOOL_SPECS if t not in where)
    assert not untiered, (
        f"{untiered} would run as unclassified-AMBER - add a tier in "
        "config/safety.yaml")


def test_no_tool_is_in_two_tiers():
    doubled = {t: v for t, v in _tiers_of().items() if len(v) > 1}
    assert not doubled, f"the later tier wins silently: {doubled}"


def test_signing_in_is_green_when_he_asks_and_refused_when_a_page_asks():
    """
    GREEN, not AMBER: AMBER is "deliberately tiny" - things that overwrite
    content or reach other people - and signing into his own account does
    neither; the password box is already a human step. But a page must not
    be able to start it, so it is on refuse_from_content too.
    """
    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"
    mine = engine.classify("web_sign_in", {"agent": "chatgpt"}, origin="user")
    assert mine.tier is Tier.GREEN
    assert not mine.detail.get("unclassified")
    assert engine.classify("web_sign_in", {"agent": "chatgpt"},
                           origin="content").tier is Tier.BLACK
