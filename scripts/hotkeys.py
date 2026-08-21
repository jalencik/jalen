"""
The global hotkey. This is how Jalen starts without typing anything.

    Ctrl+Alt+J        wake him — launch him if he isn't running, listen if he is
    Ctrl+Alt+K        the kill switch (config/jarvis.yaml, safety.kill_switch_hotkey)

Run it with:
    .venv\\Scripts\\pythonw.exe scripts\\hotkeys.py

Four decisions worth knowing before changing anything here.

SEPARATE PROCESS, ON PURPOSE. The obvious design is a keyboard listener
thread inside Jalen. It cannot work: the hotkey's most important job is
starting him, and a listener living inside the thing it is supposed to start
is dead exactly when it is needed. This process outlives him, and survives
him crashing.

RegisterHotKey, NOT A KEYBOARD HOOK. The `keyboard` package installs a
low-level hook that sees every keystroke on the machine — including
passwords typed into other applications — and on Windows frequently wants
administrator rights. RegisterHotKey asks the OS to deliver ONE specific
combination and nothing else. It needs no new dependency, no admin, and it
cannot observe anything it was not given. For software that is going to hold
someone's credentials, "cannot see your keystrokes" is worth more than the
convenience of a nicer API.

FALLBACKS, BECAUSE COMBINATIONS COLLIDE. Found live on this machine:
Ctrl+Alt+Space — the combination config/jarvis.yaml has promised as the kill
switch since day one — is already owned by another application.
RegisterHotKey returned failure, and because this runs under pythonw there
was no console for the error to appear in, so the kill switch simply did
nothing and looked like a bug in Jalen. Each binding now has a list of
candidates and takes the first that is free.

FAILURES GO TO A FILE. There is no console. Anything worth saying is written
to data/hotkeys.log, so "which key actually works" is answerable after the
fact instead of being a guess.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import runtime  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402

LOG_PATH = ROOT / "data" / "hotkeys.log"

# --- Win32 --------------------------------------------------------------------
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000     # holding the keys down fires once, not fifty times
WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN,
}

# Only the keys worth binding. A full VK table would invite combinations that
# collide with the OS's own (Ctrl+Alt+Del cannot be taken at all).
KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "insert": 0x2D,
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}

# Single-instance guard. A named mutex rather than a PID file because the OS
# releases it when the process dies, however it dies — there is no stale
# state to clean up after a crash.
MUTEX_NAME = "Global\\JalenHotkeyListener"
ERROR_ALREADY_EXISTS = 183

_MUTEX_HANDLE = None


def log(message: str) -> None:
    """Say it on stdout AND on disk — under pythonw there is no stdout."""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}"
    print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """
    "ctrl+alt+j" -> (modifiers, virtual key code). None if unparseable.

    Returns None rather than raising: a typo in a config file should cost you
    one hotkey and a line in the log, not the whole listener — including the
    other hotkey, which might be the one that stops a runaway assistant.
    """
    mods = 0
    key = None
    for part in str(spec).lower().replace(" ", "").split("+"):
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        elif part.upper() in KEYS:
            key = KEYS[part.upper()]
        elif part in KEYS:
            key = KEYS[part]
        else:
            return None
    if key is None or mods == 0:
        return None      # a bare key with no modifier would swallow typing
    return mods | MOD_NOREPEAT, key


def _candidates(config_path: str, default: str, fallbacks: list[str]) -> list[str]:
    configured = CONFIG.get_path(config_path, default) or default
    ordered = [configured] + [f for f in fallbacks if f != configured]
    return ordered


def _python_for_background() -> Path:
    """pythonw.exe, so the assistant starts without a console window."""
    candidate = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate
    sibling = Path(sys.executable).with_name("pythonw.exe")
    return sibling if sibling.exists() else Path(sys.executable)


def launch_jalen() -> None:
    """
    Start Jalen unmuted, detached from this process.

    Unmuted specifically: pressing the wake key is an unambiguous request to
    be listened to, and starting muted in response to it would be an
    assistant that ignores the button you pressed to summon it. That is
    different from the login autostart, which honours startup.start_muted
    because nobody asked for anything by logging in.

    DETACHED_PROCESS so Jalen is not a child that dies when this listener is
    restarted or killed.
    """
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [str(_python_for_background()), str(ROOT / "run.py"), "--unmuted"],
        cwd=str(ROOT),
        creationflags=creationflags,
        close_fds=True,
    )
    log("wake pressed — Jalen was not running, launched him")


def on_wake() -> None:
    """Whatever is in the way of being heard, remove it."""
    if runtime.running_instance() is None:
        launch_jalen()
    else:
        runtime.send_signal("toggle")


def on_kill() -> None:
    """The kill switch. Stops him cleanly, or forcefully if he won't."""
    log("kill pressed — " + runtime.stop_running_instance())


def _claim_single_instance() -> bool:
    """
    True if we are the only listener. The handle is deliberately kept in a
    module global: releasing it would release the claim, and this process
    holds it for its whole life by design.
    """
    global _MUTEX_HANDLE
    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def _register_first_available(user32, hotkey_id: int, specs: list[str], purpose: str):
    """Take the first combination that is free. Returns its label, or None."""
    kernel32 = ctypes.windll.kernel32
    for spec in specs:
        parsed = parse_hotkey(spec)
        if parsed is None:
            log(f"{purpose}: cannot understand {spec!r} — skipping it")
            continue
        mods, vk = parsed
        kernel32.SetLastError(0)
        if user32.RegisterHotKey(None, hotkey_id, mods, vk):
            return spec
        err = kernel32.GetLastError()
        why = ("already owned by another application"
               if err == ERROR_HOTKEY_ALREADY_REGISTERED else f"Win32 error {err}")
        log(f"{purpose}: {spec} unavailable — {why}")
    return None


def main() -> int:
    if sys.platform != "win32":
        print("Windows only — RegisterHotKey is a Win32 API.")
        return 1

    if not _claim_single_instance():
        log("a hotkey listener is already running — nothing to do")
        return 0

    user32 = ctypes.windll.user32
    wanted = [
        (1, _candidates("startup.wake_hotkey", "ctrl+alt+j",
                        ["ctrl+shift+j", "ctrl+alt+f9"]), "wake", on_wake),
        (2, _candidates("safety.kill_switch_hotkey", "ctrl+alt+k",
                        ["ctrl+shift+k", "ctrl+alt+f10"]), "kill switch", on_kill),
    ]

    actions: dict[int, tuple[str, str, object]] = {}
    for hotkey_id, specs, purpose, action in wanted:
        won = _register_first_available(user32, hotkey_id, specs, purpose)
        if won:
            actions[hotkey_id] = (won, purpose, action)
            log(f"{purpose}: bound to {won}")

    if not actions:
        log("no hotkeys could be registered — every candidate is taken")
        return 1

    msg = wt.MSG()
    try:
        # GetMessage blocks until a message arrives, so this loop costs no
        # CPU at all while idle — it is not a poll.
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam in actions:
                label, purpose, action = actions[msg.wParam]
                try:
                    action()
                except Exception as exc:  # noqa: BLE001
                    # One failed press must never take the listener down, or
                    # the recovery path for "Jalen is stuck" dies with Jalen.
                    log(f"{purpose} ({label}) failed: {exc}")
    except KeyboardInterrupt:
        pass
    finally:
        for hotkey_id in actions:
            user32.UnregisterHotKey(None, hotkey_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
