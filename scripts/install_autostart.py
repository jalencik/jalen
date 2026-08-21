"""
Make Jalen start with Windows, and make Ctrl+Alt+J work everywhere.

    python scripts\\install_autostart.py            install
    python scripts\\install_autostart.py --remove   undo
    python scripts\\install_autostart.py --status   what is installed right now

TWO entries are installed, not one, and the second is the important one:

  Jalen.lnk           starts the assistant at login, muted (startup.start_muted)
  Jalen Hotkeys.lnk   starts the global hotkey listener

The hotkey listener is separate because it has to survive Jalen. If it lived
inside him it would die with him — and the press that is supposed to bring
him back would do nothing, which is precisely the situation you reach for a
hotkey in. Separate, it can restart him from cold.

MUTED AT LOGIN IS DELIBERATE. Nobody asked for anything by logging in, and an
assistant that begins talking as the desktop appears is one you turn off. The
hotkey starts him unmuted, because pressing it IS asking.

NO pywin32. This used to import win32com.client, which is not in
requirements.txt, so the whole script failed on a clean machine with "pywin32
missing" — an install step that cannot install. Shortcut creation goes
through PowerShell's WScript.Shell instead, which ships with Windows.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STARTUP = Path(os.path.expandvars(
    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
))

# (shortcut name, .vbs shim, what it runs, arguments)
ENTRIES = [
    ("Jalen.lnk", "start_jalen.vbs", "run.py", []),
    ("Jalen Hotkeys.lnk", "start_jalen_hotkeys.vbs", "scripts/hotkeys.py", []),
]

# Files the OLD Jarvis-named install left behind. Removed on every install so
# a rename does not silently leave two assistants racing to open the same
# microphone at login — the exact "I saw two instances" failure runtime.py
# exists to prevent, arriving through the one door it cannot see.
LEGACY = ["Jarvis.lnk", "start_jarvis.vbs"]


def _pythonw() -> Path:
    """The windowless interpreter, so login does not flash a console."""
    candidate = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate
    fallback = Path(sys.executable).with_name("pythonw.exe")
    return fallback if fallback.exists() else Path(sys.executable)


def _write_shim(vbs_name: str, target: str, args: list[str]) -> Path:
    """
    A .vbs wrapper, because Windows shows a console window for a .py at login
    even under pythonw in some shell configurations. WScript.Run with a window
    style of 0 is the reliable way to get silence.
    """
    path = ROOT / vbs_name
    argline = " ".join(f'""{a}""' for a in args)
    argline = (" " + argline) if argline else ""
    path.write_text(
        'Set s = CreateObject("WScript.Shell")\n'
        f's.CurrentDirectory = "{ROOT}"\n'
        f's.Run """{_pythonw()}"" ""{ROOT / target}""{argline}", 0, False\n',
        encoding="utf-8",
    )
    return path


def _make_shortcut(link: Path, target: Path, description: str) -> None:
    script = (
        "$s = New-Object -ComObject WScript.Shell; "
        f"$l = $s.CreateShortcut('{link}'); "
        f"$l.TargetPath = '{target}'; "
        f"$l.WorkingDirectory = '{ROOT}'; "
        f"$l.Description = '{description}'; "
        "$l.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
    )


def install() -> int:
    if os.name != "nt":
        print("Windows only.")
        return 1
    if not (ROOT / ".venv" / "Scripts").exists():
        print("No .venv found. Create it first:  python -m venv .venv")
        return 1

    remove(quiet=True)
    STARTUP.mkdir(parents=True, exist_ok=True)
    for link_name, vbs_name, target, args in ENTRIES:
        shim = _write_shim(vbs_name, target, args)
        try:
            _make_shortcut(STARTUP / link_name, shim, f"Jalen — {target}")
        except subprocess.CalledProcessError as exc:
            print(f"Could not create {link_name}: {exc.stderr.decode(errors='replace').strip()}")
            return 1
        print(f"Installed: {STARTUP / link_name}")

    print()
    print("At login: Jalen starts muted, and Ctrl+Alt+J becomes live.")
    print("Press Ctrl+Alt+J to wake him. Ctrl+Alt+Space stops him.")
    print("Starting the hotkey listener now so you don't have to log out.")
    # Launch the interpreter directly, NOT the .vbs shim through shell=True.
    # That combination ran the shim twice — two listeners, 62ms apart, the
    # second one silently useless because the first already owned the key
    # combination. The shim exists for the login shortcut, where Windows
    # needs it to suppress a console window; here there is nothing to
    # suppress. hotkeys.py holds a named mutex, so a duplicate would exit on
    # its own now, but launching one cleanly beats relying on that.
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        if sys.platform == "win32" else 0
    )
    subprocess.Popen(
        [str(_pythonw()), str(ROOT / "scripts" / "hotkeys.py")],
        cwd=str(ROOT),
        creationflags=creationflags,
        close_fds=True,
    )
    return 0


def remove(quiet: bool = False) -> int:
    targets = [STARTUP / name for name, _v, _t, _a in ENTRIES]
    targets += [ROOT / vbs for _n, vbs, _t, _a in ENTRIES]
    targets += [STARTUP / name for name in LEGACY if name.endswith(".lnk")]
    targets += [ROOT / name for name in LEGACY if name.endswith(".vbs")]
    for path in targets:
        if path.exists():
            try:
                path.unlink()
                if not quiet:
                    print(f"Removed: {path}")
            except OSError as exc:
                print(f"Could not remove {path}: {exc}")
    return 0


def status() -> int:
    print(f"Startup folder: {STARTUP}")
    for link_name, vbs_name, target, _a in ENTRIES:
        link = STARTUP / link_name
        shim = ROOT / vbs_name
        mark = "yes" if link.exists() and shim.exists() else "no "
        print(f"  [{mark}] {link_name:20} -> {target}")
    for name in LEGACY:
        stale = (STARTUP / name) if name.endswith(".lnk") else (ROOT / name)
        if stale.exists():
            print(f"  [!!!] stale Jarvis-era entry still present: {stale}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Install Jalen's startup entries")
    parser.add_argument("--remove", action="store_true", help="uninstall")
    parser.add_argument("--status", action="store_true", help="show what is installed")
    parsed = parser.parse_args()
    if parsed.status:
        sys.exit(status())
    sys.exit(remove() if parsed.remove else install())
