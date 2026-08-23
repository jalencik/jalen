"""
Jalen working on Jalen.

His ask: "Jarvis should be able to control itself, its vs code and test it
instead of me."

The capability is small; the BOUNDARY around it is the part worth testing.
An assistant that can run its own tests is useful. One that can also edit
its own safety gate, its own tier table or its own audit log — and then
report that everything passes — has removed the only thing that would have
told anyone it had. That is the same look/touch split the technician
already follows, and for the same reason.

So: it may run tests, read diagnostics and open an editor. It may not write
to its own source. If Jalen should change its own code, that goes through
start_coding_job in a git repo with a baseline, where the diff is visible.
"""
from __future__ import annotations

import inspect

import pytest

from jarvis.tools import selfcontrol


# ---------------------------------------------------------------------------
# THE BOUNDARY.
# ---------------------------------------------------------------------------
def test_it_cannot_write_to_its_own_source():
    """
    Read-only with respect to Jalen's own code. Checked structurally,
    because the failure this prevents is one nobody would notice: a self-test
    that quietly fixed its own failing assertion would report green forever.
    """
    source = inspect.getsource(selfcontrol)
    for forbidden in ("write_text(", "open(", ".unlink(", "shutil.copy",
                      "shutil.move", "os.remove", "rmtree"):
        assert forbidden not in source, (
            f"selfcontrol.py contains {forbidden!r} — it is supposed to be "
            "read-only with respect to Jalen's own code"
        )


def test_every_exported_tool_is_read_only_or_opens_an_editor():
    """
    Enumerated deliberately. A new tool added here without thinking about
    the boundary should fail this rather than slip through.
    """
    assert set(selfcontrol.REGISTRY) == {
        "run_own_tests", "self_diagnose", "open_own_project", "own_health",
    }


def test_editing_its_own_code_goes_through_a_reviewable_path():
    """
    There IS a way for Jalen to change its own code — start_coding_job, in a
    git repo, against a recorded baseline, with review_coding_job comparing
    the diff to what was asked. The point is that it is the only way, and
    that it leaves evidence.
    """
    from jarvis import tools

    assert "start_coding_job" in tools.REGISTRY
    assert "review_coding_job" in tools.REGISTRY
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    # And it is announced, not silent.
    assert SafetyEngine(CONFIG).classify("start_coding_job", {}).tier.value == "amber"


# ---------------------------------------------------------------------------
# Reading the result honestly.
# ---------------------------------------------------------------------------
def test_a_clean_run_is_reported_plainly():
    passed, failed, names = selfcontrol._summarise("... 2248 passed in 142.00s")
    assert (passed, failed, names) == (2248, 0, [])


def test_failures_are_counted_and_named():
    output = (
        "FAILED tests/test_safety.py::test_black_is_black\n"
        "FAILED tests/test_orb.py::test_idle_colour_is_visible\n"
        "2 failed, 2246 passed in 140.00s"
    )
    passed, failed, names = selfcontrol._summarise(output)
    assert passed == 2246 and failed == 2
    assert "tests/test_safety.py::test_black_is_black" in names


def test_network_failures_are_reported_separately():
    """
    "the network was flaky" and "I broke something" are different findings,
    and only one of them needs him. Collapsing them is how a real regression
    gets waved through as "just the voice tests again".
    """
    class Result:
        stdout = (
            "FAILED tests/test_voice_pipeline.py::test_wake_word_fires\n"
            "1 failed, 2247 passed in 142.00s"
        )
        stderr = ""

    out = selfcontrol._describe_run(Result.stdout, 142.0)
    assert "live-network" in out
    assert "re-running those alone" in out
    assert "genuine failure" not in out


def test_a_genuine_failure_is_not_excused_as_network():
    class Result:
        stdout = (
            "FAILED tests/test_safety.py::test_injection_guard_ordering\n"
            "1 failed, 2247 passed in 140.00s"
        )
        stderr = ""

    out = selfcontrol._describe_run(Result.stdout, 140.0)
    assert "1 genuine failure" in out
    assert "test_injection_guard_ordering" in out


def test_both_kinds_at_once_are_told_apart():
    class Result:
        stdout = (
            "FAILED tests/test_safety.py::test_real_bug\n"
            "FAILED tests/test_voice_pipeline.py::test_flaky\n"
            "2 failed, 2246 passed in 141.00s"
        )
        stderr = ""

    out = selfcontrol._describe_run(Result.stdout, 141.0)
    assert "1 genuine failure" in out
    assert "test_real_bug" in out
    assert "1 of them are the live-network" in out


def test_a_hang_is_reported_rather_than_waited_out(monkeypatch):
    def boom(*a, **k):
        raise selfcontrol.subprocess.TimeoutExpired(cmd="pytest", timeout=900)

    monkeypatch.setattr(selfcontrol.subprocess, "run", boom)
    # A named subset, because that is the path that blocks and therefore the
    # only one that can time out. The background path cannot hang a turn.
    out = selfcontrol.run_own_tests("test_orb.py")
    assert "still going" in out and "hanging" in out


def test_a_run_that_produces_nothing_says_so():
    """
    Silence must not read as success. A pytest that failed to start prints
    neither "passed" nor "failed", and the naive summariser would call that
    zero failures.
    """
    class Result:
        stdout = "ERROR: file or directory not found: tests/"
        stderr = ""

    out = selfcontrol._describe_run(Result.stdout)
    assert "no result" in out


# ---------------------------------------------------------------------------
# Running the real thing.
# ---------------------------------------------------------------------------
def test_it_runs_the_documented_suite_not_a_convenient_subset():
    """
    A self-test that quietly skips half the suite answers a question nobody
    asked. It excludes exactly the three benchmark files HANDOFF.md excludes,
    and nothing else.
    """
    source = inspect.getsource(selfcontrol.run_own_tests)
    assert "--ignore=" in source
    assert set(selfcontrol.BENCHMARKS) == {
        "tests/benchmark_latency.py",
        "tests/benchmark_open.py",
        "tests/benchmark_phrasing.py",
    }
    assert selfcontrol.NETWORK_TESTS not in selfcontrol.BENCHMARKS, (
        "the network tests are excluded from the run instead of reported apart"
    )


def test_it_uses_its_own_interpreter():
    """
    A fresh terminal's `python` is a different install with none of this
    project's packages — the exact failure jalen.ps1 exists to prevent.
    """
    assert ".venv" in selfcontrol._python() or selfcontrol._python().endswith("python.exe")


@pytest.mark.slow
def test_it_can_actually_run_a_real_subset():
    """The genuine article, on a small file, so it stays quick."""
    out = selfcontrol.run_own_tests("test_orb.py")
    assert "pass" in out.lower(), out


def test_health_is_short_enough_to_say_out_loud():
    """
    Questions about itself are the one topic where it reliably over-explains,
    and out loud that is the worst possible topic for it.
    """
    out = selfcontrol.own_health()
    assert out and len(out) < 220, f"{len(out)} chars is a paragraph: {out}"


def test_health_mentions_a_dirty_previous_shutdown(monkeypatch):
    from jarvis import crashlog

    monkeypatch.setattr(
        crashlog, "previous_exit",
        lambda: {"state": "running", "pid": 123, "session_id": "abc"},
    )
    assert "without shutting down" in selfcontrol.own_health()


# ---------------------------------------------------------------------------
# Reachability.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name, tier", [
    ("run_own_tests", "green"),
    ("self_diagnose", "green"),
    ("own_health", "green"),
    ("open_own_project", "amber"),
])
def test_the_tools_are_dispatchable_and_gated(name, tier):
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    assert name in tools.REGISTRY
    assert name in TOOL_SPECS
    assert SafetyEngine(CONFIG).classify(name, {}).tier.value == tier


def test_something_it_read_cannot_make_it_open_its_own_source():
    """
    AMBER is refused to content-derived requests, so an email saying "open
    your source folder" gets nowhere.
    """
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    verdict = SafetyEngine(CONFIG).classify("open_own_project", {}, origin="content")
    assert verdict.tier.value == "black"


# ---------------------------------------------------------------------------
# The full suite must not be run inside a turn.
# ---------------------------------------------------------------------------
def test_the_full_suite_runs_in_the_background(monkeypatch):
    """
    Measured live: a subset returns in 2 seconds, the full suite in 140.
    Two and a half minutes of a voice assistant saying nothing is
    indistinguishable from a hang - it is the "it starts and then suddenly
    stops" complaint in a new place.
    """
    started = {}

    def fake_start(args, cwd, label, summarise=None):
        started["args"] = args
        started["label"] = label
        started["summarise"] = summarise
        return "Running it in the background."

    from jarvis.tools import devwork

    monkeypatch.setattr(devwork, "start_background_run", fake_start)
    out = selfcontrol.run_own_tests()
    assert "background" in out
    assert started["summarise"] is selfcontrol._describe_run, (
        "the background run would report differently from the blocking one"
    )
    # And it still runs the documented suite, not a convenient subset.
    joined = " ".join(started["args"])
    for benchmark in selfcontrol.BENCHMARKS:
        assert f"--ignore={benchmark}" in joined


def test_a_named_subset_still_runs_inline(monkeypatch):
    """
    Seconds, not minutes, so blocking is right - and an immediate answer is
    what makes "check the orb tests" worth asking.
    """
    class Result:
        stdout = "30 passed in 2.00s"
        stderr = ""

    monkeypatch.setattr(selfcontrol.subprocess, "run", lambda *a, **k: Result())
    out = selfcontrol.run_own_tests("test_orb.py")
    assert "All 30 tests pass" in out


def test_both_paths_describe_a_result_identically():
    """
    A self-test that words its findings differently depending on how it was
    started is one nobody can compare across runs.
    """
    output = "FAILED tests/test_safety.py::test_x\n1 failed, 10 passed in 3.00s"
    inline = selfcontrol._describe_run(output, 3.0)
    background = selfcontrol._describe_run(output)
    assert "genuine failure" in inline and "genuine failure" in background
    assert inline.replace(" Took 3 seconds.", "") == background
