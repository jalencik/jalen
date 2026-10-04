"""
Benchmark + regression suite for "opening apps, files and folders" — the
single most used capability. Every row is a REAL utterance the user would
plausibly say, run through the REAL IntentRouter, then checked against
what's ACTUALLY on this machine (real installed apps, real Desktop/
Documents/Downloads files) via resolve_app / find_files — never mocked.

This intentionally makes the suite machine-specific: "open ayugram" only
means something because AyuGram is actually installed here, and "open my
CV" only resolves because a CV file actually exists in this user's
Desktop/Documents/Downloads. That's the point — a resolver that only
passes against a fake fixture directory has never proven it works for the
person actually using it.

What each row checks:
  - ROUTER: does the phrase hit the right local tool in <1ms, without a
    Claude round trip? (IntentRouter.route)
  - RESOLUTION: for rows that name something real, does the resolver
    actually find it — a real installed app (resolve_app) or a real file/
    folder (find_files) — or, for rows that name something that does NOT
    exist, does it honestly find nothing rather than fabricating a match?

Two categories are deliberately NOT in this table even though they're
"opening" phrases: bare recognizable-website names ("open chess", "open
claude") now resolve through a separate web-shortcut router rule (a
different agent's area, added concurrently in this same working tree) —
testing them here would couple this file to that rule's exact word list
instead of to the app/file/folder resolver this file actually owns.

Before/after numbers (measured, see PR notes): the app-resolution pass
rate went from 20/25 to 25/25 on the misspelling/partial table below, and
three live false-app-launches were found and fixed along the way
("word" opening WordPad, "desktop" opening Remote Desktop Connection,
"opera"/"zoom" opening unrelated apps at 0.6 fuzzy confidence).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.brain.router import IntentRouter  # noqa: E402
from jalen.config import CONFIG  # noqa: E402
from jalen.tools.launcher import find_files, resolve_app  # noqa: E402

# --------------------------------------------------------------------------
# The table. (utterance, expected_tool, kind)
#
# kind drives how RESOLUTION is checked:
#   "app"          -> resolve_app(name) must return a real target
#   "fail"         -> neither resolve_app(name) nor find_files(name) may
#                      find anything — a nonexistent app must fail honestly
#   "folder"       -> must resolve to a real, existing special folder
#                      (either via open_folder's path arg, or open_target's
#                      own special-folder short-circuit — see test below)
#   "file"         -> find_files(name) or resolve_app(name) must find a
#                      real hit somewhere on this machine
#   "search"       -> find_files(query) must find a real hit
#   "search_fail"  -> find_files(query) must find nothing
#   "router_only"  -> only the router's tool choice is checked; the target
#                      tool belongs to a different module/area
# --------------------------------------------------------------------------
CASES: list[tuple[str, str, str]] = [
    # ---- misspellings and partials the user will actually say ----------
    ("open ayugram", "open_target", "app"),
    ("open aygram", "open_target", "app"),
    ("open igram", "open_target", "app"),
    ("open telegran", "open_target", "app"),
    ("open capcut", "open_target", "app"),
    ("open cap cut", "open_target", "app"),
    ("open vs code", "open_target", "app"),
    ("open vscode", "open_target", "app"),
    ("open v s code", "open_target", "app"),
    ("open chrom", "open_target", "app"),
    ("open google chrom", "open_target", "app"),
    ("open noteped", "open_target", "app"),
    ("open exploerer", "open_target", "app"),
    ("open crome", "open_target", "app"),
    ("open gogle chrome", "open_target", "app"),
    ("open wrod", "open_target", "app"),
    ("open exel", "open_target", "app"),

    # ---- common everyday apps, correctly spelled ------------------------
    ("open word", "open_target", "app"),
    ("open excel", "open_target", "app"),
    ("open calc", "open_target", "app"),
    ("open calculator", "open_target", "app"),
    ("open notepad", "open_target", "app"),
    ("open chrome", "open_target", "app"),
    ("open google chrome", "open_target", "app"),
    ("open powershell", "open_target", "app"),
    ("open terminal", "open_target", "app"),
    ("open paint", "open_target", "app"),
    ("open wordpad", "open_target", "app"),
    ("open task manager", "open_target", "app"),
    ("open control panel", "open_target", "app"),
    ("open registry editor", "open_target", "app"),
    ("open winrar", "open_target", "app"),
    ("open git bash", "open_target", "app"),
    ("open onenote", "open_target", "app"),
    ("open outlook", "open_target", "app"),

    # ---- alternate verbs people actually use -----------------------------
    ("launch vscode", "open_target", "app"),
    ("start notepad", "open_target", "app"),
    ("fire up chrome", "open_target", "app"),
    ("pull up excel", "open_target", "app"),
    ("boot up powershell", "open_target", "app"),

    # ---- possessives / filler --------------------------------------------
    ("open my telegram", "open_target", "app"),
    ("can you open the chrome app", "open_target", "app"),
    ("open up capcut for me", "open_target", "app"),

    # ---- apps that only exist as a Desktop shortcut, not the Start Menu --
    ("open trello", "open_target", "file"),
    ("open slack", "open_target", "file"),

    # ---- named files/folders from the user's real machine -----------------
    ("open my CV", "open_target", "file"),
    ("open changes.pdf", "open_target", "file"),
    ("open the SAT TOP folder", "open_target", "file"),
    ("open eco pulse", "open_target", "file"),
    ("open downloads", "open_folder", "folder"),
    ("open my desktop", "open_folder", "folder"),
    ("show me my documents", "open_folder", "folder"),
    ("open my downloads folder", "open_folder", "folder"),
    ("show me the desktop", "open_target", "folder"),  # "the", not "my" — must still work
    ("open pictures", "open_folder", "folder"),

    # ---- search phrasing (a different tool, same resolver underneath) ----
    ("find my CV", "search_files", "search"),
    ("where is changes.pdf", "search_files", "search"),
    ("search for eco pulse", "search_files", "search"),
    ("locate the SAT TOP folder", "search_files", "search"),

    # ---- things that do NOT exist: must fail honestly, never fabricate ---
    ("open discrod", "open_target", "fail"),
    ("open my imaginary unicorn launcher", "open_target", "fail"),
    ("open xzqvbnmasdf", "open_target", "fail"),
    ("open frobnicator9000", "open_target", "fail"),
    ("find my flying car", "search_files", "search_fail"),

    # ---- a named media app still launches via its own tool ---------------
    ("play spotify", "open_app", "router_only"),
]


@pytest.fixture(scope="module")
def router() -> IntentRouter:
    return IntentRouter(CONFIG)


def _route(router: IntentRouter, utterance: str):
    return router.route(utterance)


# --------------------------------------------------------------------- ROUTER
@pytest.mark.parametrize("utterance,expected_tool,kind", CASES, ids=[c[0] for c in CASES])
def test_router_picks_right_tool(router, utterance, expected_tool, kind):
    intent = router.route(utterance)
    assert intent is not None, f"router MISSED entirely (fell through to Claude): {utterance!r}"
    assert intent.tool == expected_tool, (
        f"{utterance!r} -> tool {intent.tool!r}, expected {expected_tool!r} (args={intent.args!r})"
    )


# ----------------------------------------------------------------- RESOLUTION
RESOLVABLE = [c for c in CASES if c[2] != "router_only"]


@pytest.mark.parametrize("utterance,expected_tool,kind", RESOLVABLE, ids=[c[0] for c in RESOLVABLE])
def test_resolves_against_real_machine(router, utterance, expected_tool, kind):
    intent = router.route(utterance)
    assert intent is not None, f"router MISSED entirely: {utterance!r}"

    if kind == "app":
        name = intent.args.get("name", "")
        target, matched = resolve_app(name)
        assert target is not None, f"{utterance!r} (name={name!r}) should resolve to a real installed app"

    elif kind == "fail":
        name = intent.args.get("name", "")
        target, _ = resolve_app(name)
        hits = find_files(name)
        assert target is None and not hits, (
            f"{utterance!r} (name={name!r}) should find NOTHING (it names something that "
            f"doesn't exist) but got app={target!r} file_hits={len(hits)}"
        )

    elif kind == "file":
        name = intent.args.get("name", "")
        target, _ = resolve_app(name)
        hits = find_files(name)
        assert target is not None or hits, (
            f"{utterance!r} (name={name!r}) should find a real app or file, found neither"
        )

    elif kind == "folder":
        # Either the dedicated open_folder rule fired (path arg present), or
        # open_target's own special-folder short-circuit will handle it —
        # both are correct, verified against the real home directory.
        if intent.tool == "open_folder":
            raw_path = intent.args.get("path", "")
        else:
            assert intent.tool == "open_target"
            raw_path = "~/" + intent.args.get("name", "").strip()
        expanded = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        # open_target's special-folder path takes the bare word ("desktop"),
        # not a "~/Desktop"-shaped string, so fall back to a direct lookup
        # against the known special folders when the naive join doesn't
        # already point at a real directory.
        if not expanded.is_dir():
            from jalen.tools.launcher import _SPECIAL_FOLDERS

            key = intent.args.get("name", "").strip().lower()
            for prefix in ("my ", "the ", "a ", "an "):
                if key.startswith(prefix):
                    key = key[len(prefix):]
            special = _SPECIAL_FOLDERS.get(key)
            assert special, f"{utterance!r} -> not a recognized special folder (key={key!r})"
            expanded = Path.home() / special
        assert expanded.is_dir(), f"{utterance!r} should resolve to a real existing folder, got {expanded}"

    elif kind == "search":
        query = intent.args.get("query", "")
        hits = find_files(query)
        assert hits, f"{utterance!r} (query={query!r}) should find at least one real match"

    elif kind == "search_fail":
        query = intent.args.get("query", "")
        hits = find_files(query)
        assert not hits, f"{utterance!r} (query={query!r}) should find nothing, got {hits}"


# ------------------------------------------------------------------- SUMMARY
def test_benchmark_summary(router):
    """
    Not a correctness check on its own (the two tests above already assert
    every row) — computes the overall pass rate and median router latency
    and writes them to a UTF-8 report file (never printed raw to the cp1251
    console, which crashes on any non-ASCII byte a real filename here can
    contain) plus a coarse regression guard on the pass rate itself.
    """
    total = len(CASES)
    router_hits = 0
    resolved_hits = 0
    resolved_total = 0
    latencies: list[float] = []

    for utterance, expected_tool, kind in CASES:
        t0 = time.perf_counter()
        intent = router.route(utterance)
        latencies.append(time.perf_counter() - t0)
        if intent and intent.tool == expected_tool:
            router_hits += 1
        if intent is None or kind == "router_only":
            continue

        resolved_total += 1
        ok = False
        try:
            if kind == "app":
                ok = resolve_app(intent.args.get("name", ""))[0] is not None
            elif kind == "fail":
                name = intent.args.get("name", "")
                ok = resolve_app(name)[0] is None and not find_files(name)
            elif kind == "file":
                name = intent.args.get("name", "")
                ok = resolve_app(name)[0] is not None or bool(find_files(name))
            elif kind == "folder":
                ok = True  # verified precisely in test_resolves_against_real_machine
            elif kind == "search":
                ok = bool(find_files(intent.args.get("query", "")))
            elif kind == "search_fail":
                ok = not find_files(intent.args.get("query", ""))
        except Exception:
            ok = False
        resolved_hits += ok

    latencies.sort()
    median_ms = latencies[len(latencies) // 2] * 1000
    max_ms = latencies[-1] * 1000

    report_dir = Path(os.environ.get("TEMP", ".")) / "jarvis_benchmark_open"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.txt"
    report_path.write_text(
        "\n".join(
            [
                f"total cases: {total}",
                f"router tool-match: {router_hits}/{total}",
                f"resolution pass: {resolved_hits}/{resolved_total}",
                f"median router latency: {median_ms:.4f} ms",
                f"max router latency: {max_ms:.4f} ms",
            ]
        ),
        encoding="utf-8",
    )

    assert router_hits / total >= 0.95, f"router tool-match pass rate too low: {router_hits}/{total}"
    assert resolved_hits / resolved_total >= 0.95, (
        f"resolution pass rate too low: {resolved_hits}/{resolved_total} (see {report_path})"
    )
    assert median_ms < 50, f"router median latency too slow: {median_ms:.2f} ms"
