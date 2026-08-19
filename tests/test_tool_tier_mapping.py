"""
Every Phase C tool (desktop.py + filesystem.py) must resolve to the tier
its own docstring claims, and must be an EXPLICIT entry in safety.yaml —
never fall through to the unclassified-AMBER default, which would mean a
tool exists and is callable but nobody actually decided its tier. This is
the tier column of the handoff's "full tool inventory table" as a test
rather than only prose.

paranoid_first_week is on by default (config/jarvis.yaml), which promotes
AMBER to RED — real and correct behaviour, but it would make every AMBER
assertion here look like a RED one unless turned off first.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import CONFIG  # noqa: E402
from jarvis.safety import SafetyEngine, Tier  # noqa: E402

EXPECTED_TIERS = {
    # filesystem.py
    "read_file": Tier.GREEN,
    "list_directory": Tier.GREEN,
    "search_files": Tier.GREEN,
    "create_file": Tier.AMBER,
    "edit_file": Tier.AMBER,
    "move_file": Tier.AMBER,
    "rename_file": Tier.AMBER,
    "copy_file": Tier.AMBER,
    "create_folder": Tier.AMBER,
    "delete_file": Tier.RED,
    # desktop.py
    "get_window_list": Tier.GREEN,
    "read_screen": Tier.GREEN,
    "click_element": Tier.AMBER,
    "type_text": Tier.AMBER,
    "keyboard_shortcut": Tier.AMBER,
}


@pytest.fixture
def engine():
    eng = SafetyEngine(CONFIG)
    eng.paranoid = False
    eng.posture = "irreversible_only"
    return eng


@pytest.mark.parametrize("tool,expected_tier", list(EXPECTED_TIERS.items()))
def test_tool_resolves_to_its_documented_tier(engine, tool, expected_tier):
    verdict = engine.classify(tool, {})
    assert not verdict.detail.get("unclassified"), (
        f"{tool} isn't in safety.yaml at all — it fell through to the "
        f"unclassified-AMBER default instead of an explicit tier"
    )
    assert verdict.tier is expected_tier, (
        f"{tool} classified as {verdict.tier.value}, expected {expected_tier.value}"
    )


def test_every_desktop_and_filesystem_tool_is_covered_by_this_table():
    from jarvis.tools import desktop, filesystem

    all_tools = set(desktop.REGISTRY) | set(filesystem.REGISTRY)
    assert all_tools == set(EXPECTED_TIERS), (
        f"mismatch between registered tools and this test's coverage: "
        f"missing from table={all_tools - set(EXPECTED_TIERS)}, "
        f"stale in table={set(EXPECTED_TIERS) - all_tools}"
    )
