r"""
Diagnostics. Run this whenever something doesn't work:

    .\jalen.ps1 check

Tells you exactly which piece is missing, in plain language.
"""
from __future__ import annotations

import importlib
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WARN, BAD = "  [ok] ", "  [--] ", "  [XX] "

PACKAGES = [
    ("yaml", "pyyaml", True),
    ("dotenv", "python-dotenv", True),
    ("numpy", "numpy", True),
    ("sounddevice", "sounddevice", True),
    ("onnxruntime", "onnxruntime", True),
    ("openwakeword", "openwakeword==0.6.0", True),
    ("groq", "groq", True),
    ("edge_tts", "edge-tts", True),
    ("av", "av", True),
    ("claude_agent_sdk", "claude-agent-sdk", True),
    ("psutil", "psutil", False),
    ("uiautomation", "uiautomation", False),
    ("PIL", "pillow", False),
    ("aiogram", "aiogram", False),
    ("telethon", "telethon", False),
    ("moonshine_voice", "moonshine-voice", False),
    ("fastembed", "fastembed", False),
]


def main() -> int:
    # This console defaults to a legacy code page (cp1251 on this machine)
    # and print() RAISES on a character it cannot encode rather than
    # degrading. The dashes below are enough to do it, and a diagnostics tool
    # that dies partway through is worse than no diagnostics — you get half a
    # report and a traceback about the report, not about the problem.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    problems = 0
    print("\n=== Jalen diagnostics ===\n")

    print("System")
    print(f"{OK}Python {platform.python_version()} ({platform.architecture()[0]})")
    print(f"{OK}{platform.system()} {platform.release()}")
    if sys.version_info < (3, 12):
        print(f"{WARN}Python 3.12+ recommended (Windows-MCP needs 3.12+, 3.13 is the tested path)")
    if platform.architecture()[0] != "64bit":
        print(f"{BAD}32-bit Python — onnxruntime has no 32-bit wheels. Reinstall 64-bit Python.")
        problems += 1

    print("\nPackages")
    for module, pip_name, required in PACKAGES:
        try:
            mod = importlib.import_module(module)
            version = getattr(mod, "__version__", "")
            print(f"{OK}{pip_name} {version}")
        except ImportError:
            marker = BAD if required else WARN
            problems += 1 if required else 0
            print(f"{marker}{pip_name} missing — pip install {pip_name}")

    print("\nModels")
    for name, why in (
        ("silero_vad.onnx", "voice activity detection"),
        ("hey_jarvis_v0.1.onnx", "wake word"),
    ):
        path = ROOT / "models" / name
        if path.exists():
            print(f"{OK}{name} ({path.stat().st_size / 1e6:.1f} MB)")
        else:
            print(f"{WARN}{name} missing ({why}) — python scripts/download_models.py")

    print("\nMicrophone")
    try:
        from jarvis.audio.mic import Microphone

        devices = Microphone.list_devices()
        if not devices:
            print(f"{BAD}No input devices found.")
            problems += 1
        for d in devices[:8]:
            print(f"{OK}[{d['index']}] {d['name']}")
        print("      (set audio.input_device in config/jarvis.yaml to pin one)")
    except Exception as exc:
        print(f"{BAD}Can't list devices: {exc}")
        problems += 1

    print("\nCredentials")
    try:
        from jarvis.config import SECRETS

        checks = [
            ("GROQ_API_KEY", SECRETS.groq_api_key, True, "speech recognition"),
            ("TELEGRAM_BOT_TOKEN", SECRETS.telegram_bot_token, False, "Telegram control"),
            ("GEMINI_API_KEY", SECRETS.gemini_api_key, False, "free-tier offload"),
            ("TELEGRAM_API_ID", SECRETS.telegram_api_id, False, "reading your own chats"),
            ("GITHUB_TOKEN", SECRETS.github_token, False, "GitHub"),
            ("NOTION_TOKEN", SECRETS.notion_token, False, "Notion"),
        ]
        for name, value, required, why in checks:
            if value:
                print(f"{OK}{name} set")
            else:
                marker = BAD if required else WARN
                problems += 1 if required else 0
                print(f"{marker}{name} missing — needed for {why}")
    except Exception as exc:
        print(f"{BAD}Config failed to load: {exc}")
        problems += 1

    print("\nClaude authentication")
    import shutil
    import subprocess

    # NOT shutil.which("claude"). That probes npm's shim on PATH, which is not
    # the binary the brain spawns, and it once reported a healthy plan through
    # a twenty-minute outage. resolved_cli_path() is the brain's own answer.
    from jarvis.brain.agent import resolved_cli_path

    claude_path, where, spawnable = resolved_cli_path()
    if claude_path and not spawnable:
        print(f"{BAD}the brain cannot start: {where}")
        print(f"      it would use: {claude_path}")
        print("      fix: reinstall the SDK with a real wheel --")
        print("        .venv\\Scripts\\python.exe -m pip install --force-reinstall \\")
        print("          --only-binary claude-agent-sdk 'claude-agent-sdk>=0.2.140,<0.3.0'")
        problems += 1
    elif claude_path:
        print(f"{OK}claude CLI found ({where})")
        try:
            # The full resolved path, never a bare name: subprocess.run with
            # shell=False cannot launch "claude" with an implicit .CMD
            # extension, and that raised FileNotFoundError which this swallowed
            # as "couldn't verify" while auth was in fact fine.
            #
            # --setting-sources "" for the same reason Brain.start() passes
            # setting_sources=[]: without it the probe loads this machine's
            # Claude Code settings and every MCP server in them, so a
            # diagnostic meant to answer one question about auth waits on
            # unrelated servers (two of which currently time out at 30s each).
            result = subprocess.run(
                [claude_path, "-p", "reply with the single word: ok",
                 "--setting-sources", ""],
                capture_output=True, text=True, timeout=60,
            )
            # "ok" AS A WORD. The old test was `"ok" in stdout`, and the
            # failure text from 22 August - "...access token has been
            # revoked." - contains "ok" inside "token". It only failed to
            # misfire because that run also exited non-zero.
            import re as _re
            said = (result.stdout or "").strip()
            if result.returncode == 0 and _re.search(r"\bok\b", said.lower()):
                print(f"{OK}authenticated — your Claude plan is working")
            else:
                # BLOCKING, not a warning. This printed "[--]" and then "All
                # good" with exit code 0 for ten days while every brain turn
                # failed - and it is the diagnostic Jalen itself tells him to
                # run when that happens. The CLI's own reply is shown
                # because it names the cause and holds no secret.
                reason = (said or (result.stderr or "").strip()).splitlines()
                print(f"{BAD}the brain is NOT signed in to Claude — it cannot think")
                if reason:
                    print(f"      Claude says: {reason[0][:160]}")
                _print_sign_in_fix(claude_path)
                problems += 1
        except Exception as exc:
            print(f"{BAD}couldn't verify the Claude sign-in ({type(exc).__name__}: {exc})")
            _print_sign_in_fix(claude_path)
            problems += 1
    else:
        # Not "not on PATH" — PATH is irrelevant to the brain. This means the
        # SDK could resolve nothing at all, which on Windows usually means the
        # bundled binary is missing from the wheel.
        print(f"{BAD}the brain has no runnable Claude CLI: {where}")
        print("      most likely the SDK installed without its bundled binary:")
        print("        .venv\\Scripts\\python.exe -m pip install --force-reinstall \\")
        print("          --only-binary claude-agent-sdk 'claude-agent-sdk>=0.2.140,<0.3.0'")
        problems += 1

    print("\nConfig")
    try:
        from jarvis.config import CONFIG
        from jarvis.safety import SafetyEngine

        engine = SafetyEngine(CONFIG)
        counts = {}
        for tier in engine._tier_map.values():
            counts[tier.value] = counts.get(tier.value, 0) + 1
        print(f"{OK}safety tiers loaded: {counts}")
        print(f"{OK}posture: {engine.posture}, paranoid: {engine.paranoid}")
        print(f"{OK}voice: {CONFIG.get_path('tts.voice')}")
        print(f"{OK}wake word: {CONFIG.get_path('identity.wake_word')}")
    except Exception as exc:
        print(f"{BAD}Config problem: {exc}")
        problems += 1

    problems += _check_disk()
    _check_manual_steps()
    _check_last_exit()

    # NOT "python run.py": that advice fails twice over on Windows.
    # PowerShell refuses to run a script from the current folder without a
    # ".\" prefix, and bare "python" is a different install with none of
    # this project's packages. The launcher handles both.
    print("\n" + ("All good — start it with:  .\\jalen.ps1" if problems == 0
                  else f"{problems} blocking problem(s) above."))
    return 1 if problems else 0


def _print_sign_in_fix(claude_path: str) -> None:
    """
    The fix, naming the binary the BRAIN runs - not whatever `claude` means
    in his terminal, which is npm's shim and a different install.

    setup-token first, because it produces a LONG-LIVED token: the ordinary
    login is what expired on 20 September and "could not be refreshed",
    leaving the brain unable to think for ten days before anyone noticed.
    A token in CLAUDE_CODE_OAUTH_TOKEN beats the machine-wide login (see
    CLAUDE.md), so it also stops another Claude install on this machine from
    signing Jalen out. He pastes it himself: nothing here reads, writes or
    prints a credential.
    """
    print("      FIX (about two minutes, once):")
    print(f'        1. In a terminal run:  "{claude_path}" setup-token')
    print("           It opens your browser - sign in with your Claude account.")
    print("        2. It prints a long token. Open .env in this folder and set:")
    print("              CLAUDE_CODE_OAUTH_TOKEN=<paste it here>")
    print("        3. Restart Jalen. The token is read at startup.")
    print("      Leave ANTHROPIC_API_KEY empty - a value there switches the")
    print("      brain to per-token billing instead of your subscription.")


def _check_disk() -> int:
    """
    Free space, as a blocking check.

    Not a nicety. Found at 100% on this machine (70 MB free of 147 GB), and
    a full disk breaks Jalen in ways that look like anything but a full
    disk: sqlite cannot write the audit log, edge-tts cannot spool its mp3,
    Whisper cannot write its temp file, and each of those surfaces as its
    own confusing error. It is also the kind of thing that makes a process
    die somewhere with no room to write down why.
    """
    import shutil

    print("\nDisk")
    try:
        total, _used, free = shutil.disk_usage(str(ROOT))
    except OSError as exc:
        print(f"{WARN}couldn't read disk usage ({exc})")
        return 0

    gb = free / 1e9
    if gb < 1.0:
        print(f"{BAD}{gb:.2f} GB free of {total / 1e9:.0f} GB — this WILL break things.")
        print("      A full disk stops the audit log, speech synthesis and")
        print("      transcription, each with its own misleading error.")
        print("      Free some up:  say \"Jalen, what's eating my disk\"")
        print("                     .venv\\Scripts\\python.exe -m pip cache purge")
        return 1
    if gb < 5.0:
        print(f"{WARN}{gb:.2f} GB free of {total / 1e9:.0f} GB — getting tight.")
        return 0
    print(f"{OK}{gb:.1f} GB free of {total / 1e9:.0f} GB")
    return 0


def _check_manual_steps() -> None:
    """
    The things only HE can do, and the optional extras.

    These are not failures — Jalen runs fine without every one of them — so
    none of them counts toward `problems`. They are here because the
    alternative is a handoff document, and a handoff document is read once.
    """
    print("\nSetup still outstanding")

    from jarvis.tools import vault

    if vault.VAULT_PATH.exists():
        print(f"{OK}credentials vault created")
    else:
        print(f"{WARN}no vault yet — fill_credential cannot work without one.")
        print("      YOU have to run this one; it asks for a passphrase that")
        print("      must never be typed by anyone but you:")
        print("      .venv\\Scripts\\python.exe scripts\\vault_setup.py")

    real_takes = len(list((ROOT / "data" / "wake_training" / "positive").glob("real_*.wav")))
    if real_takes >= 20:
        print(f"{OK}wake word trained on {real_takes} recordings of your voice")
    else:
        print(f"{WARN}the wake model has never heard YOUR voice ({real_takes} real takes).")
        print("      It was trained on synthetic speech, and accent is exactly")
        print("      what these models are sensitive to. If it misses you:")
        print("      .venv\\Scripts\\python.exe scripts\\record_wake_samples.py")

    print(f'{OK}orb resizing: Ctrl+Alt+B / Ctrl+Alt+S, or "make yourself bigger"')

    record = ROOT / "data" / "rehearsal.md"
    if record.exists():
        passed = record.read_text(encoding="utf-8").count("- [x]")
        print(f"{OK}{passed} of 6 end-to-end flows rehearsed with you")
    else:
        print(f"{WARN}six flows have never been driven end to end with you present:")
        print("      .venv\\Scripts\\python.exe scripts\\rehearse.py")


def _check_last_exit() -> None:
    """
    Did the previous run stop for a reason, or just vanish?

    A vanished run is the 21 August bug (jarvis/crashlog.py). Surfacing it
    here means the answer is one command away instead of a forensic exercise.
    """
    from jarvis import crashlog

    record = crashlog.previous_exit()
    if record is None:
        return
    print("\nLast run")
    if record.get("state") == "running":
        print(f"{WARN}ended WITHOUT shutting down — pid {record.get('pid')}, "
              f"started {record.get('started_iso')}.")
        print("      Full detail:  python run.py --why")
    else:
        print(f"{OK}stopped cleanly: {record.get('reason')}")


if __name__ == "__main__":
    sys.exit(main())
