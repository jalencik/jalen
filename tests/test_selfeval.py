"""
The weakness log: Jalen writing down what he cannot do, so that using him
and improving him become the same activity.

The design pressure these tests encode:

  * A log that appends unconditionally becomes forty copies of one sentence
    inside a week, and forty copies of one problem READ as forty problems.
    Repeats must count, not duplicate.
  * The record must survive two turns writing at once — MAX_IN_FLIGHT_TURNS
    is 2, and each runs on its own thread.
  * It must name the missing capability, not the excuse, because the file is
    read weeks later by someone deciding what to build.
"""
from __future__ import annotations

import threading

import pytest

from jarvis.tools import selfeval


@pytest.fixture(autouse=True)
def temp_log(tmp_path, monkeypatch):
    """Never touch the real data/weaknesses.md — it is a durable record."""
    path = tmp_path / "weaknesses.md"
    monkeypatch.setattr(selfeval, "WEAKNESS_PATH", path)
    return path


def test_nothing_recorded_yet_says_so():
    assert "Nothing recorded yet" in selfeval.review_weaknesses()


def test_a_weakness_is_written_in_readable_markdown(temp_log):
    selfeval.log_weakness(
        missing="No tool can change a Windows service's startup type.",
        asked="make the wifi service start later",
        happened="said so and stopped",
    )
    text = temp_log.read_text(encoding="utf-8")
    assert "## No tool can change a Windows service's startup type." in text
    assert "- Asked: make the wifi service start later" in text
    assert "- Seen: 1 time" in text
    assert text.startswith("# What Jalen cannot do yet")


def test_the_same_weakness_counts_instead_of_duplicating(temp_log):
    """
    The single most important behaviour here. Without it the file becomes
    unreadable within a week and stops being consulted, which makes the
    whole feature worthless.
    """
    for _ in range(4):
        selfeval.log_weakness(missing="No tool can read a scanned PDF.")

    text = temp_log.read_text(encoding="utf-8")
    assert text.count("## No tool can read a scanned PDF.") == 1
    assert "- Seen: 4 times" in text


@pytest.mark.parametrize(
    "variant",
    [
        "No tool can read a scanned PDF",
        "no tool can read a scanned pdf.",
        "No tool can read a scanned PDF!",
    ],
)
def test_near_identical_phrasings_collapse(temp_log, variant):
    """
    The model will not phrase it byte-identically every time. Punctuation
    and case must not create a second entry for the same gap.
    """
    selfeval.log_weakness(missing="No tool can read a scanned PDF.")
    selfeval.log_weakness(missing=variant)
    text = temp_log.read_text(encoding="utf-8")
    assert text.lower().count("no tool can read a scanned pdf") == 1
    assert "- Seen: 2 times" in text


def test_different_weaknesses_stay_separate(temp_log):
    selfeval.log_weakness(missing="No tool can read a scanned PDF.")
    selfeval.log_weakness(missing="No tool can change a Windows service.")
    report = selfeval.review_weaknesses()
    assert "2 things I can't do yet" in report


def test_an_empty_weakness_is_refused(temp_log):
    """A blank entry is noise in a file whose only value is signal."""
    result = selfeval.log_weakness(missing="   ")
    assert "Nothing recorded" in result
    assert not temp_log.exists()


def test_the_report_ranks_by_how_often_it_comes_up(temp_log):
    """
    The count is the only honest way to decide what to build next, so the
    thing that blocks him most must be read out first.
    """
    selfeval.log_weakness(missing="Rare thing.")
    for _ in range(5):
        selfeval.log_weakness(missing="Constant thing.")
    selfeval.log_weakness(missing="Occasional thing.")
    selfeval.log_weakness(missing="Occasional thing.")

    report = selfeval.review_weaknesses()
    lines = [line for line in report.splitlines() if line.startswith("  - ")]
    assert "Constant thing." in lines[0] and "5 times" in lines[0]
    assert "Occasional thing." in lines[1]
    assert "Rare thing." in lines[2] and "once" in lines[2]


def test_the_report_is_bounded_and_says_what_it_left_out(temp_log):
    for i in range(9):
        selfeval.log_weakness(missing=f"Gap number {i}.")
    report = selfeval.review_weaknesses(limit=3)
    assert report.count("  - ") == 3
    assert "6 more" in report


def test_concurrent_turns_do_not_corrupt_the_file(temp_log):
    """
    Two turns can be in flight at once and each runs on its own thread. An
    unguarded read-modify-write would interleave and lose entries — or worse,
    write half a file.
    """
    def writer(n: int) -> None:
        for i in range(10):
            selfeval.log_weakness(missing=f"Gap {n}-{i}.")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = temp_log.read_text(encoding="utf-8")
    assert text.count("## Gap ") == 40, "entries were lost to a race"
    assert text.startswith("# What Jalen cannot do yet"), "the header was clobbered"


# ------------------------------------------------------- reachable at all
def test_the_tools_are_registered_and_classified():
    """
    A tool the brain cannot call, or one that is unclassified and therefore
    silently AMBER, is a tool that does not work. log_weakness announcing
    itself and waiting two seconds would be absurd.
    """
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    for name in ("log_weakness", "review_weaknesses"):
        assert name in tools.REGISTRY, f"{name} is not dispatchable"
        assert name in TOOL_SPECS, f"{name} is invisible to the brain"
        assert engine.classify(name, {}).tier is Tier.GREEN


def test_asking_out_loud_does_not_cost_a_claude_turn():
    """Reading a local file must not require the brain."""
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    router = IntentRouter(CONFIG)
    for phrase in [
        "what can't you do",
        "what cant you do yet",
        "what are your gaps",
        "what are your weaknesses",
        "show me your weaknesses",
        "jalen what can't you do",
    ]:
        hit = router.route(phrase)
        assert hit is not None and hit.tool == "review_weaknesses", f"{phrase!r} missed"
