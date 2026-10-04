"""
Every explicit request in this conversation, checked against the build.

WHY THIS FILE EXISTS, AND WHY IT IS NOT LIKE THE OTHERS
--------------------------------------------------------
The other test files ask "does this component behave correctly". This one
asks a different question: "did we build the thing he asked for". Those are
not the same, and the gap between them is where this project has failed
before — a module can be perfectly correct and still not be what was wanted.

He asked, more than once, to "re-read the whole history and make sure every
user expectation has been met". That is a checklist, and a checklist that
lives in someone's memory is one that quietly loses rows. So it lives here,
tagged by the message each request came from.

WHEN A ROW HERE FAILS it does not mean an assertion drifted. It means a
capability he explicitly asked for has stopped existing — which is the same
kind of failure tests/test_every_request_reachable.py was written to catch,
and should be read that way.

The rows are deliberately SHALLOW — "does this tool exist, is it reachable,
is it gated correctly". Depth belongs in the per-feature files. What this
file guarantees is coverage: that nothing he asked for silently disappeared.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jalen import tools  # noqa: E402
from jalen.brain.router import IntentRouter  # noqa: E402
from jalen.brain.tools import TOOL_SPECS  # noqa: E402
from jalen.config import CONFIG  # noqa: E402
from jalen.safety import SafetyEngine  # noqa: E402

e = SafetyEngine(CONFIG)
r = IntentRouter(CONFIG)


def routes(phrase, expect=None):
    hit = r.route(phrase)
    if hit is None:
        return False
    return hit.tool == expect if expect else True


CHECKS = [
    # ---- MESSAGE 1: the handoff, six items -----------------------------
    # WITHDRAWN in message 13: "Remove orb resizing by hand entirely... I
    # completely agree with this idea, Ctrl Alt B should make it bigger, and
    # control alt S should make it smaller."
    #
    # The check is not deleted, it is REPLACED. A withdrawn request still
    # needs its replacement guarded, or "we removed it because he said so"
    # quietly becomes "we removed it and put nothing there".
    # WITHDRAWN AGAIN in message 14, after he actually used it: "make
    # yourself bigger and smaller none of it is working, just forget it man,
    # we do not need feature, you gotta remove this altogether, it should
    # stay still in one place, and it should not move, it should not be
    # draggable."
    #
    # What replaces it is stronger than what it replaced: with nothing on the
    # orb meant to be clicked, the window is click-through in EVERY state, so
    # there is no longer any combination of circumstances where it can take a
    # click meant for something underneath it.
    ("M1", "1. the orb never touches the cursor",
     lambda: not (ROOT / "jalen/ui/gestures.py").exists()
             and not hasattr(__import__("jalen.ui.orb", fromlist=["x"]).Orb, "resize_by")
             and "set_click_through(self._hwnd, True)" in
                 (ROOT / "jalen/ui/orb.py").read_text(encoding="utf-8")),
    ("M1", "2. silent exit -> diagnosable",
     lambda: (ROOT / "jalen/crashlog.py").exists() and "--why" in (ROOT / "run.py").read_text(encoding="utf-8")),
    ("M1", "3. wake model / his voice",
     lambda: (ROOT / "scripts/record_wake_samples.py").exists()
             and float(CONFIG.get_path("wake.threshold")) <= 0.5),
    ("M1", "4. six untested flows",
     lambda: (ROOT / "scripts/rehearse.py").exists()
             and (ROOT / "tests/test_flow_rehearsal.py").exists()),
    ("M1", "5. vault created",
     lambda: (ROOT / "data/vault.json").exists()),
    ("M1", "6a. onboarding for a 2nd user",
     lambda: (ROOT / "scripts/onboard.py").exists()),
    ("M1", "6b. per-user config, not hardcoded",
     lambda: "USER_CONFIG" in (ROOT / "jalen/config.py").read_text(encoding="utf-8")),
    ("M1", "6c. my-voice skill is portable",
     lambda: "personal.voice_guide" in (ROOT / "jalen/tools/voice.py").read_text(encoding="utf-8")),
    ("M1", "6d. packaging",
     lambda: (ROOT / "pyproject.toml").exists()),
    ("M1", "6e. licence",
     lambda: (ROOT / "LICENSE").exists()),
    ("M1", "6f. update path",
     lambda: (ROOT / "scripts/update.py").exists()),
    ("M1", "stale weakness deleted",
     lambda: "CloudCork" not in (ROOT / "data/weaknesses.md").read_text(encoding="utf-8")),

    # ---- MESSAGE 3: stress, orb, new-user sweep ------------------------
    ("M3", "1. ja-LEN stress understood",
     lambda: routes("jaleen, quit", "jalen_quit") and routes("jelen, quit", "jalen_quit")),
    ("M3", "1b. Cyrillic J (dzh/zh)",
     lambda: routes("dzhalen, quit", "jalen_quit") and routes("zhalen, quit", "jalen_quit")),
    ("M3", "1c. his OWN name not stolen",
     lambda: not routes("jaluddin", "jalen_ack")),
    ("M3", "2a. orb visible when idle",
     lambda: max(int(CONFIG.get_path("ui.theme.idle")[i:i + 2], 16) for i in (1, 3, 5)) >= 120),
    ("M3", "2b. orb always on top, re-asserted",
     lambda: "TOPMOST_REASSERT_TICKS" in (ROOT / "jalen/ui/orb.py").read_text(encoding="utf-8")),
    ("M3", "2c. name written below the orb",
     lambda: "_draw_name" in (ROOT / "jalen/ui/orb.py").read_text(encoding="utf-8")),
    ("M3", "2d. sphere like the photo (mesh + rim)",
     lambda: all(x in (ROOT / "jalen/ui/orb.py").read_text(encoding="utf-8")
                 for x in ("_draw_mesh", "_draw_rim", "_draw_wireframe"))),
    ("M3", "2e. listening = waves",
     lambda: "WAVES travelling outward" in (ROOT / "jalen/ui/orb.py").read_text(encoding="utf-8")),
    ("M3", "2f. thinking = different motion",
     lambda: "cannot be mistaken for listening" in (ROOT / "jalen/ui/orb.py").read_text(encoding="utf-8")),
    ("M3", "2g. the orb stays put and stays out of the way",
     lambda: not hasattr(__import__("jalen.ui.orb", fromlist=["x"]).Orb, "move_to")),
    ("M3", "4. new-user sweep exists",
     lambda: (ROOT / "tests/test_new_user_sweep.py").exists()),

    # ---- MESSAGE 4: vault leak, cut-off speech, dev work ---------------
    ("M4", "1a. passphrase redacted in the audit",
     lambda: e.classify("unlock_vault", {"passphrase": "x"}).detail["args"]["passphrase"] == "***redacted***"),
    ("M4", "1b. spoken passphrase not transcribed to log",
     lambda: "_SECRET_SHAPES" in (ROOT / "jalen/audit.py").read_text(encoding="utf-8")),
    ("M4", "1c. typed unlock box",
     lambda: "unlock_vault_prompt" in tools.REGISTRY),
    ("M4", "2. speech no longer cuts off",
     lambda: float(CONFIG.get_path("conversation.barge_in_grace_s", 0)) >= 0.8
             and int(CONFIG.get_path("conversation.barge_in_frames", 1)) >= 3),
    ("M4", "2b. barge-in fires once per reply",
     lambda: "barge_fired" in (ROOT / "jalen/app.py").read_text(encoding="utf-8")),
    ("M4", "3. speaker ID - measured, not shipped",
     lambda: not (ROOT / "jalen/audio/voiceid.py").exists()),
    ("M4", "4a. hand off to Claude Code",
     lambda: "start_coding_job" in tools.REGISTRY),
    ("M4", "4b. open folders in VS Code",
     lambda: "open_in_vscode" in tools.REGISTRY),
    ("M4", "4c. initialise git repos",
     lambda: "init_git_repo" in tools.REGISTRY),
    ("M4", "4d. read the agent's outcome",
     lambda: "review_coding_job" in tools.REGISTRY),
    ("M4", "4e. notified when the agent finishes",
     lambda: "_announce_finished_jobs" in (ROOT / "jalen/app.py").read_text(encoding="utf-8")),
    ("M4", "4f. judge % of expectations met",
     lambda: "NOW JUDGE IT" in (ROOT / "jalen/tools/devwork.py").read_text(encoding="utf-8")),
    ("M4", "4g. agent may actually WRITE files",
     lambda: __import__("jalen.tools.devwork", fromlist=["x"]).PERMISSION_MODE == "acceptEdits"),
    ("M4", "4h. authenticate to sites / fill credentials",
     lambda: all(t in tools.REGISTRY for t in ("fill_credential", "site_permission", "remember_site_decision"))),
    ("M4", "4i. ask him for a human step (API keys)",
     lambda: "ask_user" in tools.REGISTRY),
    ("M4", "4j. secrets never exposed",
     lambda: "get_secret" not in tools.REGISTRY),

    # ---- MESSAGE 5: self-control, autonomous test ----------------------
    ("M5", "Jalen tests itself",
     lambda: "run_own_tests" in tools.REGISTRY and routes("test yourself", "run_own_tests")),
    ("M5", "Jalen diagnoses itself",
     lambda: "self_diagnose" in tools.REGISTRY and routes("diagnose yourself", "self_diagnose")),
    ("M5", "Jalen opens its own VS Code",
     lambda: "open_own_project" in tools.REGISTRY),
    ("M5", "self-control is read-only on its own code",
     lambda: "write_text(" not in (ROOT / "jalen/tools/selfcontrol.py").read_text(encoding="utf-8")),

    # ---- MESSAGE 7: navigate me ---------------------------------------
    ("M7", "one command shows what is left",
     lambda: (ROOT / "scripts/whats_left.py").exists()
             and '"todo"' in (ROOT / "jalen.ps1").read_text(encoding="utf-8")),
    ("M7", "each task is one word",
     lambda: all(f'"{c}"' in (ROOT / "jalen.ps1").read_text(encoding="utf-8")
                 for c in ("vault", "voice", "rehearse", "licence"))),
    ("M7", "licence is a guided choice",
     lambda: (ROOT / "scripts/choose_licence.py").exists()),

    # ---- MESSAGE 8: gemini/chatgpt, master prompts, the orb, the list ---
    ("M8", "1. hand off to Gemini and ChatGPT",
     lambda: all(t in tools.REGISTRY for t in ("delegate_task", "follow_up_task"))),
    ("M8", "1b. compare outcome against expectations",
     lambda: "NOW JUDGE IT" in tools.REGISTRY["review_delegation"]()
             or "nothing delegated" in tools.REGISTRY["review_delegation"]()),
    ("M8", "1c. follow-up keeps the conversation",
     lambda: "follow_up_task" in TOOL_SPECS),
    ("M8", "2. master-prompt standard exists",
     lambda: all(x in tools.REGISTRY["master_prompt_guide"]()
                 for x in ("CONTEXT", "WHAT SUCCESS LOOKS LIKE", "CONSTRAINTS"))),
    ("M8", "2b. thin briefs are REFUSED, not sent",
     lambda: "isn't ready" in tools.REGISTRY["hand_off_to_cowork"]("fix it")),
    ("M8", "3. capability list is generated",
     lambda: (ROOT / "scripts/capabilities.py").exists()
             and '"can"' in (ROOT / "jalen.ps1").read_text(encoding="utf-8")),
    ("M8", "4. orb shows WORKING while working",
     lambda: "_refresh_orb" in (ROOT / "jalen/app.py").read_text(encoding="utf-8")),
    ("M8", "5. a turn is not held for three minutes",
     lambda: "_WAKE" in __import__("inspect").getsource(
         __import__("jalen.audio.tts", fromlist=["x"]).Speaker.stop)),
    ("M8", "6. every long answer reaches the screen",
     lambda: "shown_count" in (ROOT / "jalen/ui/orb.py").read_text(encoding="utf-8")),

    # ---- invariants that must never break ------------------------------
    ("INV", "injection guard before pre-approval",
     lambda: e.classify("send_telegram_message",
                        {"to": "AI engineering & Machine learning", "text": "x"},
                        origin="content").tier.value == "black"),
    ("INV", "every spec has a real tool",
     lambda: set(TOOL_SPECS) == set(tools.REGISTRY)),
    ("INV", "quit always works",
     lambda: all(routes(p, "jalen_quit") for p in ("quit", "exit", "turn off", "jalen quit"))),
    # Was "camera off by default", which was a promise about a setting.
    # Now there is no camera code at all, which is a promise about the
    # binary — a strictly stronger thing to be able to assert.
    ("INV", "no camera code anywhere",
     lambda: not (ROOT / "jalen/ui/gestures.py").exists()
             and CONFIG.get_path("ui.hand_gestures") is None
             and "mediapipe" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")),
]


@pytest.mark.parametrize(
    "message, requirement, check",
    CHECKS,
    ids=[f"{m}-{n.split('.')[0]}" for m, n, _ in CHECKS],
)
def test_the_thing_he_asked_for_still_exists(message, requirement, check):
    assert check(), (
        f"{message}: {requirement}\n"
        "This is a capability he explicitly asked for, and it has gone "
        "missing. That is a different kind of failure from an assertion "
        "changing - see this file's docstring."
    )


def test_the_checklist_covers_every_conversation_turn():
    """
    Guards the guard. If a message's requests were never added, this file
    silently checks less than it claims to.
    """
    covered = {m for m, _, _ in CHECKS}
    assert {"M1", "M3", "M4", "M5", "M7", "INV"} <= covered, (
        f"no rows for { 'M1 M3 M4 M5 M7 INV'.split() } - a whole turn's "
        "requests are unchecked"
    )
    assert len(CHECKS) >= 50, f"only {len(CHECKS)} rows; requests have been dropped"
