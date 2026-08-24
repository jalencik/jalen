"""
Why does the Jalen extension say "not connected"?

Walks every link in the chain and reports which one is broken, with the
evidence. Written after a real "still not connected" report where the answer
turned out to be two absent ends rather than a bug - and there was no way to
see that without reading files by hand.

    Jalen app  ->  bridge.json  ->  registry  ->  host manifest
               ->  launcher     ->  native host  ->  Chrome extension

Run it any time:

    .venv\\Scripts\\python.exe scripts\\diagnose_extension.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOST_NAME = "com.jalen.bridge"
PINNED_ID = "emlfljokadcfdfedhkgkgecpgjfbhfkg"
BRIDGE_FILE = ROOT / "data" / "bridge.json"
LAUNCHER = ROOT / "jalen_bridge_host.bat"
HOST_MANIFEST = ROOT / "native_host_manifest.json"
EXT_DIR = ROOT / "browser_extension"

OK, BAD, WARN = "[ok]  ", "[XX]  ", "[~~]  "
problems: list[str] = []


def say(mark: str, label: str, detail: str = "") -> None:
    print(f"  {mark}{label:34s}{detail}")


def check_app() -> dict | None:
    """Is a Jalen app running, and is bridge.json its own (not stale)?"""
    if not BRIDGE_FILE.is_file():
        say(BAD, "Jalen app running", "no data/bridge.json - Jalen isn't running")
        problems.append("Start Jalen. The bridge only exists while it runs.")
        return None
    try:
        info = json.loads(BRIDGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        say(BAD, "bridge.json readable", f"{type(exc).__name__}")
        problems.append("data/bridge.json is corrupt - delete it and restart Jalen.")
        return None

    pid = info.get("pid")
    alive = False
    try:
        import psutil
        alive = isinstance(pid, int) and psutil.pid_exists(pid)
    except ImportError:
        alive = True                       # can't tell; don't fail on it

    if not alive:
        say(BAD, "Jalen app running", f"bridge.json is STALE (pid {pid} is gone)")
        problems.append(
            "Jalen isn't running - the bridge file is left over from a "
            "previous run. Start Jalen; it rewrites the file on startup.")
        return None

    say(OK, "Jalen app running", f"pid {pid}")

    port = info.get("port")
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=3):
            say(OK, "Bridge port listening", f"127.0.0.1:{port}")
    except OSError:
        say(BAD, "Bridge port listening", f"nothing on 127.0.0.1:{port}")
        problems.append("Jalen is running but its bridge port isn't open - "
                        "restart Jalen.")
        return None
    return info


def check_registration() -> bool:
    good = True
    if not HOST_MANIFEST.is_file():
        say(BAD, "Native host manifest", "missing")
        good = False
    else:
        try:
            man = json.loads(HOST_MANIFEST.read_text(encoding="utf-8"))
            origins = man.get("allowed_origins", [])
            expect = f"chrome-extension://{PINNED_ID}/"
            if expect in origins:
                say(OK, "Native host manifest", "allows the pinned extension id")
            else:
                say(BAD, "Native host manifest",
                    f"allows {origins} - not the pinned id")
                problems.append("Re-run scripts/install_extension.py")
                good = False
            if man.get("path") != str(LAUNCHER):
                say(WARN, "Manifest -> launcher path", man.get("path", "?"))
        except ValueError:
            say(BAD, "Native host manifest", "not valid JSON")
            good = False

    if not LAUNCHER.is_file():
        say(BAD, "Launcher .bat", "missing")
        problems.append("Re-run scripts/install_extension.py")
        good = False
    else:
        text = LAUNCHER.read_text(encoding="utf-8", errors="replace")
        if "cd /d" in text and str(ROOT) in text:
            say(OK, "Launcher .bat", "cd's to the project (the cwd fix)")
        else:
            say(BAD, "Launcher .bat", "no 'cd /d' - it WILL fail from Chrome")
            problems.append("Re-run scripts/install_extension.py")
            good = False

    try:
        import winreg
        key = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _ = winreg.QueryValueEx(k, "")
        if value == str(HOST_MANIFEST):
            say(OK, "Chrome registry entry", "points at the manifest")
        else:
            say(BAD, "Chrome registry entry", f"points at {value}")
            problems.append("Re-run scripts/install_extension.py")
            good = False
    except ImportError:
        say(WARN, "Chrome registry entry", "not Windows - skipped")
    except OSError:
        say(BAD, "Chrome registry entry", "not registered")
        problems.append("Run: .venv\\Scripts\\python.exe scripts\\install_extension.py")
        good = False
    return good


def check_extension_loaded() -> bool:
    """Has the extension actually been loaded into Chrome? The usual answer."""
    if not (EXT_DIR / "manifest.json").is_file():
        say(BAD, "Extension files", f"{EXT_DIR} has no manifest.json")
        return False
    say(OK, "Extension files", str(EXT_DIR))

    user_data = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data"
    if not user_data.is_dir():
        say(WARN, "Loaded into Chrome", "no Chrome user data found")
        return False

    needle_id, needle_path = PINNED_ID, "browser_extension"
    for prefs in list(user_data.glob("Profile*/Preferences")) + \
            list(user_data.glob("Default/Preferences")):
        try:
            text = prefs.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle_id in text or needle_path in text:
            say(OK, "Loaded into Chrome", f"found in {prefs.parent.name}")
            return True

    say(BAD, "Loaded into Chrome", "NOT loaded in any Chrome profile")
    problems.append(
        "Load it once: chrome://extensions -> Developer mode -> Load "
        f"unpacked -> {EXT_DIR}")
    return False


def main() -> int:
    print("\n  JALEN <-> CHROME EXTENSION - where the chain stands\n")
    check_app()
    print()
    check_registration()
    print()
    check_extension_loaded()

    print("\n  " + "=" * 58)
    if not problems:
        print("  Every link checks out. If the panel still says not "
              "connected,\n  fully quit and reopen Chrome - native hosts are "
              "read at startup.")
    else:
        print("  WHAT TO DO, in order:\n")
        for i, item in enumerate(problems, 1):
            print(f"   {i}. {item}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
