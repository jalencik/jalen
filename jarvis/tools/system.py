"""
Local system actions — the things the router calls without ever touching an LLM.

Windows-only by design (pywin32 + ctypes). Everything degrades to a readable
error message rather than a traceback, because these get spoken out loud.

Media control uses Windows media keys rather than the Spotify Web API: as of
February 2026 Spotify requires a Premium subscription for developer access, and
media keys work with Spotify Free, YouTube, AIMP, VLC and anything else that
registers for them. Play/pause/skip/volume only — which is exactly what the
spec asked for.
"""
from __future__ import annotations

import ctypes
import os
import platform
import subprocess
from datetime import datetime

IS_WINDOWS = platform.system() == "Windows"

# Virtual key codes
VK = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "vol_up": 0xAF,
    "vol_down": 0xAE,
    "vol_mute": 0xAD,
}
KEYEVENTF_KEYUP = 0x0002


def _tap(vk: int) -> None:
    if not IS_WINDOWS:
        raise RuntimeError("Windows only")
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


# ----------------------------------------------------------------------- media
def media_play_pause() -> str:
    _tap(VK["play_pause"])
    return "Done."


def media_next() -> str:
    _tap(VK["next"])
    return "Skipped."


def media_previous() -> str:
    _tap(VK["previous"])
    return "Back one."


def volume_step(direction: str = "up", steps: int = 4) -> str:
    key = VK["vol_up"] if direction == "up" else VK["vol_down"]
    for _ in range(steps):
        _tap(key)
    return f"Volume {direction}."


def volume_mute_toggle(mute: bool = True) -> str:
    _tap(VK["vol_mute"])
    return "Muted." if mute else "Unmuted."


def volume_set(level: int) -> str:
    """Absolute volume needs the audio endpoint API; pycaw is the light way."""
    level = max(0, min(100, int(level)))
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume {level}."
    except Exception:
        # Fall back to stepping — imprecise but always works
        for _ in range(50):
            _tap(VK["vol_down"])
        for _ in range(round(level / 2)):
            _tap(VK["vol_up"])
        return f"Roughly {level}."


# ------------------------------------------------------------------------ apps
_APP_ALIASES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "browser": "chrome.exe",
    "telegram": "Telegram.exe",
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "code": "code",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "spotify": "spotify.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "settings": "ms-settings:",
    "task manager": "taskmgr.exe",
    "aimp": "AIMP.exe",
    "zoom": "Zoom.exe",
    "photoshop": "photoshop.exe",
}


def open_app(name: str) -> str:
    key = (name or "").strip().lower()
    target = _APP_ALIASES.get(key, name)
    try:
        if target.startswith("ms-settings:") or target.startswith("http"):
            os.startfile(target)  # noqa: S606
        else:
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        return f"Opening {name}."
    except Exception as exc:
        return f"Couldn't open {name}: {exc}"


def open_folder(path: str) -> str:
    try:
        os.startfile(os.path.expandvars(os.path.expanduser(path)))  # noqa: S606
        return "Opened."
    except Exception as exc:
        return f"Couldn't open that folder: {exc}"


def lock_workstation() -> str:
    ctypes.windll.user32.LockWorkStation()
    return "Locked."


def focus_window(name: str) -> str:
    try:
        import uiautomation as auto

        window = auto.WindowControl(searchDepth=1, RegexName=f".*{name}.*")
        if not window.Exists(2, 0.3):
            return f"I can't find a window called {name}."
        window.SetActive()
        window.SetTopmost(False)
        return f"Switched to {name}."
    except ImportError:
        return "Window control isn't installed yet."
    except Exception as exc:
        return f"Couldn't switch: {exc}"


def window_state(state: str) -> str:
    try:
        import uiautomation as auto

        window = auto.GetForegroundControl().GetTopLevelControl()
        if state.startswith("min"):
            window.Minimize()
            return "Minimised."
        window.Maximize()
        return "Maximised."
    except Exception as exc:
        return f"Couldn't do that: {exc}"


# ----------------------------------------------------------------------- facts
def get_time() -> str:
    return datetime.now().strftime("It's %-I:%M %p." if not IS_WINDOWS else "It's %#I:%M %p.")


def get_date() -> str:
    fmt = "%A, %B %#d." if IS_WINDOWS else "%A, %B %-d."
    return datetime.now().strftime("It's " + fmt)


def get_battery() -> str:
    try:
        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            return "I can't read the battery on this machine."
        plugged = " and charging" if battery.power_plugged else ""
        return f"Battery is at {int(battery.percent)} percent{plugged}."
    except Exception as exc:
        return f"Couldn't read the battery: {exc}"


def get_system_status() -> str:
    try:
        import psutil

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.4)
        disk = psutil.disk_usage("C:/" if IS_WINDOWS else "/")
        return (
            f"CPU {cpu:.0f} percent, memory {mem.percent:.0f} percent used with "
            f"{mem.available / 1e9:.1f} gigabytes free, disk {disk.percent:.0f} percent full."
        )
    except Exception as exc:
        return f"Couldn't read system status: {exc}"


def screenshot(path: str | None = None) -> str:
    try:
        from PIL import ImageGrab

        target = path or os.path.join(
            os.path.expanduser("~"), "Pictures", f"jarvis-{datetime.now():%Y%m%d-%H%M%S}.png"
        )
        os.makedirs(os.path.dirname(target), exist_ok=True)
        ImageGrab.grab(all_screens=True).save(target)
        return f"Saved to {os.path.basename(target)}."
    except Exception as exc:
        return f"Screenshot failed: {exc}"


# --------------------------------------------------------------------- dispatch
REGISTRY = {
    "media_play_pause": media_play_pause,
    "media_next": media_next,
    "media_previous": media_previous,
    "volume_step": volume_step,
    "volume_set": volume_set,
    "volume_mute_toggle": volume_mute_toggle,
    "open_app": open_app,
    "open_folder": open_folder,
    "focus_window": focus_window,
    "window_state": window_state,
    "lock_workstation": lock_workstation,
    "get_time": get_time,
    "get_date": get_date,
    "get_battery": get_battery,
    "get_system_status": get_system_status,
    "screenshot": screenshot,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
