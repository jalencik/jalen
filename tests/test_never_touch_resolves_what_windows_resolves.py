"""
The never-touch check compared spelled-out paths; Windows resolves more than
that.

cd14483 made the check see through ~, %VARIABLES%, ".." and the long-path
prefix. An independent review of that work then confirmed, against a real
file, that Windows still reaches a protected file by names the check never
expanded:

  - 8.3 short names (PASSWO~1.TXT, MOTHER~1\\..., ENV~1, VAULT~1.JSO)
  - legacy junctions ("C:\\Documents and Settings\\<user>" IS the user
    profile; "Application Data" and "All Users" likewise)
  - names with a trailing dot or space ("vault.json.") and an alternate data
    stream suffix ("server.pem::$DATA"), which Windows ignores but the
    filename patterns did not
  - this computer's own network names (\\\\localhost\\C$\\..., \\\\127.0.0.1\\...)
    and device paths (\\\\?\\GLOBALROOT\\..., \\??\\C:\\...)

Through the real read_file path with the turn tainted, the 8.3 name came back
with the file's contents. The vault and the Telegram session file are exactly
what the list exists for.

THE RULE NOW: a path that exists is resolved the way Windows resolves it
(os.path.realpath: junctions, links, short names) before it is compared, and
the filename patterns see the resolved names; trailing dots, spaces and stream
suffixes are removed from every component; and any form that names this
machine through the network or a device path is refused as a path that cannot
be checked.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

import pytest

from jarvis.config import CONFIG
from jarvis.safety import SafetyEngine, Tier

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows path rules")


@pytest.fixture
def engine():
    return SafetyEngine(CONFIG)


@pytest.fixture
def vault_tree(tmp_path, engine):
    """A protected folder with a protected file in it, and the engine told so."""
    protected = tmp_path / "Mother credentials"
    protected.mkdir()
    (protected / "bank details.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "plain.pem").write_text("k", encoding="utf-8")
    engine._never_paths = list(engine._never_paths) + [
        str(protected).replace("\\", "/").lower(),
        os.path.realpath(str(protected)).replace("\\", "/").lower(),
    ]
    return tmp_path, protected


def _short(path) -> str:
    buf = ctypes.create_unicode_buffer(600)
    ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 600)
    return buf.value


def _black(engine, raw, key="path"):
    return engine.classify("read_file", {key: raw}).tier is Tier.BLACK


def test_an_8_3_short_name_reaches_the_same_file(engine, vault_tree):
    _, protected = vault_tree
    short = _short(protected / "bank details.txt")
    if not short or short.lower() == str(protected / "bank details.txt").lower():
        pytest.skip("8.3 short names are not generated on this volume")
    assert _black(engine, short), short


def test_a_junction_into_a_protected_folder_is_the_protected_folder(engine, vault_tree):
    tmp, protected = vault_tree
    link = tmp / "innocent"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(protected)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("cannot create a junction here")
    assert _black(engine, str(link / "bank details.txt"))
    assert _black(engine, str(link / "a new file.txt")), "a file to be created inside it"


def test_the_legacy_profile_aliases_are_the_profile(engine):
    home = os.path.expanduser("~")
    user = os.path.basename(home)
    alias = os.path.join(os.path.dirname(os.path.dirname(home)), "Documents and Settings", user, ".ssh", "config")
    if os.path.realpath(alias).lower() == alias.lower():
        pytest.skip("the legacy alias does not exist on this machine")
    assert _black(engine, alias), alias


@pytest.mark.parametrize("name", [
    "vault.json.", "vault.json  ", "server.pem::$DATA", "jalen.session::$DATA",
    "google_token.json.", "id_rsa.", "db.kdbx::$DATA", "x.pem:Zone.Identifier",
])
def test_names_windows_ignores_the_ending_of_do_not_escape_the_patterns(engine, tmp_path, name):
    assert _black(engine, str(tmp_path) + "\\" + name), name


@pytest.mark.parametrize("raw", [
    r"\\localhost\C$\Users\someone\.ssh\config",
    r"\\127.0.0.1\c$\Windows\System32",
    r"\\?\UNC\localhost\C$\Windows\notepad.exe",
    r"\\?\GLOBALROOT\Device\HarddiskVolume3\Windows\notepad.exe",
    r"\??\C:\Windows\notepad.exe",
    r"\\.\C:\Windows\notepad.exe",
])
def test_this_computer_by_another_name_is_refused_as_unreadable(engine, raw):
    verdict = engine.classify("read_file", {"path": raw})
    assert verdict.tier is Tier.BLACK, raw
    assert "couldn't check" in verdict.reason, verdict.reason


def test_this_computers_own_name_is_refused_too(engine):
    name = os.environ.get("COMPUTERNAME", "")
    if not name:
        pytest.skip("no computer name")
    assert _black(engine, "\\\\" + name + "\\C$\\Windows\\win.ini")


@pytest.mark.parametrize("raw", [
    r"\\fileserver\team\notes.txt",
    r"\\nas\media\film.mkv",
])
def test_another_machines_share_is_not_this_machines_secrets(engine, raw):
    assert not _black(engine, raw), raw


def test_ordinary_files_are_still_ordinary(engine, tmp_path):
    ordinary = tmp_path / "report.pdf"
    ordinary.write_text("x", encoding="utf-8")
    assert not _black(engine, str(ordinary))
    assert not _black(engine, str(ordinary) + ".")
    assert not _black(engine, str(tmp_path / "not yet there.txt"))


def test_a_folder_named_like_a_pattern_is_not_a_reason_to_refuse_relative_paths(
        engine, tmp_path, monkeypatch):
    """Started from credentials-app, `notes\\todo.txt` must still work."""
    start = tmp_path / "credentials-app"
    (start / "notes").mkdir(parents=True)
    monkeypatch.chdir(start)
    assert not _black(engine, "notes\\todo.txt")
    assert _black(engine, "notes\\..\\.env")


def test_the_check_is_fast_enough_to_run_on_every_file_a_search_reads(engine, tmp_path):
    import time

    # The FASTEST of five batches: what the code costs, not what a busy
    # machine adds. Measured 0.5 ms alone; one batch read 3+ ms while six
    # agents ran the suite in parallel (2026-10-01), so a single batch made
    # this a test of the machine's load.
    target = str(tmp_path / "some" / "deep" / "folder" / "file.txt")
    batches = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(100):
            engine.protected_path(target)
        batches.append((time.perf_counter() - start) / 100 * 1000)
    assert min(batches) < 3.0, f"{min(batches):.2f} ms per path at best"
