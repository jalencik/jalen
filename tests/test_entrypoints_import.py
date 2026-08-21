"""
Every entry point must actually start.

A blanket rename of "jarvis" to "jalen" rewrote the IMPORT statements in
run.py alongside the prose, so `from jarvis import runtime` became
`from jalen import runtime`. Twelve hundred tests passed, because they
import jarvis.* directly and never go through run.py — and run.py is the
only way a person starts this.

That is the failure this file exists for: a green suite over an application
that cannot launch. It is cheap to prevent and embarrassing to ship.
"""
from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Everything a person is told to run in README/SETUP, plus the two the
# installer launches on his behalf.
ENTRY_POINTS = [
    "run.py",
    "scripts/check_env.py",
    "scripts/connect_google.py",
    "scripts/connect_telegram.py",
    "scripts/download_models.py",
    "scripts/selftest_voice.py",
    "scripts/hotkeys.py",
    "scripts/install_autostart.py",
    "scripts/train_wake_word.py",
]


@pytest.mark.parametrize("relative", ENTRY_POINTS)
def test_entry_point_parses(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    ast.parse(source)  # raises SyntaxError with the line number if broken


@pytest.mark.parametrize("relative", ENTRY_POINTS)
def test_every_import_target_exists(relative):
    """
    Walk the imports and confirm each first-party module is real.

    Static rather than executed: importing scripts/connect_telegram.py would
    try to sign in to Telegram. What matters is that the module PATHS are
    right, which is exactly what the rename broke.
    """
    sys.path.insert(0, str(ROOT))
    try:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                root_package = name.split(".")[0]
                if root_package not in ("jarvis", "jalen"):
                    continue     # third-party and stdlib are pip's problem
                assert importlib.util.find_spec(name) is not None, (
                    f"{relative} imports {name!r}, which does not exist"
                )
    finally:
        if str(ROOT) in sys.path:
            sys.path.remove(str(ROOT))


def test_run_py_actually_runs():
    """
    The one that would have caught it. --status touches the real import
    chain, prints, and exits, without opening a microphone or a window.
    """
    result = subprocess.run(
        [sys.executable, str(ROOT / "run.py"), "--status"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"run.py --status failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "Jalen" in result.stdout, result.stdout


def test_the_launcher_offers_the_documented_commands():
    """
    jalen.ps1 is what SETUP.md tells him to type. A command listed there and
    missing from the switch fails with "Unknown command" at the moment he
    needs it.
    """
    script = (ROOT / "jalen.ps1").read_text(encoding="utf-8")
    for command in ("start", "stop", "restart", "status", "text",
                    "telegram", "check", "hotkeys", "uninstall"):
        assert f'"{command}"' in script, f"jalen.ps1 lost the {command!r} command"
