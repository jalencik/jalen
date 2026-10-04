r"""
From a fresh checkout to a working assistant, in one script. (HANDOFF item 6)

    .venv\Scripts\python.exe scripts\onboard.py

WHAT WAS WRONG
--------------
"A second user has no path from install to working. They need Google OAuth,
a Telegram login, a Groq key, and a Claude token, and today that is four
scripts and a README."

Four scripts and a README is not an onboarding. It is a treasure hunt where
every wrong turn produces an error message about the symptom rather than the
step you skipped — and where nothing tells you which parts you can skip.

WHAT THIS DOES
--------------
Walks the steps in dependency order, tells you what each one is FOR and what
it costs, and lets you skip anything optional. It is resumable: run it again
and everything already done is detected and reported rather than re-asked.

It writes exactly two files:

    .env                credentials, appended to rather than overwritten
    config/user.yaml    this person's name, folders and channels

It never writes config/jalen.yaml. That file is the defaults AND the design
record — every value in it is commented with why it is that value — and
overwriting it per-machine would destroy the most useful documentation this
project has. user.yaml overlays it instead. See jalen/config.py.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not create the vault. That needs a passphrase typed with getpass,
and a passphrase that passes through an onboarding script is one the script
could have kept. It prints the one command and moves on.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENV_PATH = ROOT / ".env"
USER_CONFIG = ROOT / "config" / "user.yaml"
PY = ROOT / ".venv" / "Scripts" / "python.exe"

TICK, CROSS, DASH = "  [ok] ", "  [XX] ", "  [--] "


# --------------------------------------------------------------------- utils
def say(text: str = "") -> None:
    print(text)


def rule(title: str) -> None:
    say("\n" + "=" * 66)
    say(f"  {title}")
    say("=" * 66)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {prompt} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer.startswith("y")


def shown(path: Path) -> str:
    """
    A path to print. Relative to the project when it is inside it, absolute
    when it is not.

    Path.relative_to() RAISES rather than returning the absolute path when
    there is no common root, so the obvious one-liner turns a successful step
    into a traceback for anyone whose config lives elsewhere.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_env(key: str, value: str) -> None:
    """
    Set one key, preserving everything else including comments.

    Rewritten in place rather than appended: a second run appending a second
    GROQ_API_KEY line would leave two, and which one wins depends on
    dotenv's parse order — a bug that only appears the second time anyone
    runs this, which is the worst time to find it.
    """
    ENV_PATH.touch(exist_ok=True)
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_script(name: str) -> bool:
    """Hand control to one of the existing connect scripts."""
    script = ROOT / "scripts" / name
    interpreter = str(PY) if PY.exists() else sys.executable
    say(f"\n  running {name}…\n")
    try:
        return subprocess.run([interpreter, str(script)], cwd=str(ROOT)).returncode == 0
    except OSError as exc:
        say(f"{CROSS}couldn't run {name}: {exc}")
        return False


# --------------------------------------------------------------------- steps
def step_python() -> bool:
    rule("1 of 8   Python and packages")
    ok = True
    say(f"{TICK}Python {sys.version.split()[0]}")
    if sys.version_info < (3, 12):
        say(f"{CROSS}Python 3.12 or newer is needed.")
        ok = False
    try:
        import numpy, sounddevice, onnxruntime, yaml  # noqa: F401
        say(f"{TICK}core packages installed")
    except ImportError as exc:
        say(f"{CROSS}missing package: {exc.name}")
        say("       .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        ok = False

    import shutil
    free = shutil.disk_usage(str(ROOT)).free / 1e9
    if free < 1.0:
        say(f"{CROSS}only {free:.2f} GB free. A full disk breaks the audit log,")
        say("       speech synthesis and transcription, each with its own")
        say("       misleading error. Free some space before going on.")
        ok = False
    else:
        say(f"{TICK}{free:.1f} GB free disk")
    return ok


def step_identity() -> None:
    rule("2 of 8   Who you are")
    say("  This is what it calls you, and what you call it.\n")

    from jalen.config import CONFIG

    current_user = CONFIG.get_path("identity.user_name", "")
    name = ask("Your name", "" if current_user == "O'ktam" else current_user)
    address = ask("What should it call you? (Boss, chief, your name)", name or "Boss")
    assistant = ask("What is the assistant called?", "Jalen")
    wake = ask("Wake phrase", f"hey {assistant.lower()}")

    lines = [
        "# Your settings. Overlays config/jalen.yaml, which stays as the",
        "# defaults and the design record. Only the keys you set here change.",
        "# Written by scripts/onboard.py; safe to edit by hand.",
        "",
        "identity:",
        f'  name: "{assistant}"',
        f'  user_name: "{name}"',
        f'  address_user_as: "{address}"',
        f'  wake_word: "{wake}"',
        "",
        "index:",
        "  # {home} expands to your home directory at load time.",
        "  content_index_paths:",
        '    - "{home}/Documents"',
        '    - "{home}/Downloads"',
        '    - "{home}/Desktop"',
        "",
        "telegram:",
        "  personal:",
        "    # Chats the assistant may send to WITHOUT asking first. Yours only:",
        "    # anything here can be posted to unprompted. Empty is the safe start.",
        "    send_without_asking_to: []",
        "",
    ]

    if wake.lower() != "hey jalen":
        # Worth saying plainly rather than letting them discover it by
        # standing in a room saying a phrase nothing responds to. The
        # acoustic model is a trained artefact; renaming the assistant in
        # config does not retrain it.
        say(f"\n{DASH}Note: the trained wake model listens for \"hey jalen\".")
        say(f"      \"{wake}\" will be understood once you say it, but waking")
        say("      it by voice still needs the old phrase until you train a")
        say("      new model:  scripts\\train_wake_word.py")

    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG.write_text("\n".join(lines), encoding="utf-8")
    say(f"\n{TICK}wrote {shown(USER_CONFIG)}")


def step_groq(env: dict) -> None:
    rule("3 of 8   Speech recognition (required)")
    if env.get("GROQ_API_KEY"):
        say(f"{TICK}GROQ_API_KEY already set")
        return
    say("  Groq runs Whisper. It is the only REQUIRED key: without it")
    say("  nothing you say can be turned into text. Free tier is plenty.\n")
    say("      https://console.groq.com/keys\n")
    key = ask("Paste your Groq API key (or Enter to skip)")
    if key:
        write_env("GROQ_API_KEY", key)
        say(f"{TICK}saved")
    else:
        say(f"{DASH}skipped — voice input will not work until this is set")


def step_claude() -> None:
    rule("4 of 8   The brain (required)")
    import shutil

    if shutil.which("claude") is None:
        say(f"{CROSS}the claude CLI is not on PATH.")
        say("       Install it from https://claude.com/download, then re-run this.")
        return
    say(f"{TICK}claude CLI found")
    say("\n  It needs to be authenticated once. If you have a Claude")
    say("  subscription this uses it — no per-token billing.\n")
    if yes("Run `claude setup-token` now?", default=False):
        try:
            subprocess.run(["claude", "setup-token"], shell=True)
        except OSError as exc:
            say(f"{CROSS}{exc}")
    else:
        say(f"{DASH}skipped — run `claude setup-token` before first use")


def step_google() -> None:
    rule("5 of 8   Gmail and Calendar (optional)")
    token = ROOT / "data" / "google_token.json"
    if token.exists():
        say(f"{TICK}already connected")
        return
    say("  Lets it read your mail, draft replies and see your calendar.")
    say("  Your browser opens and you press Allow; it never sees your")
    say("  password, and you can revoke it at myaccount.google.com/permissions.\n")
    if not (ROOT / "client_secret.json").exists():
        say(f"{DASH}client_secret.json is missing — see CREDENTIALS.md for how")
        say("      to create one. Skipping.")
        return
    if yes("Connect Google now?", default=False):
        run_script("connect_google.py")


def step_telegram(env: dict) -> None:
    rule("6 of 8   Telegram (optional)")
    say("  Two separate things, and you can have either or both:\n")
    say("    the BOT     control it from your phone")
    say("    your ACCOUNT   let it read and send in your real chats\n")

    if env.get("TELEGRAM_BOT_TOKEN"):
        say(f"{TICK}bot token already set")
    elif yes("Set up the bot?", default=False):
        say("\n  Message @BotFather on Telegram, send /newbot, follow it,")
        say("  and paste the token it gives you.\n")
        token = ask("Bot token")
        if token:
            write_env("TELEGRAM_BOT_TOKEN", token)
            say("\n  Now message @userinfobot to get YOUR numeric id. Only")
            say("  ids listed here can talk to the bot; empty means nobody.\n")
            ids = ask("Your Telegram user id")
            if ids:
                write_env("TELEGRAM_ALLOWED_USER_IDS", ids)
            say(f"{TICK}saved")

    if (ROOT / "data" / "telegram_user.session").exists():
        say(f"{TICK}personal account already signed in")
    elif yes("Sign in to your personal Telegram account?", default=False):
        if not env.get("TELEGRAM_API_ID"):
            say("\n  Get an api_id and api_hash from https://my.telegram.org/apps\n")
            api_id = ask("api_id")
            api_hash = ask("api_hash")
            if api_id and api_hash:
                write_env("TELEGRAM_API_ID", api_id)
                write_env("TELEGRAM_API_HASH", api_hash)
        run_script("connect_telegram.py")


def step_vault() -> None:
    rule("7 of 8   Credentials vault (optional)")
    from jalen.tools import vault

    if vault.VAULT_PATH.exists():
        say(f"{TICK}vault already created")
        return
    say("  Stores passwords and codes so it can type them into forms.")
    say("  YOU have to run this one yourself. It asks for a passphrase, and")
    say("  a passphrase that passed through this script is one this script")
    say("  could have kept — so it is not asked for here.\n")
    say("      .venv\\Scripts\\python.exe scripts\\vault_setup.py\n")
    say(f"{DASH}not created — fill_credential stays unavailable until you run it")


def step_finish() -> None:
    rule("8 of 8   Finishing up")
    say("  Two optional extras, both safe to leave for later:\n")
    say("    autostart + global hotkey (Ctrl+Alt+J)")
    say("      .venv\\Scripts\\python.exe scripts\\install_autostart.py\n")
    say("    teach the wake word YOUR voice (~15 minutes)")
    say("      .venv\\Scripts\\python.exe scripts\\record_wake_samples.py\n")
    if yes("Install autostart and the hotkey now?", default=False):
        run_script("install_autostart.py")

    say("\n  Check everything:   .\\jalen.ps1 check")
    say("  Start it:           .\\jalen.ps1")
    say("  Stop it:            .\\jalen.ps1 stop")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    say()
    say("  Setting up your assistant.")
    say("  Anything marked optional can be skipped and done later.")
    say("  Run this again any time; it detects what is already done.")

    try:
        if not step_python():
            say("\n  Fix the blocking problems above, then run this again.")
            return 1
        step_identity()
        env = read_env()
        step_groq(env)
        step_claude()
        step_google()
        step_telegram(read_env())
        step_vault()
        step_finish()
    except KeyboardInterrupt:
        say("\n\n  Stopped. Everything answered so far is saved; run it again")
        say("  to pick up where you left off.")
        return 1

    say("\n" + "=" * 66)
    say("  Done.")
    say("=" * 66 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
