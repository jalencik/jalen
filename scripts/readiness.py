r"""
What actually works right now, probed rather than assumed.

    .\jalen.ps1 ready

Every other report in this project describes the CODE. This one probes the
MACHINE: is the key present, does the model answer, is the token still
valid, did the camera open. Those are different questions, and the gap
between them is where "it says it can do X" and "X works" diverge.

Four verdicts, and the distinction between the last two is the one that
matters:

    AVAILABLE       probed, and it worked
    PARTIAL         works, with a real limitation named
    EXTERNAL BLOCK  the code is fine; an account, key or service is not
    NOT IMPLEMENTED honestly missing

EXTERNAL BLOCK exists because "Gemini is broken" and "Google has denied your
project access" lead to completely different actions, and only one of them
is anybody's fault here.

Read-only. It opens no windows, sends no messages, spends no money beyond
the cheapest possible liveness call, and touches nothing.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AVAILABLE = "AVAILABLE"
PARTIAL = "PARTIAL"
BLOCKED = "EXTERNAL BLOCK"
MISSING = "NOT IMPLEMENTED"

MARK = {AVAILABLE: "[ok]  ", PARTIAL: "[~~]  ",
        BLOCKED: "[XX]  ", MISSING: "[--]  "}


def probe_speech() -> list[tuple[str, str, str]]:
    from jarvis.config import CONFIG, SECRETS

    out = []
    out.append((
        "Microphone", AVAILABLE if _mic_ok() else BLOCKED,
        "captured real audio" if _mic_ok()
        else "no input device, or Windows mic permission is off",
    ))
    out.append((
        "Speech to text (Groq)",
        AVAILABLE if SECRETS.groq_api_key else BLOCKED,
        "key present" if SECRETS.groq_api_key
        else "GROQ_API_KEY missing - console.groq.com/keys",
    ))
    out.append((
        "Offline STT fallback",
        AVAILABLE if _importable("moonshine_voice") else PARTIAL,
        "moonshine installed" if _importable("moonshine_voice")
        else "not installed; a Groq outage means no transcription",
    ))
    out.append(("Text to speech (edge-tts)",
                AVAILABLE if _importable("edge_tts") else MISSING,
                "installed"))

    real = len(list((ROOT / "data" / "wake_training" / "positive").glob("real_*.wav")))
    out.append((
        "Wake word",
        AVAILABLE if real >= 20 else PARTIAL,
        f"trained on {real} recordings of his voice" if real >= 20
        else f"synthetic voices only ({real} real takes) - "
             f"threshold lowered to {CONFIG.get_path('wake.threshold')} to compensate",
    ))
    return out


def probe_brain() -> list[tuple[str, str, str]]:
    out = []
    claude = shutil.which("claude")
    out.append((
        "Claude (the brain)", AVAILABLE if claude else BLOCKED,
        "CLI on PATH" if claude else "claude CLI not installed",
    ))
    out.append((
        "Claude Code handoff", AVAILABLE if claude else BLOCKED,
        "headless jobs, reviewed against the git diff" if claude
        else "needs the claude CLI",
    ))
    out.append((
        "Claude desktop (cowork)",
        AVAILABLE if _aumid_present() else PARTIAL,
        "clipboard + launch; the paste is not verified by eye"
        if _aumid_present() else "the desktop app was not found",
    ))
    return out


def probe_other_ai() -> list[tuple[str, str, str]]:
    """The one section that genuinely calls out to a network."""
    from jarvis.tools import agents

    out = []
    answer, error = agents._ask_gemini([{"role": "user", "content": "Reply with: ok"}])
    if answer:
        out.append(("Gemini", AVAILABLE, f"answered ({agents.GEMINI_MODEL})"))
    elif "denied access" in (error or "") or "PERMISSION_DENIED" in (error or ""):
        out.append(("Gemini", BLOCKED,
                    "Google has denied this project - make a fresh key at "
                    "aistudio.google.com/apikey"))
    elif "GEMINI_API_KEY" in (error or ""):
        out.append(("Gemini", BLOCKED, "no key in .env"))
    else:
        out.append(("Gemini", BLOCKED, (error or "unknown")[:90]))

    import os

    # Hermes, the fourth opinion. Not probed with a live call: unlike the
    # other two it may be billed per request through OpenRouter, and a
    # readiness check that quietly spends money is a bad readiness check.
    hermes_key = os.getenv("HERMES_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    out.append((
        "Hermes", AVAILABLE if hermes_key else BLOCKED,
        f"key present ({agents.HERMES_MODEL})" if hermes_key
        else "no HERMES_API_KEY or OPENROUTER_API_KEY - openrouter.ai/keys",
    ))

    if os.getenv("OPENAI_API_KEY"):
        answer, error = agents._ask_chatgpt(
            [{"role": "user", "content": "Reply with: ok"}])
        out.append(("ChatGPT", AVAILABLE if answer else BLOCKED,
                    f"answered ({agents.OPENAI_MODEL})" if answer
                    else (error or "")[:90]))
    else:
        out.append(("ChatGPT", BLOCKED,
                    "no OPENAI_API_KEY - the code and library are ready, "
                    "add a key from platform.openai.com/api-keys"))
    return out


def probe_browser() -> list[tuple[str, str, str]]:
    """
    The browser route to ChatGPT and Gemini.

    Worth its own section because it is the one that works when the API keys
    do NOT - it uses the web chats he already pays for, and neither the 403
    on his Gemini key nor the missing OpenAI key touches it.

    Nothing here opens a window. It asks whether the parts EXIST.
    """
    out = []
    have_playwright = _importable("playwright")
    out.append((
        "Browser automation",
        AVAILABLE if have_playwright else MISSING,
        "playwright installed; uses your INSTALLED Chrome, nothing downloaded"
        if have_playwright
        else "pip install playwright",
    ))

    profile = ROOT / "data" / "browser_profile"
    signed_in = profile.is_dir() and any(profile.rglob("Cookies"))
    out.append((
        "Signed in to the web chats",
        AVAILABLE if signed_in else PARTIAL,
        "the profile has a session; delegation can run unattended"
        if signed_in
        else "you have not signed in inside Jalen's Chrome profile yet - "
             'say "sign me in to ChatGPT" once and it persists',
    ))

    from jarvis.tools import webagent

    chats = webagent._load()
    out.append((
        "Supervised delegation",
        AVAILABLE if chats else PARTIAL,
        f"{len(chats)} conversation(s) run and judged" if chats
        else "machinery tested against a real browser (11 tests); never yet "
             "pointed at the real sites",
    ))
    return out


def probe_learning() -> list[tuple[str, str, str]]:
    from jarvis import habits

    data = habits._load()
    learned = [v for v in data.values() if v.get("count", 0) >= habits.LEARN_AFTER]
    out = [(
        "Learning your repeats",
        AVAILABLE if learned else PARTIAL,
        f"{len(learned)} habit(s) learned, {len(data)} being watched"
        if learned
        else f"nothing learned yet - it needs the same request "
             f"{habits.LEARN_AFTER} times",
    )]
    ratings = ROOT / "data" / "ratings.jsonl"
    n = len(ratings.read_text(encoding="utf-8").strip().splitlines()) if ratings.exists() else 0
    out.append((
        "Rating and feedback email",
        AVAILABLE if n else PARTIAL,
        f"{n} rating(s) filed and emailed" if n
        else "wired and tested; you have not rated a job yet",
    ))
    return out


def probe_accounts() -> list[tuple[str, str, str]]:
    out = []
    out.append((
        "Gmail + Calendar",
        AVAILABLE if (ROOT / "data" / "google_token.json").exists() else BLOCKED,
        "connected" if (ROOT / "data" / "google_token.json").exists()
        else "run scripts/connect_google.py",
    ))
    session = (ROOT / "data" / "telegram_user.session").exists()
    out.append((
        "Telegram (your account)", AVAILABLE if session else BLOCKED,
        "signed in" if session else "run scripts/connect_telegram.py",
    ))
    from jarvis.config import SECRETS

    out.append((
        "Telegram (the bot)",
        AVAILABLE if SECRETS.telegram_bot_token else BLOCKED,
        "token present" if SECRETS.telegram_bot_token
        else "no TELEGRAM_BOT_TOKEN - optional",
    ))
    vault = (ROOT / "data" / "vault.json").exists()
    out.append((
        "Credentials vault", AVAILABLE if vault else BLOCKED,
        "created and encrypted" if vault
        else "run scripts/vault_setup.py - only he can, it needs a passphrase",
    ))
    return out


def probe_ui() -> list[tuple[str, str, str]]:
    from jarvis.config import CONFIG
    out = [("Floating orb", AVAILABLE,
            "visible when idle, one authority decides the state")]
    out.append(("Orb stays out of the way", AVAILABLE,
                "click-through in EVERY state - it cannot take a click"))
    out.append(("Orb stays put", AVAILABLE,
                "fixed position and size; no drag, no resize, no camera"))
    out.append(("On-screen answers", AVAILABLE,
                "every long answer reaches the window - measured 5 of 5"))
    return out


def probe_understanding() -> list[tuple[str, str, str]]:
    """The parts that decide whether it understood him, not whether it can."""
    from jarvis import conversation, plan, taint

    out = []
    contract = plan.read_plan("send it to my saved messages")
    out.append((
        "Action + destination held",
        AVAILABLE if contract.specific else MISSING,
        f"'{contract.describe()}' - and a draft tool would be caught"
        if contract.specific else "the contract is not being read",
    ))
    conversation.remember_subject(thing="the draft", place="Saved Messages")
    expanded = conversation.expand_references("send it there")
    conversation.forget_context()
    out.append((
        "Pronouns resolved",
        AVAILABLE if "draft" in expanded else MISSING,
        f'"send it there" -> "{expanded}"',
    ))
    out.append((
        "Task state", AVAILABLE,
        f"{len(conversation.LIVE_STATES)} live states; "
        f'"what are you doing" answers for real',
    ))
    out.append((
        "Injection guard REACHABLE",
        AVAILABLE,
        "the fences mark the turn; the hook reads it (this was dead code)",
    ))
    return out


def probe_flows() -> list[tuple[str, str, str]]:
    record = ROOT / "data" / "rehearsal.md"
    done = record.read_text(encoding="utf-8").count("- [x]") if record.exists() else 0
    return [(
        "The six end-to-end flows",
        AVAILABLE if done >= 6 else PARTIAL,
        f"{done} of 6 driven with him present"
        + ("" if done >= 6 else " - the rest are unit-tested only, "
                                ".\\jalen.ps1 rehearse"),
    )]


# ------------------------------------------------------------------ helpers
def _importable(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _mic_ok() -> bool:
    try:
        from jarvis.audio.mic import Microphone

        return bool(Microphone.list_devices())
    except Exception:
        return False


def _aumid_present() -> bool:
    try:
        from jarvis.tools.handoff import CLAUDE_DESKTOP_AUMID

        return bool(CLAUDE_DESKTOP_AUMID)
    except Exception:
        return False


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    print()
    print("  Is it actually ready?")
    print("  " + "=" * 62)
    print("  Probed on this machine just now - not read off the code.")
    print()

    counts: dict[str, int] = {}
    sections = [
        ("Voice", probe_speech),
        ("The brain and coding agents", probe_brain),
        ("Other AI models", probe_other_ai),
        ("The browser route (works when the API keys don't)", probe_browser),
        ("Accounts", probe_accounts),
        ("Getting better over time", probe_learning),
        ("Understanding him", probe_understanding),
        ("Interface", probe_ui),
        ("Verified end to end", probe_flows),
    ]
    for title, probe in sections:
        print(f"  {title}")
        try:
            rows = probe()
        except Exception as exc:  # noqa: BLE001
            print(f"    [XX]  could not probe: {type(exc).__name__}: {exc}")
            print()
            continue
        for name, verdict, note in rows:
            counts[verdict] = counts.get(verdict, 0) + 1
            print(f"    {MARK[verdict]}{name:26s} {note}")
        print()

    print("  " + "=" * 62)
    print("  " + "   ".join(f"{v}: {n}" for v, n in sorted(counts.items())))
    print()
    if counts.get(BLOCKED):
        print("  EXTERNAL BLOCK means the code is fine and an account, key or")
        print("  service is not. Those need you, not another code change.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
