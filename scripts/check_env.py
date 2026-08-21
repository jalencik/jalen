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

    claude_path = shutil.which("claude")
    if claude_path:
        print(f"{OK}claude CLI found")
        try:
            # Windows: "claude" resolves to an npm-installed claude.CMD shim.
            # subprocess.run(["claude", ...], shell=False) can't launch a bare
            # name with an implicit .CMD extension — it raises FileNotFoundError
            # (WinError 2), silently swallowed below as "couldn't verify" even
            # though auth is fine. Passing the already-resolved full path (which
            # includes the extension) works on every platform.
            result = subprocess.run(
                [claude_path, "-p", "reply with the single word: ok"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and "ok" in result.stdout.lower():
                print(f"{OK}authenticated — your Claude plan is working")
            else:
                print(f"{WARN}claude CLI is installed but not authenticated. Run: claude setup-token")
        except Exception as exc:
            print(f"{WARN}couldn't verify ({type(exc).__name__}: {exc}) — run `claude setup-token` if the brain fails")
    else:
        print(f"{WARN}claude CLI not on PATH. Install from https://claude.com/download,")
        print("      then run: claude setup-token")

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

    # NOT "python run.py": that advice fails twice over on Windows.
    # PowerShell refuses to run a script from the current folder without a
    # ".\" prefix, and bare "python" is a different install with none of
    # this project's packages. The launcher handles both.
    print("\n" + ("All good — start it with:  .\\jalen.ps1" if problems == 0
                  else f"{problems} blocking problem(s) above."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
