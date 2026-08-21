"""
Phase 3: the IT technician.

He described the job in his own words — "wifi disconnects every 5 minutes,
or some antivirus problem or old windows, or storage filling out" — and
wanted something that hears a symptom and goes and finds the cause.

The property these tests protect is HONESTY, not coverage. A technician that
reports a fix it did not make, or a clean bill of health for checks that
never ran, is worse than no technician: he stops looking for the real cause
because he believes it is handled. Both of those bugs were in the first
version and both were caught by running it on this machine.
"""
from __future__ import annotations

import pytest

from jarvis.tools import repairs, technician
from jarvis.tools.technician import Finding, Report


# --------------------------------------------------------------- reporting
def test_problems_are_reported_before_the_clean_list():
    report = Report()
    report.checked.append("something fine")
    report.add(Finding(area="storage", severity="problem", summary="drive is full"))
    report.add(Finding(area="wifi", severity="info", summary="signal is fine"))
    rendered = report.render()
    assert rendered.index("drive is full") < rendered.index("signal is fine")


def test_a_hypothesis_is_marked_as_one():
    """
    "Your adapter is set to power down" is a fact. "That is why it drops" is
    a guess — a good one, and still a guess. Presenting the second as the
    first is how someone spends an evening on the wrong cause.
    """
    fact = Finding(area="wifi", severity="problem", confidence="observed",
                   summary="the signal is at 20 percent")
    guess = Finding(area="wifi", severity="problem", confidence="likely",
                    summary="power saving is why it drops")
    assert "probably" not in fact.spoken()
    assert "probably" in guess.spoken()


def test_checks_that_could_not_run_are_never_silently_dropped():
    """
    THE IMPORTANT ONE. "I looked at six things and two failed" is a
    different answer from "I looked at four things", and collapsing them
    turns a partial scan into a clean bill of health.
    """
    report = Report()
    report.checked.append("storage")
    report.failed.append("antivirus status")
    rendered = report.render()
    assert "COULD NOT CHECK" in rendered
    assert "antivirus status" in rendered
    assert "not a clean bill of health" in rendered


def test_an_empty_report_says_so_rather_than_nothing():
    assert "Nothing wrong" in Report().render()


def test_a_finding_can_name_what_it_does_not_know():
    finding = Finding(
        area="wifi", severity="problem", summary="power saving is on",
        unknown="whether that is actually causing the drops",
    )
    report = Report()
    report.add(finding)
    assert "Couldn't determine" in report.render()


# ------------------------------------------------------------- diagnostics
def test_an_unknown_area_lists_the_real_ones():
    reply = technician.diagnose("bluetooth")
    assert "wifi" in reply and "storage" in reply


def test_one_broken_check_does_not_kill_the_diagnosis(monkeypatch):
    """
    A diagnostic that dies on its third check tells him nothing about the
    other five — and the failure must still be reported, not swallowed.
    """
    def explode(report):
        raise RuntimeError("boom")

    monkeypatch.setitem(technician._AREAS, "wifi", (explode,))
    reply = technician.diagnose("wifi")
    assert "COULD NOT CHECK" in reply
    assert "RuntimeError" in reply


@pytest.mark.parametrize("area", ["wifi", "storage", "updates", "security", "performance"])
def test_every_advertised_area_exists(area):
    assert area in technician._AREAS


def test_the_diagnostic_module_cannot_write_anything():
    """
    The split between looking and fixing is enforced here, not by convention.
    If a repair ever migrates into technician.py, diagnosis stops being free
    to run — and a fix nobody reviewed ships with it.
    """
    import inspect

    source = inspect.getsource(technician)
    for forbidden in ("Set-NetAdapter", "Remove-Item", "Set-ItemProperty",
                      "Stop-Service", "Set-Service", "New-Item"):
        assert forbidden not in source, (
            f"technician.py contains {forbidden!r} — diagnosis must not change anything"
        )


# ----------------------------------------------------------------- repairs
def test_an_unsupported_setting_is_not_reported_as_fixed(monkeypatch):
    """
    THE FALSE SUCCESS THIS CAUGHT, on his own machine. His wireless adapter
    returns "Unsupported" for power management — it does not expose the
    setting at all. "Unsupported" != "Disabled" looked like work to do, and
    the reply announced "Windows can no longer switch it off" having changed
    precisely nothing.
    """
    monkeypatch.setattr(
        repairs, "run_powershell",
        lambda script, timeout=25.0: (True, "Wireless Adapter|Unsupported"),
    )
    reply = repairs.fix_wifi_power_saving(allow_power_off=False)
    assert "Nothing to change" in reply
    assert "doesn't support power management" in reply
    assert "can no longer" not in reply, "it claimed a change it did not make"


def test_a_change_is_verified_by_reading_it_back(monkeypatch):
    """
    A command exiting zero has not necessarily changed anything. The only way
    to earn a report he can trust is to look again afterwards.
    """
    calls = []

    def fake(script, timeout=25.0):
        calls.append(script)
        if "Get-NetAdapter -Physical" in script:
            return True, "Wi-Fi|Enabled"
        if "Set-NetAdapterPowerManagement" in script:
            return True, "OK"
        # The read-back: report that it did NOT stick.
        return True, "Enabled"

    monkeypatch.setattr(repairs, "run_powershell", fake)
    reply = repairs.fix_wifi_power_saving(allow_power_off=False)
    assert "didn't stick" in reply
    assert "administrator" in reply
    assert any("AllowComputerToTurnOffDevice" in c and "Set-" not in c for c in calls), (
        "the setting was never read back"
    )


def test_a_change_that_sticks_is_reported_with_its_undo(monkeypatch):
    def fake(script, timeout=25.0):
        if "Get-NetAdapter -Physical" in script:
            return True, "Wi-Fi|Enabled"
        if "Set-NetAdapterPowerManagement" in script:
            return True, "OK"
        return True, "Disabled"

    monkeypatch.setattr(repairs, "run_powershell", fake)
    reply = repairs.fix_wifi_power_saving(allow_power_off=False)
    assert "can no longer switch Wi-Fi off" in reply
    assert "let wifi sleep again" in reply, "the undo is not named"


def test_skipped_files_are_counted_not_rounded_away(monkeypatch):
    """
    Files in use are skipped by Windows. Reporting only what was deleted
    would overstate the saving, which is the same silent-omission failure as
    everything else in this project.
    """
    monkeypatch.setattr(repairs, "run_powershell",
                        lambda script, timeout=25.0: (True, "142|1500|29"))
    reply = repairs.clear_temp_files()
    assert "142 megabytes" in reply
    assert "29 were in use" in reply


def test_a_cleanup_that_reports_nothing_admits_it(monkeypatch):
    monkeypatch.setattr(repairs, "run_powershell",
                        lambda script, timeout=25.0: (True, "unexpected"))
    assert "can't tell you" in repairs.clear_temp_files()


# ------------------------------------------------------------- reachability
def test_tiers_match_what_each_tool_actually_does():
    """
    Reading is free, changing a setting announces itself, deleting asks.
    If diagnose ever became AMBER he would stop asking what is wrong; if
    clear_temp_files ever became GREEN a mis-heard word would delete files.
    """
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    expected = {
        "diagnose": Tier.GREEN,
        "diagnose_wifi": Tier.GREEN,
        "temp_file_report": Tier.GREEN,
        "open_windows_update": Tier.GREEN,
        "open_windows_security": Tier.GREEN,
        "open_startup_settings": Tier.GREEN,
        "open_storage_settings": Tier.GREEN,
        "fix_wifi_power_saving": Tier.AMBER,
        "clear_temp_files": Tier.RED,
    }
    for name, tier in expected.items():
        assert name in tools.REGISTRY, f"{name} is not dispatchable"
        assert name in TOOL_SPECS, f"{name} is invisible to the brain"
        assert engine.classify(name, {}).tier is tier, f"{name} has the wrong tier"


@pytest.mark.parametrize(
    "phrase, tool",
    [
        ("my wifi keeps dropping", "diagnose_wifi"),
        ("why is my wifi disconnecting", "diagnose_wifi"),
        ("check my wifi", "diagnose_wifi"),
        ("whats wrong with my computer", "diagnose"),
        ("run a diagnostic", "diagnose"),
        ("health check", "diagnose"),
        ("check storage", "diagnose"),
        ("fix my wifi", "fix_wifi_power_saving"),
        ("stop my wifi from sleeping", "fix_wifi_power_saving"),
        ("let wifi sleep again", "fix_wifi_power_saving"),
        ("temp files", "temp_file_report"),
        ("clear the temp files", "clear_temp_files"),
        ("windows update", "open_windows_update"),
        ("check antivirus", "open_windows_security"),
        ("startup apps", "open_startup_settings"),
    ],
)
def test_symptoms_route_without_an_llm(phrase, tool):
    """
    Symptom-first, because that is how he says it: he does not ask for a
    diagnostic, he says the wifi keeps cutting out. Asking what is wrong has
    to be free or he stops asking.
    """
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    hit = IntentRouter(CONFIG).route(phrase)
    assert hit is not None, f"{phrase!r} fell through to Claude"
    assert hit.tool == tool


def test_the_launcher_rules_are_not_shadowed():
    """
    "run a diagnostic" was being eaten by the generic "run X" launcher rule
    and became open_target(name="a diagnostic"). Fixing that must not break
    the launcher in the other direction.
    """
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    router = IntentRouter(CONFIG)
    assert router.route("run notepad").tool == "open_target"
    assert router.route("open chrome").tool == "open_target"
