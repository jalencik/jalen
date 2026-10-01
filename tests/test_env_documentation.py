"""
Every environment variable the code reads must be documented in .env.example.

The bug this prevents, found 20 September 2026: jarvis/tools/agents.py:297 has a
complete, working Hermes delegate -- reading HERMES_API_KEY or
OPENROUTER_API_KEY, with HERMES_MODEL and HERMES_BASE_URL both overridable --
and scripts/readiness.py:135 already reports it as BLOCKED with the right
remedy. But none of those four names appeared in .env.example, so the only way
to discover the feature existed was to read the source.

The owner asked Jalen about Hermes in a real session. Jalen asked which Hermes he
meant. His answer was silently discarded by the address gate (see
tests/test_brain_start_failure_is_spoken.py for the sibling class of silent
failure), and the feature stayed unreachable for another three weeks.

A key that is read by code and documented nowhere is a feature that does not
exist. This test is the structural guard, in the same spirit as
tests/test_adversarial.py pinning the injection-guard ordering by source index:
the property is about the repo's shape, and no behavioural test can see it.

Two escape hatches, both deliberate and both narrow. PLATFORM_VARS are set by
Windows, not by the user. INTERNAL_SWITCHES are development and test seams that
a user is never meant to set -- each one is listed with why. Adding a name to
either list is a decision someone has to write down here, which is the point.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent

# Set by the operating system. Not project configuration, never the user's job.
PLATFORM_VARS = frozenset({
    "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "USER", "USERNAME", "PATH",
    "PATHEXT", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "COMSPEC",
    "PROGRAMFILES", "PROGRAMDATA",
    # safety.py refuses \\<this computer>\C$\... as this computer by another name.
    "COMPUTERNAME",
})

# Development and test seams. A user is never told to set these, and documenting
# them in .env.example would invite exactly that.
INTERNAL_SWITCHES = frozenset({
    # tests/test_bridge.py and test_native_host_process.py point the bridge at a
    # temp file so a test cannot publish over data/bridge.json and steal the
    # extension out of his live Chrome. That happened; it is why the seam exists.
    "JALEN_BRIDGE_FILE",
    # Overrides which /my-voice skill file the drafting tools read, for tests
    # that must not depend on the real one being installed.
    "JARVIS_VOICE_SKILL",
})

_READS = re.compile(
    r"""os\.(?:getenv\(|environ\.get\(|environ\[)\s*["']([A-Z][A-Z0-9_]*)["']"""
)


def _vars_the_code_reads() -> dict[str, list[str]]:
    """{VAR_NAME: ["file:line", ...]} across the shipped source."""
    found: dict[str, list[str]] = {}
    for directory in ("jarvis", "scripts"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                for name in _READS.findall(line):
                    where = f"{path.relative_to(ROOT).as_posix()}:{lineno}"
                    found.setdefault(name, []).append(where)
    return found


def _vars_documented() -> set[str]:
    """Names on the left of an = in .env.example, commented lines included so a
    documented-but-commented example still counts as documented."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", text, re.MULTILINE))


def test_every_env_var_the_code_reads_is_documented():
    """The whole point. A credential read by code and documented nowhere is a
    feature nobody can find -- which is exactly what happened to Hermes."""
    reads = _vars_the_code_reads()
    documented = _vars_documented()

    undocumented = {
        name: sites
        for name, sites in reads.items()
        if name not in documented
        and name not in PLATFORM_VARS
        and name not in INTERNAL_SWITCHES
    }

    assert not undocumented, (
        "read by code, documented in neither .env.example nor an escape hatch:\n"
        + "\n".join(
            f"  {name}  <- {', '.join(sites[:3])}"
            for name, sites in sorted(undocumented.items())
        )
    )


def test_the_escape_hatches_are_not_used_to_hide_a_credential():
    """A lazy fix for the test above would be to dump a real key name into
    INTERNAL_SWITCHES. Anything that looks like a credential must be documented
    rather than excused."""
    credential_shaped = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|API)")

    smuggled = sorted(
        n for n in (PLATFORM_VARS | INTERNAL_SWITCHES) if credential_shaped.search(n)
    )

    assert not smuggled, (
        f"credential-shaped names hidden in an escape hatch: {smuggled}. "
        "Document them in .env.example instead."
    )


def test_the_scanner_actually_finds_things():
    """A regex that silently matches nothing would make the test above pass
    forever. Pin a variable that is definitely read."""
    reads = _vars_the_code_reads()

    assert "GROQ_API_KEY" in reads, "the scanner found no GROQ_API_KEY - it is broken"
    assert len(reads) >= 10, f"the scanner found only {len(reads)} vars - suspicious"
