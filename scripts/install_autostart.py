"""
Register Jarvis to start with Windows (spec C23: yes, but start muted).

Creates a shortcut in the user's Startup folder. Run:
    python scripts/install_autostart.py           # install
    python scripts/install_autostart.py --remove  # undo
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STARTUP = Path(os.path.expandvars(
    r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
))
SHORTCUT = STARTUP / "Jarvis.lnk"
LAUNCHER = ROOT / "start_jarvis.vbs"


def write_launcher() -> None:
    """A .vbs wrapper so Windows doesn't flash a console window at login."""
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
    LAUNCHER.write_text(
        'Set s = CreateObject("WScript.Shell")\n'
        f's.CurrentDirectory = "{ROOT}"\n'
        f's.Run """{pythonw}"" ""{ROOT / "run.py"}""", 0, False\n',
        encoding="utf-8",
    )


def install() -> int:
    if os.name != "nt":
        print("Windows only.")
        return 1
    write_launcher()
    try:
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortcut(str(SHORTCUT))
        link.TargetPath = str(LAUNCHER)
        link.WorkingDirectory = str(ROOT)
        link.Description = "Jarvis voice assistant"
        link.Save()
    except ImportError:
        print("pywin32 missing. Run: pip install pywin32")
        return 1
    print(f"Installed: {SHORTCUT}")
    print("Jarvis will start muted at login. Say 'unmute' or use the tray icon.")
    return 0


def remove() -> int:
    for path in (SHORTCUT, LAUNCHER):
        if path.exists():
            path.unlink()
            print(f"Removed: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(remove() if "--remove" in sys.argv else install())
