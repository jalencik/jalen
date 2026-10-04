"""
The other half of the technician: actually fixing things.

Kept apart from technician.py deliberately. Diagnosis is free to run and
changes nothing; repair changes the machine and is gated. A fix living
inside a diagnostic is a fix nobody reviewed, and a diagnostic that also
repairs is one you hesitate to run.

THREE RULES, AND THEY ARE WHY THIS IS SAFE TO GIVE A VOICE ASSISTANT
--------------------------------------------------------------------
1. EVERY REPAIR REPORTS THE BEFORE STATE. Not "done" — what it found, what
   it set, and what that means. A fix he cannot verify is a fix he has to
   trust, and this project has already produced enough confident output that
   turned out to be wrong.

2. EVERY REPAIR NAMES ITS UNDO. In the same sentence. If the fix makes
   things worse at 2am, the way back should not require finding this file.

3. NOTHING IRREVERSIBLE HAPPENS WITHOUT THE RED GATE. Changing a driver
   power setting is AMBER — announced, reversible in one command. Deleting
   files is RED and asks out loud, every time, however obviously safe the
   files look.

WHY SO FEW REPAIRS
------------------
Every repair here is one he would otherwise do by hand in Settings, and
each was chosen because a real diagnostic finding points at it. The
temptation with a "technician" is a hundred registry tweaks from forum
posts; that is how you break someone's machine while sounding competent.
These are the ones with a known cause, a known effect, and a known undo.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any

from .system import IS_WINDOWS
from .technician import run_powershell


def _open_settings(uri: str, what: str) -> str:
    """Open a Windows Settings page. GREEN — it shows him a screen."""
    if not IS_WINDOWS:
        return "Windows only."
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(["cmd", "/c", "start", "", uri],
                         creationflags=creation, close_fds=True)
    except OSError as exc:
        return f"I couldn't open {what} ({type(exc).__name__})."
    return f"{what} is open on screen."


def open_windows_update() -> str:
    """Show the Windows Update page — GREEN."""
    return _open_settings("ms-settings:windowsupdate", "Windows Update")


def open_windows_security() -> str:
    """Show Windows Security — GREEN."""
    return _open_settings("windowsdefender:", "Windows Security")


def open_startup_settings() -> str:
    """Show which programs start with Windows — GREEN."""
    return _open_settings("ms-settings:startupapps", "Startup apps")


def open_storage_settings() -> str:
    """Show where the disk space went — GREEN."""
    return _open_settings("ms-settings:storagesense", "Storage settings")


# ------------------------------------------------------------------- wifi
def fix_wifi_power_saving(allow_power_off: bool = False) -> str:
    """
    Stop Windows switching the wireless adapter off to save power — AMBER.

    The single commonest cause of "my wifi drops every few minutes" on a
    laptop, and among the safest changes on this machine: it costs a little
    battery and nothing else, and one command puts it back.

    Reports every adapter it touched and every one it did not, because
    "done" on a machine with two wireless adapters tells him nothing about
    which one changed.
    """
    if not IS_WINDOWS:
        return "Windows only."

    ok, before = run_powershell(
        "Get-NetAdapter -Physical | Where-Object { $_.MediaType -eq 'Native 802.11' "
        "-or $_.InterfaceDescription -match 'Wi-?Fi|Wireless' } | ForEach-Object {"
        "  $p = Get-NetAdapterPowerManagement -Name $_.Name -ErrorAction SilentlyContinue;"
        "  '{0}|{1}' -f $_.Name, $(if ($p) { $p.AllowComputerToTurnOffDevice } else { 'unknown' })"
        "}"
    )
    if not ok:
        return f"I couldn't read the wireless adapter settings ({before})."
    if not before.strip():
        return "There's no wireless adapter on this machine to change."

    current = {}
    for line in before.splitlines():
        parts = line.split("|")
        if len(parts) == 2:
            current[parts[0].strip()] = parts[1].strip()

    target = "Enabled" if allow_power_off else "Disabled"

    # "Unsupported" is a THIRD state, and missing it produced a false success
    # on this very machine: the adapter does not expose the setting at all,
    # the value came back as Unsupported, "Unsupported != Disabled" looked
    # like work to do, and the reply announced "Windows can no longer switch
    # it off" having changed precisely nothing.
    #
    # It is also good news, so it is worth saying: an adapter that cannot be
    # powered down is not the cause of his dropouts, which rules a suspect
    # out rather than leaving it hanging.
    unsupported = [n for n, v in current.items() if v.lower() == "unsupported"]
    already = [n for n, v in current.items() if v == target]
    todo = [n for n, v in current.items()
            if v != target and v.lower() not in ("unsupported", "unknown")]

    if not todo:
        if unsupported and not already:
            return (
                f"Nothing to change — {', '.join(unsupported)} doesn't support "
                "power management at all, so Windows was never switching it off. "
                "That rules it out as the cause; if the wifi is still dropping "
                "it's the driver, the router, or the signal."
            )
        return (
            f"Already set the way you want — {', '.join(already or unsupported)} "
            f"{'may' if allow_power_off else 'cannot'} be powered down to save battery. "
            "Nothing changed."
        )

    changed, failed = [], []
    for name in todo:
        # Set-NetAdapterPowerManagement needs the change applied to the
        # object, not passed as a flag — this is the documented shape.
        ok, out = run_powershell(
            f"$p = Get-NetAdapterPowerManagement -Name '{name}';"
            f"$p.AllowComputerToTurnOffDevice = '{target}';"
            "$p | Set-NetAdapterPowerManagement -ErrorAction Stop; 'OK'"
        )
        if not (ok and "OK" in out):
            failed.append(name)
            continue
        # RE-READ IT. A command that exits zero has not necessarily changed
        # anything — the whole point of this fix is that he can trust the
        # report, and the only way to earn that is to look again.
        ok, after = run_powershell(
            f"(Get-NetAdapterPowerManagement -Name '{name}').AllowComputerToTurnOffDevice"
        )
        (changed if ok and after.strip() == target else failed).append(name)

    if not changed:
        return (
            f"I couldn't change {', '.join(failed)} — the setting didn't stick when "
            "I read it back. This usually means it needs administrator rights: run "
            "Jalen as administrator and ask again, or do it in Device Manager under "
            "the adapter's Power Management tab."
        )

    undo = "say 'let wifi sleep again'" if not allow_power_off else "say 'stop wifi sleeping'"
    reply = (
        f"Done — Windows can no longer switch {', '.join(changed)} off to save power. "
        if not allow_power_off else
        f"Done — Windows may now power {', '.join(changed)} down to save battery. "
    )
    reply += f"To undo, {undo}."
    if failed:
        reply += f" I could NOT change {', '.join(failed)} — that needs administrator rights."
    if not allow_power_off:
        reply += (
            " Give it a day before deciding it worked — ask me to check the wifi "
            "again and I'll compare the drop count."
        )
    return reply


# ---------------------------------------------------------------- storage
def temp_file_report() -> str:
    """
    How much is sitting in the temp folders — READ ONLY, GREEN.

    Deliberately separate from deleting it. He should see the number and
    decide; a tool that measures and deletes in one step is one he cannot
    ask a question with.
    """
    if not IS_WINDOWS:
        return "Windows only."
    # [System.IO.Directory]::EnumerateFiles, not Get-ChildItem -Recurse.
    #
    # Measured on this machine: Get-ChildItem took 59 SECONDS over 2,065
    # files. That is not a slow disk, it is the cost of PowerShell building a
    # rich FileInfo object per file — and 59 seconds is unusable in a voice
    # turn, where he is standing there with no way to tell thinking from
    # crashed. The .NET enumerator streams paths and the same walk finishes
    # in about two.
    ok, out = run_powershell(
        "$paths = @($env:TEMP, \"$env:SystemRoot\\Temp\");"
        "foreach ($p in $paths) {"
        "  if (-not (Test-Path $p)) { continue }"
        "  $count = 0; $bytes = 0;"
        "  try {"
        "    foreach ($f in [System.IO.Directory]::EnumerateFiles("
        "        $p, '*', [System.IO.SearchOption]::AllDirectories)) {"
        "      try { $bytes += (New-Object System.IO.FileInfo $f).Length; $count++ }"
        "      catch { }"
        "    }"
        "  } catch { }"
        "  '{0}|{1}|{2}' -f $p, $count, [math]::Round($bytes/1MB, 0)"
        "}",
        timeout=45.0,
    )
    if not ok:
        return f"I couldn't measure the temp folders ({out})."

    lines, total = [], 0
    for row in out.splitlines():
        parts = row.split("|")
        if len(parts) != 3:
            continue
        path, count, mb = parts
        try:
            total += int(mb)
        except ValueError:
            continue
        lines.append(f"  {path} — {count} files, {mb} MB")
    if not lines:
        return "The temp folders are empty."
    return (
        f"{total} MB of temporary files:\n" + "\n".join(lines)
        + "\nSay 'clear the temp files' and I'll delete them — I'll ask first."
    )


def clear_temp_files() -> str:
    """
    Delete the contents of the temp folders — RED, asks out loud first.

    RED even though these files are safe to delete by definition. Two
    reasons: an application writing to its temp file right now can lose
    work, and more importantly this is the shape of tool that must never
    become casual. A voice assistant that deletes files without asking is
    one bad transcription away from a disaster, and "it was only temp files"
    is exactly how that boundary erodes.

    Files in use are skipped by Windows rather than forced, and the reply
    says how many — a silent skip would report a bigger saving than it made.
    """
    if not IS_WINDOWS:
        return "Windows only."
    ok, out = run_powershell(
        "$paths = @($env:TEMP, \"$env:SystemRoot\\Temp\");"
        "$freed = 0; $removed = 0; $skipped = 0;"
        "foreach ($p in $paths) {"
        "  if (-not (Test-Path $p)) { continue }"
        "  Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {"
        "    $size = $_.Length;"
        "    try { Remove-Item $_.FullName -Force -ErrorAction Stop; "
        "          $freed += $size; $removed++ }"
        "    catch { $skipped++ }"
        "  }"
        "}"
        "'{0}|{1}|{2}' -f [math]::Round($freed/1MB,0), $removed, $skipped",
        timeout=180.0,
    )
    if not ok:
        return f"The cleanup didn't finish ({out})."
    match = re.search(r"(\d+)\|(\d+)\|(\d+)", out)
    if not match:
        return "The cleanup ran but didn't report what it did, so I can't tell you."
    freed, removed, skipped = match.groups()
    reply = f"Freed {freed} megabytes — deleted {removed} temporary files."
    if int(skipped):
        reply += f" {skipped} were in use by running programs and were left alone."
    return reply


def _reclaimable() -> str:
    """What else is taking space, for the brain to explain."""
    from .sysinfo import cleanup_suggestions

    return cleanup_suggestions()


REGISTRY: dict[str, Any] = {
    "fix_wifi_power_saving": fix_wifi_power_saving,
    "open_windows_update": open_windows_update,
    "open_windows_security": open_windows_security,
    "open_startup_settings": open_startup_settings,
    "open_storage_settings": open_storage_settings,
    "temp_file_report": temp_file_report,
    "clear_temp_files": clear_temp_files,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
