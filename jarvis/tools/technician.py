"""
The IT technician: find out what is actually wrong with this machine.

He described the job precisely — "wifi disconnects every 5 minutes, or some
antivirus problem or old windows, or storage filling out" — and wanted an
assistant that hears a symptom and goes and finds the cause.

TWO LAYERS, AND THEY ARE SEPARATE ON PURPOSE
--------------------------------------------
This module only ever LOOKS. Every function here reads state and returns
findings; not one of them changes anything. Repairs live in repairs.py,
where each one is gated, reversible, and names what it changed.

That split is not tidiness. A diagnostic that also fixes cannot be run
casually — you would think twice before asking "why is my wifi dropping",
which is exactly the question that should be free to ask. And a fix that
lives inside a diagnostic is a fix nobody reviewed.

FINDINGS CARRY CONFIDENCE, AND SAY WHAT THEY DO NOT KNOW
--------------------------------------------------------
"Your wifi adapter is set to power down" is a fact. "That is why it drops
every five minutes" is a hypothesis, and a good one, but the disconnect
could equally be the router. Every finding says which it is, because the
failure mode this project keeps hitting is confident-sounding output that
was never verified — and a confident wrong diagnosis on someone's machine
costs them an evening.

EVERYTHING IS BOUNDED
---------------------
Every command runs under a timeout and every result is truncated. A
diagnostic that hangs is worse than one that finds nothing: he is waiting,
out loud, with no way to tell the difference between "thinking" and "dead".
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .system import IS_WINDOWS

# Long enough for Get-WinEvent over a big System log, short enough that a
# voice turn does not die waiting.
COMMAND_TIMEOUT_S = 25.0
MAX_OUTPUT_CHARS = 6000


@dataclass
class Finding:
    """One thing noticed about the machine."""

    area: str                      # "wifi", "storage", "updates", "security"
    summary: str                   # one sentence, spoken aloud
    severity: str = "info"         # info | warn | problem
    confidence: str = "observed"   # observed (a fact) | likely (a hypothesis)
    detail: str = ""               # the evidence, for the screen not the ear
    fix: str = ""                  # the repair that addresses it, by tool name
    unknown: str = ""              # what this check could NOT determine

    def spoken(self) -> str:
        mark = {"problem": "Problem", "warn": "Worth knowing", "info": "FYI"}[self.severity]
        hedge = "" if self.confidence == "observed" else " — probably, not certainly"
        return f"{mark}: {self.summary}{hedge}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "problem"]

    def render(self) -> str:
        """
        What gets returned to the brain: problems first, then what was
        checked and found clean, then what could not be checked at all.

        The third list matters as much as the first. "I looked at six things
        and two of them failed to run" is a different answer from "I looked
        at four things", and collapsing them is how a partial scan gets
        reported as a clean bill of health.
        """
        lines: list[str] = []
        ranked = sorted(
            self.findings,
            key=lambda f: ({"problem": 0, "warn": 1, "info": 2}[f.severity],
                           0 if f.confidence == "observed" else 1),
        )
        if not ranked:
            lines.append("Nothing wrong that I can see.")
        for finding in ranked:
            lines.append(f"- {finding.spoken()}")
            if finding.detail:
                lines.append(f"    {finding.detail}")
            if finding.fix:
                lines.append(f"    Fix available: {finding.fix}")
            if finding.unknown:
                lines.append(f"    Couldn't determine: {finding.unknown}")
        if self.checked:
            lines.append("")
            lines.append("Checked and clean: " + ", ".join(self.checked))
        if self.failed:
            lines.append(
                "COULD NOT CHECK: " + ", ".join(self.failed)
                + " — so this is not a clean bill of health for those."
            )
        return "\n".join(lines)


def run_powershell(script: str, timeout: float = COMMAND_TIMEOUT_S) -> tuple[bool, str]:
    """
    Run a read-only PowerShell snippet. Returns (ok, output).

    -NonInteractive and -NoProfile: a profile that prompts, or a cmdlet that
    asks for confirmation, would hang until the timeout with no way for the
    user to answer — there is no console attached to a voice turn.

    CREATE_NO_WINDOW for the same reason the Claude CLI gets it: Jalen runs
    under pythonw, so any child would otherwise flash a console window over
    whatever he is doing.
    """
    if not IS_WINDOWS:
        return False, "Windows only."
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # UTF-8 on both sides. Without it, a device name in any non-Latin script
    # comes back as mojibake — this machine's wireless adapter is named in
    # Cyrillic, and it was being read back as "???????????? ????" and then
    # spoken aloud that way.
    script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + script
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=creation, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:.0f}s"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0 and not out:
        return False, err[:MAX_OUTPUT_CHARS] or f"exit code {result.returncode}"
    return True, out[:MAX_OUTPUT_CHARS]


# --------------------------------------------------------------------- wifi
def _wifi_findings(report: Report) -> None:
    """
    The symptom he named: "wifi disconnects every 5 minutes".

    Three independent things can cause that and they need different fixes,
    so all three are checked rather than guessing at the commonest:

      1. The adapter is allowed to power down to save energy. By far the
         most frequent cause of periodic drops on a laptop, and a one-line
         reversible fix.
      2. The signal is genuinely weak. No setting change helps; he needs to
         move or change channel.
      3. Windows is disconnecting for its own reasons — roaming
         aggressiveness, a driver fault — which shows up in the event log.
    """
    ok, out = run_powershell(
        "$a = Get-NetAdapter -Physical | Where-Object { $_.MediaType -eq 'Native 802.11' "
        "-or $_.InterfaceDescription -match 'Wi-?Fi|Wireless' };"
        "if (-not $a) { 'NO_WIFI_ADAPTER'; exit };"
        "foreach ($n in $a) {"
        "  $p = Get-NetAdapterPowerManagement -Name $n.Name -ErrorAction SilentlyContinue;"
        "  '{0}|{1}|{2}' -f $n.Name, $n.Status, $(if ($p) { $p.AllowComputerToTurnOffDevice } else { 'unknown' })"
        "}"
    )
    if not ok:
        report.failed.append("wifi adapter settings")
    elif "NO_WIFI_ADAPTER" in out:
        report.checked.append("wifi (no wireless adapter on this machine)")
    else:
        powered_down = []
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) != 3:
                continue
            name, status, power = parts
            if power.strip().lower() == "enabled":
                powered_down.append(name)
        if powered_down:
            report.add(Finding(
                area="wifi",
                severity="problem",
                confidence="likely",
                summary=(
                    "Windows is allowed to switch your wireless adapter off to save "
                    "power, which is the usual cause of wifi dropping every few minutes"
                ),
                detail=f"Adapter(s): {', '.join(powered_down)}",
                fix="fix_wifi_power_saving",
                unknown="whether the drops are actually caused by this or by the router",
            ))
        else:
            report.checked.append("wifi power management")

    ok, out = run_powershell("netsh wlan show interfaces")
    if not ok:
        report.failed.append("wifi signal strength")
        return
    signal = re.search(r"Signal\s*:\s*(\d+)%", out)
    state = re.search(r"State\s*:\s*(\w+)", out)
    if signal:
        strength = int(signal.group(1))
        if strength < 40:
            report.add(Finding(
                area="wifi", severity="problem", confidence="observed",
                summary=f"your wifi signal is weak at {strength} percent",
                detail="Below about 40% the connection drops on its own. "
                       "No setting fixes this — move closer or change channel.",
            ))
        elif strength < 65:
            report.add(Finding(
                area="wifi", severity="warn", confidence="observed",
                summary=f"wifi signal is middling at {strength} percent",
            ))
        else:
            report.checked.append(f"wifi signal ({strength}%)")
    elif state and state.group(1).lower() != "connected":
        report.add(Finding(
            area="wifi", severity="warn", confidence="observed",
            summary="wifi is not connected right now",
        ))


def _wifi_drop_history(report: Report) -> None:
    """
    How often it ACTUALLY dropped, from the event log.

    This is what turns "my wifi keeps dropping" from a complaint into a
    number, and it is the only way to tell whether a fix worked. Without it
    the answer to "did that help" is a shrug.
    """
    ok, out = run_powershell(
        "$since = (Get-Date).AddDays(-2);"
        "$e = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$since} "
        "-ErrorAction SilentlyContinue | Where-Object { "
        "$_.ProviderName -match 'WLAN|Netwtw|Wlansvc|NativeWifi' -and "
        "$_.Message -match 'disconnect|dropped|lost|failed' };"
        "if ($e) { $e.Count } else { 0 }",
        timeout=30.0,
    )
    if not ok:
        report.failed.append("wifi disconnect history")
        return
    match = re.search(r"(\d+)", out)
    if not match:
        report.failed.append("wifi disconnect history")
        return
    count = int(match.group(1))
    if count >= 20:
        report.add(Finding(
            area="wifi", severity="problem", confidence="observed",
            summary=f"your wifi has dropped {count} times in the last two days",
            detail="That is roughly " + f"{count / 48:.1f}" + " times an hour.",
        ))
    elif count > 0:
        report.add(Finding(
            area="wifi", severity="info", confidence="observed",
            summary=f"wifi dropped {count} times in the last two days",
        ))
    else:
        report.checked.append("wifi disconnect history (none logged)")


# ------------------------------------------------------------------ storage
def _storage_findings(report: Report) -> None:
    ok, out = run_powershell(
        "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | "
        "ForEach-Object { '{0}|{1}|{2}' -f $_.DeviceID, $_.Size, $_.FreeSpace }"
    )
    if not ok:
        report.failed.append("disk space")
        return
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        drive, size, free = parts
        try:
            size_gb, free_gb = int(size) / 2**30, int(free) / 2**30
        except ValueError:
            continue
        if size_gb <= 0:
            continue
        pct = free_gb / size_gb * 100
        if pct < 8:
            report.add(Finding(
                area="storage", severity="problem", confidence="observed",
                summary=f"drive {drive} is nearly full — {free_gb:.0f} gigabytes left of {size_gb:.0f}",
                detail="Below about 10% free, Windows slows down and updates start failing.",
                fix="cleanup_suggestions",
            ))
        elif pct < 15:
            report.add(Finding(
                area="storage", severity="warn", confidence="observed",
                summary=f"drive {drive} is getting full — {free_gb:.0f} gigabytes left",
                fix="cleanup_suggestions",
            ))
        else:
            report.checked.append(f"{drive} ({free_gb:.0f}GB free)")


def _disk_health(report: Report) -> None:
    """SMART, via Windows' own summary rather than raw attribute parsing."""
    ok, out = run_powershell(
        "Get-PhysicalDisk | ForEach-Object { '{0}|{1}|{2}' -f "
        "$_.FriendlyName, $_.HealthStatus, $_.OperationalStatus }"
    )
    if not ok:
        report.failed.append("disk health")
        return
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        name, health, _op = parts
        if health.strip().lower() not in ("healthy", ""):
            report.add(Finding(
                area="storage", severity="problem", confidence="observed",
                summary=f"the drive {name.strip()} reports its health as {health.strip()}",
                detail="Back up anything you care about before doing anything else.",
            ))
        else:
            report.checked.append(f"{name.strip()} health")


# ------------------------------------------------------- updates & security
def _update_findings(report: Report) -> None:
    ok, out = run_powershell(
        "$h = Get-HotFix -ErrorAction SilentlyContinue | "
        "Sort-Object InstalledOn -Descending | Select-Object -First 1;"
        "if ($h -and $h.InstalledOn) { (New-TimeSpan -Start $h.InstalledOn).Days } else { 'UNKNOWN' }"
    )
    if not ok or "UNKNOWN" in out:
        report.failed.append("Windows update age")
        return
    match = re.search(r"(\d+)", out)
    if not match:
        report.failed.append("Windows update age")
        return
    days = int(match.group(1))
    if days > 90:
        report.add(Finding(
            area="updates", severity="problem", confidence="observed",
            summary=f"Windows hasn't been updated in {days} days",
            detail="Security patches ship monthly. Three months behind is a real exposure.",
            fix="open_windows_update",
        ))
    elif days > 45:
        report.add(Finding(
            area="updates", severity="warn", confidence="observed",
            summary=f"last Windows update was {days} days ago",
            fix="open_windows_update",
        ))
    else:
        report.checked.append(f"Windows updates ({days} days ago)")


def _security_findings(report: Report) -> None:
    ok, out = run_powershell(
        "$m = Get-MpComputerStatus -ErrorAction SilentlyContinue;"
        "if (-not $m) { 'NO_DEFENDER'; exit };"
        "'{0}|{1}|{2}' -f $m.RealTimeProtectionEnabled, $m.AntivirusSignatureAge, "
        "$m.QuickScanAge"
    )
    if not ok:
        report.failed.append("antivirus status")
        return
    if "NO_DEFENDER" in out:
        report.add(Finding(
            area="security", severity="warn", confidence="likely",
            summary="Windows Defender isn't reporting — you may be running a different antivirus",
        ))
        return
    parts = out.strip().split("|")
    if len(parts) != 3:
        report.failed.append("antivirus status")
        return
    realtime, sig_age, _scan_age = parts
    if realtime.strip().lower() != "true":
        report.add(Finding(
            area="security", severity="problem", confidence="observed",
            summary="real-time antivirus protection is switched off",
            fix="open_windows_security",
        ))
    else:
        report.checked.append("antivirus real-time protection")
    try:
        if int(sig_age) > 7:
            report.add(Finding(
                area="security", severity="warn", confidence="observed",
                summary=f"antivirus definitions are {int(sig_age)} days old",
                fix="open_windows_security",
            ))
    except ValueError:
        pass


# ------------------------------------------------------------------- memory
def _startup_findings(report: Report) -> None:
    """Startup programs are the commonest cause of "it's slow to boot"."""
    ok, out = run_powershell(
        "(Get-CimInstance Win32_StartupCommand | Measure-Object).Count"
    )
    if not ok:
        report.failed.append("startup programs")
        return
    match = re.search(r"(\d+)", out)
    if not match:
        report.failed.append("startup programs")
        return
    count = int(match.group(1))
    if count > 20:
        report.add(Finding(
            area="performance", severity="warn", confidence="likely",
            summary=f"{count} programs start automatically with Windows",
            detail="That is a lot; it is usually why a machine is slow for the "
                   "first few minutes after login.",
            fix="open_startup_settings",
        ))
    else:
        report.checked.append(f"startup programs ({count})")


# -------------------------------------------------------------------- tools
_AREAS = {
    "wifi": (_wifi_findings, _wifi_drop_history),
    "storage": (_storage_findings, _disk_health),
    "updates": (_update_findings,),
    "security": (_security_findings,),
    "performance": (_startup_findings,),
}


def diagnose(area: str = "") -> str:
    """
    Look for problems. Read-only — GREEN.

    `area` narrows it to one of wifi, storage, updates, security,
    performance. Empty checks everything, which takes longer; when he names
    a symptom, name the area.
    """
    if not IS_WINDOWS:
        return "System diagnosis is Windows-only."

    wanted = (area or "").strip().lower()
    if wanted and wanted not in _AREAS:
        return (
            f"I don't have a check for {area!r}. I can look at: "
            + ", ".join(sorted(_AREAS)) + "."
        )

    report = Report()
    for name, checks in _AREAS.items():
        if wanted and name != wanted:
            continue
        for check in checks:
            try:
                check(report)
            except Exception as exc:            # noqa: BLE001
                # One broken check must not take the whole diagnosis down —
                # and must not silently vanish either.
                report.failed.append(f"{name} ({type(exc).__name__})")
    return report.render()


def diagnose_wifi() -> str:
    """Why the wifi keeps dropping. Read-only — GREEN."""
    return diagnose("wifi")


REGISTRY: dict[str, Any] = {
    "diagnose": diagnose,
    "diagnose_wifi": diagnose_wifi,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
