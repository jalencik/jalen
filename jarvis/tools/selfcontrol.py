r"""
Jalen working on Jalen.

His ask: "Jarvis should be able to control itself, its vs code and test it
instead of me."

WHAT THAT MEANS IN PRACTICE
---------------------------
Every round of this project has ended the same way: a change is made, and
then a person has to open a terminal, remember the exact pytest incantation
with its three --ignore flags, wait two minutes, and read the tail. That is
the loop this closes. Jalen can now run its own suite, read its own
diagnostics, open its own source in VS Code, and say what broke — out loud,
without anyone typing.

THE ONE RULE: IT MAY LOOK AT ITSELF, NOT REWRITE ITSELF
-------------------------------------------------------
Everything here is read-only with respect to Jalen's own code. It runs
tests, it reads results, it opens an editor. It does NOT edit
jarvis/**.py, and there is a test asserting that.

That boundary is not squeamishness, it is the same rule the technician
already follows: diagnosis and repair are separated so that a diagnosis
cannot quietly become an unreviewed change. An assistant that edits its own
safety gate, its own audit log, or its own tier table — and then reports
that everything passes — has removed the only thing that would have told
anyone. If Jalen should change its own code, that goes through
start_coding_job in a git repo with a baseline, where the diff is visible.

WHY IT RUNS THE SUITE THE HARD WAY
----------------------------------
The full suite with the three benchmark files excluded, exactly as
HANDOFF.md documents it, because a self-test that runs a convenient subset
answers a question nobody asked. tests/test_voice_pipeline.py calls live
Groq and edge-tts and fails on a flaky network, so it is reported
SEPARATELY rather than dropped: "8 network tests also failed" and "8 tests
failed" mean completely different things, and collapsing them is how a real
regression gets waved through as "just the network".
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent

# The three benchmark files are excluded from the normal run because they
# measure latency rather than assert behaviour: they take minutes and their
# "failure" is a slow machine.
BENCHMARKS = (
    "tests/benchmark_latency.py",
    "tests/benchmark_open.py",
    "tests/benchmark_phrasing.py",
)
# Live network. Kept in the run, reported apart. See the module docstring.
NETWORK_TESTS = "tests/test_voice_pipeline.py"

FULL_SUITE_TIMEOUT_S = 900.0


def _python() -> str:
    """Jalen's OWN interpreter, not whatever `python` resolves to."""
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def _summarise(output: str) -> tuple[int, int, list[str]]:
    """(passed, failed, failing test names) from pytest's tail."""
    passed = failed = 0
    tail = output[-4000:]
    if m := re.search(r"(\d+) passed", tail):
        passed = int(m.group(1))
    if m := re.search(r"(\d+) failed", tail):
        failed = int(m.group(1))
    names = re.findall(r"^FAILED (\S+)", output, flags=re.M)
    return passed, failed, names


def run_own_tests(subset: str = "") -> str:
    """
    Run Jalen's own test suite and say what happened — GREEN.

    `subset` narrows it to one file, which takes seconds. Empty means the
    whole suite: 2,200 tests, about two and a half minutes.

    THAT DURATION IS THE DESIGN PROBLEM HERE, and it is why the whole-suite
    path goes through the background job machinery instead of blocking.
    Measured live: a subset returns in 2 seconds, the full suite in 140.
    Two and a half minutes of a voice assistant saying nothing is
    indistinguishable from a hang — it is precisely the "it starts and then
    suddenly stops" complaint in a new place. So the full run is started,
    acknowledged immediately, and announced when it finishes, exactly like a
    coding job.
    """
    args = [_python(), "-m", "pytest", "-q"]
    if subset.strip():
        target = subset.strip()
        if not target.startswith("tests"):
            target = f"tests/{target}"
        args.append(target)
    else:
        # The full suite goes to the background, so he is not left in
        # silence. Reuses the coding-job machinery rather than growing a
        # second one: it already tracks state across restarts and already
        # announces exactly once when something finishes.
        from . import devwork

        return devwork.start_background_run(
            args + ["tests/"] + [f"--ignore={b}" for b in BENCHMARKS],
            cwd=ROOT,
            label="my own test suite",
            summarise=_describe_run,
        )

    started = time.monotonic()
    try:
        result = subprocess.run(
            args, cwd=str(ROOT), capture_output=True, text=True,
            timeout=FULL_SUITE_TIMEOUT_S, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return (
            f"The test run was still going after "
            f"{FULL_SUITE_TIMEOUT_S / 60:.0f} minutes, so I stopped it. "
            "Something is hanging."
        )
    except OSError as exc:
        return f"I couldn't start the test run: {exc}"

    elapsed = time.monotonic() - started
    output = (result.stdout or "") + (result.stderr or "")
    return _describe_run(output, elapsed)


def _describe_run(output: str, elapsed: float = 0.0) -> str:
    """
    Turn pytest's output into a sentence worth hearing.

    Separated from run_own_tests so the BACKGROUND path can use the identical
    reporting — a self-test that describes its results differently depending
    on how it was started is a self-test nobody can compare across runs.
    """
    passed, failed, names = _summarise(output)
    took = f" Took {elapsed:.0f} seconds." if elapsed else ""

    if failed == 0 and passed:
        return f"All {passed} tests pass.{took}"
    if failed == 0 and not passed:
        return f"The test run produced no result. Last line: {output.strip()[-200:]}"

    # Network failures are reported apart, because "the network was flaky"
    # and "I broke something" are different findings and only one of them
    # needs him.
    network = [n for n in names if NETWORK_TESTS.split("/")[-1] in n]
    real = [n for n in names if n not in network]
    lines = [f"{failed} of {passed + failed} tests failed.{took}"]
    if real:
        lines.append(f"{len(real)} genuine failure(s):")
        lines += [f"  {n}" for n in real[:12]]
    if network:
        lines.append(
            f"{len(network)} of them are the live-network voice tests, which "
            "fail on a flaky connection. Worth re-running those alone before "
            "calling it a regression."
        )
    return "\n".join(lines)


def self_diagnose() -> str:
    """
    Run Jalen's own diagnostics — GREEN.

    The same thing `.\\jalen.ps1 check` prints: packages, models, microphone,
    credentials, disk, and what setup is still outstanding.
    """
    try:
        result = subprocess.run(
            [_python(), "run.py", "--check"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"I couldn't run my own diagnostics: {exc}"
    return (result.stdout or result.stderr or "").strip() or "No output."


def open_own_project() -> str:
    """Open Jalen's own source folder in VS Code — AMBER."""
    from .devwork import open_in_vscode

    return open_in_vscode(str(ROOT))


def own_health() -> str:
    """
    A short spoken-length answer to "are you alright" — GREEN.

    Deliberately not the full diagnostic: this is the version that fits in
    two sentences out loud. self_diagnose is the one to read on screen.
    """
    import shutil

    from .. import crashlog

    free_gb = shutil.disk_usage(str(ROOT)).free / 1e9
    parts = [f"{free_gb:.1f} GB free"]

    record = crashlog.previous_exit()
    if record and record.get("state") == "running":
        parts.append("my last run ended without shutting down properly")
    elif record:
        parts.append(f"last stop was clean ({record.get('reason')})")

    try:
        from .. import tools as registry

        parts.append(f"{len(registry.REGISTRY)} tools loaded")
    except Exception:
        pass

    if free_gb < 1.0:
        parts.append("that disk figure is a problem and will break things")
    return "; ".join(parts) + "."


REGISTRY: dict[str, Any] = {
    "run_own_tests": run_own_tests,
    "self_diagnose": self_diagnose,
    "open_own_project": open_own_project,
    "own_health": own_health,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
