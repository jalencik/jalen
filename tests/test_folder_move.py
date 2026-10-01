"""
Moving a whole folder to another drive.

WHAT WAS MISSING
----------------
On 2026-08-22 08:12 he said "...move my Cafe folder and Echo Pulse folder
and..." with his C: drive at 105 MB free and D: at 319 GB free. There was no
tool for it. What existed was move_file, which is GREEN (no question asked)
and hands a directory to shutil.move: across drives that is copytree and then
rmtree - no count, no comparison, no refusal of a Windows or Program Files
folder, no never-touch scan, and it runs inside the turn so he hears nothing
for as long as it takes.

Every test here uses tmp_path trees. Nothing touches a real folder, a real
drive, the network or a real Telegram. The tests pretend two volumes exist by
patching fm._same_volume, because tmp_path is on one.
"""
from __future__ import annotations

import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.tools import foldermove as fm  # noqa: E402

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="junctions are an NTFS thing")


# ----------------------------------------------------------------- helpers
def make_tree(root: Path, files: dict[str, bytes | str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    return root


def snapshot(root: Path) -> dict[str, bytes | None]:
    """relative path -> bytes (None for a directory), everything under root."""
    out: dict[str, bytes | None] = {}
    for dp, dn, fn in os.walk(root):
        for d in dn:
            out[os.path.relpath(os.path.join(dp, d), root).replace("\\", "/")] = None
        for f in fn:
            fp = os.path.join(dp, f)
            out[os.path.relpath(fp, root).replace("\\", "/")] = Path(fp).read_bytes()
    return out


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """
    Keep every test off the real machine.

    tmp_path lives under %LOCALAPPDATA%\\Temp, which the real refusal list
    (correctly) calls an app-data folder, so the system roots are emptied
    here and put back one at a time by the tests that are about them. The
    repo, the interpreter and the running-program check are pointed
    somewhere harmless for the same reason.
    """
    monkeypatch.setattr(fm, "MOVES_DIR", tmp_path / "_moves")
    monkeypatch.setattr(fm, "ROOT", tmp_path / "_jalen_repo")
    monkeypatch.setattr(fm, "_system_roots", lambda: [])
    monkeypatch.setattr(fm, "_own_interpreter_dirs", lambda: [])
    monkeypatch.setattr(fm, "_programs_running_from", lambda path: [])
    monkeypatch.setattr(fm, "_cloud_sync_roots", lambda: [])
    fm._forget_plans()
    yield
    fm._forget_plans()


@pytest.fixture
def two_volumes(monkeypatch):
    """Pretend source and destination are on different volumes."""
    monkeypatch.setattr(fm, "_same_volume", lambda a, b: False)


@pytest.fixture
def drive_d(tmp_path):
    d = tmp_path / "D_drive"
    d.mkdir()
    return d


@pytest.fixture
def downloads(tmp_path):
    return make_tree(tmp_path / "C_drive" / "Downloads", {
        "a.txt": "alpha",
        "b.bin": os.urandom(5000),
        "sub/c.txt": "gamma",
        "sub/deeper/d.txt": "delta",
        "sub/empty_dir_marker/.keep": "",
    })


# ===================================================================== plan
def test_the_dry_run_counts_files_and_bytes_and_says_what_it_frees(downloads, drive_d, two_volumes):
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal is None, plan.refusal
    assert plan.files == 5
    assert plan.bytes == 5 + 5000 + 5 + 5 + 0
    assert plan.dst == drive_d / "Downloads"
    said = fm.describe_plan(plan)
    assert "5 files" in said
    assert "frees" in said.lower()
    assert str(drive_d / "Downloads") in said
    assert "haven't moved anything" in said.lower()


def test_a_dry_run_changes_nothing_on_disk(downloads, drive_d, two_volumes):
    before = snapshot(downloads)
    fm.plan_folder_move(str(downloads), str(drive_d))
    assert snapshot(downloads) == before
    assert not (drive_d / "Downloads").exists()
    assert not (fm.MOVES_DIR).exists() or not list(fm.MOVES_DIR.glob("*.json"))


def test_same_drive_is_called_an_instant_rename_that_frees_nothing(downloads, drive_d):
    plan = fm.make_plan(str(downloads), str(drive_d))  # really the same volume
    assert plan.refusal is None
    assert plan.same_volume
    said = fm.describe_plan(plan).lower()
    assert "instant" in said
    assert "frees nothing" in said


@pytest.mark.parametrize("spoken", ["D", "d:", "D drive", "the D drive", "drive D", "disk d"])
def test_a_drive_can_be_named_the_way_people_say_it(spoken, monkeypatch):
    monkeypatch.setattr(fm.os.path, "isdir", lambda p: True)
    dest, why = fm._resolve_destination(spoken)
    assert why is None, why
    assert str(dest).upper().startswith("D:")


def test_a_destination_that_is_not_a_drive_or_a_full_path_is_asked_about():
    dest, why = fm._resolve_destination("somewhere nice")
    assert dest is None
    assert "drive" in why.lower() and "full path" in why.lower()


def test_a_missing_folder_is_a_sentence_not_a_traceback(tmp_path, drive_d):
    plan = fm.make_plan(str(tmp_path / "nope"), str(drive_d))
    assert plan.refusal and "no folder" in plan.refusal.lower()


def test_a_file_is_pointed_at_move_file(tmp_path, drive_d):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    plan = fm.make_plan(str(f), str(drive_d))
    assert plan.refusal and "file" in plan.refusal.lower() and "move_file" in plan.refusal.replace(" ", "_")


def test_not_enough_room_names_both_numbers(downloads, drive_d, two_volumes, monkeypatch):
    monkeypatch.setattr(fm, "_free_bytes", lambda p: 100)
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal and "room" in plan.refusal.lower()


def test_headroom_is_kept_free_on_the_destination(downloads, drive_d, two_volumes, monkeypatch):
    need = 5 + 5000 + 5 + 5
    monkeypatch.setattr(fm, "_free_bytes", lambda p: need + 10)  # fits, but leaves 10 bytes
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal, "filling the destination to the last byte is not a move, it is an outage"


def test_a_destination_that_is_not_a_fixed_drive_is_refused(downloads, drive_d, two_volumes, monkeypatch):
    monkeypatch.setattr(fm, "_is_fixed_drive", lambda p: False)
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal and "usb" in plan.refusal.lower() or "network" in plan.refusal.lower()


def test_a_destination_inside_the_source_is_refused(downloads):
    plan = fm.make_plan(str(downloads), str(downloads / "sub"))
    assert plan.refusal and "inside" in plan.refusal.lower()


def test_a_destination_that_already_has_the_folder_is_not_merged(downloads, drive_d, two_volumes):
    """
    Not merged, and (since the real D: turned out to have a D:\\Downloads from
    2024 in it) not a dead end either: the move goes to a free name beside it.
    The one he already has is not touched. See the sibling tests further down.
    """
    make_tree(drive_d / "Downloads", {"keep.txt": "mine"})
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal is None, plan.refusal
    assert plan.dst != drive_d / "Downloads"
    assert (drive_d / "Downloads" / "keep.txt").read_text() == "mine"
    assert not (drive_d / "Downloads" / "a.txt").exists()


def test_an_empty_destination_folder_is_fine(downloads, drive_d, two_volumes):
    (drive_d / "Downloads").mkdir()
    assert fm.make_plan(str(downloads), str(drive_d)).refusal is None


def test_a_destination_named_like_the_folder_is_the_folder_itself(downloads, drive_d, two_volumes):
    plan = fm.make_plan(str(downloads), str(drive_d / "Downloads"))
    assert plan.dst == drive_d / "Downloads"


def test_a_venv_gets_a_warning_not_a_refusal(tmp_path, drive_d, two_volumes):
    proj = make_tree(tmp_path / "proj", {"app.py": "x", ".venv/pyvenv.cfg": "home = C:/Python"})
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal is None
    assert any("virtual environment" in w for w in plan.warnings)


def test_a_scan_that_runs_out_of_time_says_these_are_minimums(downloads, drive_d, two_volumes):
    plan = fm.make_plan(str(downloads), str(drive_d), budget_s=0.0)
    assert plan.capped
    assert "at least" in fm.describe_plan(plan).lower()


# ================================================================== refusals
def test_windows_and_program_files_are_refused(tmp_path, drive_d, monkeypatch):
    win = make_tree(tmp_path / "Windows", {"System32/x.dll": "x"})
    pf = make_tree(tmp_path / "Program Files", {"App/app.exe": "x"})
    monkeypatch.setattr(fm, "_system_roots", lambda: [win, pf])
    for victim in (win, pf, win / "System32", pf / "App"):
        plan = fm.make_plan(str(victim), str(drive_d))
        assert plan.refusal, victim
        assert "windows" in plan.refusal.lower() or "program" in plan.refusal.lower()
    assert (win / "System32" / "x.dll").exists()


def test_a_folder_that_contains_a_system_folder_is_refused_too(tmp_path, drive_d, monkeypatch):
    win = make_tree(tmp_path / "all" / "Windows", {"x.dll": "x"})
    monkeypatch.setattr(fm, "_system_roots", lambda: [win])
    plan = fm.make_plan(str(tmp_path / "all"), str(drive_d))
    assert plan.refusal and "contains" in plan.refusal.lower()


def test_installed_apps_appdata_is_refused(tmp_path, drive_d, monkeypatch):
    appdata = make_tree(tmp_path / "AppData" / "Local" / "Programs" / "Cursor", {"cursor.exe": "x"})
    monkeypatch.setattr(fm, "_system_roots", lambda: [tmp_path / "AppData"])
    plan = fm.make_plan(str(appdata), str(drive_d))
    assert plan.refusal and "appdata" in plan.refusal.lower().replace(" ", "")


def test_a_drive_root_and_the_profile_root_are_refused(drive_d):
    anchor = Path(os.path.splitdrive(str(drive_d))[0] + "\\")
    assert fm.make_plan(str(anchor), str(drive_d)).refusal
    assert fm.make_plan(str(Path.home()), str(drive_d)).refusal


def test_the_real_windows_folder_is_refused_with_the_real_roots(drive_d, monkeypatch):
    monkeypatch.undo()  # the real _system_roots, for this one test
    root = os.environ.get("SystemRoot")
    if not root:
        pytest.skip("no SystemRoot here")
    plan = fm.make_plan(root, str(drive_d))
    # C:/Windows is also on the never-touch list, which is checked first, so
    # either sentence is the right refusal. What matters is that there is one.
    assert plan.refusal and ("windows" in plan.refusal.lower() or "protected" in plan.refusal.lower())


def test_a_place_that_cannot_be_read_means_it_cannot_be_called_safe(downloads, drive_d, two_volumes, monkeypatch):
    real = os.scandir

    def no_access(path="."):
        if os.fspath(path).replace("\\", "/").endswith("/sub/deeper"):
            raise PermissionError(5, "Access is denied")
        return real(path)

    monkeypatch.setattr(os, "scandir", no_access)
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal and "couldn't read" in plan.refusal.lower()


def test_the_never_touch_list_is_refused_before_the_disk_is_even_looked_at(drive_d):
    plan = fm.make_plan(str(Path.home() / ".ssh"), str(drive_d))
    assert plan.refusal
    assert "protected" in plan.refusal.lower() or "never" in plan.refusal.lower()
    assert "no folder" not in plan.refusal.lower(), "a guarded path must not even confirm it exists"


def test_a_destination_on_the_never_touch_list_is_refused(downloads):
    plan = fm.make_plan(str(downloads), str(Path.home() / ".ssh"))
    assert plan.refusal


def test_the_protected_list_is_readable_from_the_safety_engine():
    """If safety.py renames what foldermove reads, the guard must fail loudly, not go quiet."""
    paths, patterns = fm._never_lists()
    assert paths and patterns
    assert any(p.endswith(".ssh") for p in paths)
    assert "*.pem" in patterns or ".env" in patterns


def test_an_unreadable_protected_list_refuses_everything(downloads, drive_d, monkeypatch):
    monkeypatch.setattr(fm, "_never_lists", lambda engine=None: ([], []))
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal and "protected" in plan.refusal.lower()


def test_a_folder_that_will_be_refused_is_refused_without_counting_the_rest(tmp_path, drive_d, two_volumes, monkeypatch):
    """
    Found on the real machine: his "SAT TOP web application" project (a .env
    and a node_modules) took 8.8 seconds of silence to say "I never move
    those". The answer is certain at the first protected file that can NOT be
    carried along (a key, a certificate, a vault - his decision let .env and
    token files through, see test_folder_move_review.py), so the walk stops
    there instead of counting every file in node_modules first.
    """
    proj = make_tree(tmp_path / "proj", {"server.pem": "X=1"})
    for i in range(30):
        make_tree(proj / f"sub{i}", {"a.txt": "a"})
    listed: list[str] = []
    real_scandir = os.scandir

    def counting(path):
        listed.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr(fm.os, "scandir", counting)
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "protected" in plan.refusal.lower(), plan.refusal
    assert "server.pem" in plan.refusal
    assert len(listed) <= 3, f"kept walking after the answer was known: {len(listed)} folders listed"


def test_a_link_inside_is_the_end_of_the_walk_too(tmp_path, drive_d, two_volumes, monkeypatch):
    proj = make_tree(tmp_path / "proj", {"a.txt": "a"})
    target = make_tree(tmp_path / "elsewhere", {"x.txt": "x"})
    try:
        os.symlink(target, proj / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot make a symlink")
    for i in range(30):
        make_tree(proj / f"sub{i}", {"a.txt": "a"})
    listed: list[str] = []
    real_scandir = os.scandir
    monkeypatch.setattr(fm.os, "scandir", lambda p: (listed.append(str(p)), real_scandir(p))[1])
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "link" in plan.refusal.lower(), plan.refusal
    assert len(listed) <= 3, listed


def test_a_folder_holding_a_protected_file_is_refused_and_the_file_is_not_hashed(tmp_path, drive_d, two_volumes, monkeypatch):
    proj = make_tree(tmp_path / "echo", {"app.py": "x", ".env": "TOKEN=1", "keys/server.pem": "k"})
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal
    assert "protected" in plan.refusal.lower()
    # The walk stops at the first protected file (whichever it meets first), and says which.
    assert ".env" in plan.refusal or "server.pem" in plan.refusal
    assert (proj / ".env").read_text() == "TOKEN=1"


def test_protected_looking_names_inside_dependency_folders_do_not_block_a_project(tmp_path, drive_d, two_volumes):
    """
    61 of 29,698 files in a real site-packages (0.2%) match the never-touch
    name patterns - Secret.py, cacert.pem, encrypted_credentials.py. Refusing
    every project that has a venv or node_modules would make "move my
    projects folder" impossible, and these are third-party code, not secrets.
    """
    proj = make_tree(tmp_path / "echo", {
        "app.py": "x",
        "node_modules/lib/secrets.js": "x",
        ".venv/Lib/site-packages/certifi/cacert.pem": "x",
    })
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal is None, plan.refusal


def test_a_protected_folder_hidden_inside_a_dependency_folder_still_blocks(tmp_path, drive_d, two_volumes, monkeypatch):
    proj = make_tree(tmp_path / "echo", {"node_modules/x/secret_dir/readme.txt": "x"})
    guarded = (str(tmp_path / "echo" / "node_modules" / "x" / "secret_dir").replace("\\", "/").lower())
    real = fm._never_lists
    monkeypatch.setattr(fm, "_never_lists", lambda engine=None: (real()[0] + [guarded], real()[1]))
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "protected" in plan.refusal.lower()


def test_jalens_own_folder_and_its_parent_are_refused(tmp_path, drive_d, monkeypatch):
    repo = make_tree(tmp_path / "work" / "jarvis", {"run.py": "x", "jarvis/__init__.py": ""})
    monkeypatch.setattr(fm, "ROOT", repo)
    for victim in (repo, repo / "jarvis", repo.parent):
        plan = fm.make_plan(str(victim), str(drive_d))
        assert plan.refusal, victim
        assert "jalen" in plan.refusal.lower()


def test_the_python_jalen_runs_on_is_refused(tmp_path, drive_d, monkeypatch):
    py = make_tree(tmp_path / "Python312", {"python.exe": "x"})
    monkeypatch.setattr(fm, "_own_interpreter_dirs", lambda: [py])
    assert fm.make_plan(str(py), str(drive_d)).refusal


def test_a_folder_a_running_program_lives_in_is_refused_by_name(downloads, drive_d, monkeypatch):
    monkeypatch.setattr(fm, "_programs_running_from", lambda path: ["obs64.exe"])
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal and "obs64.exe" in plan.refusal


def test_a_folder_inside_onedrive_is_refused_because_it_would_delete_it_from_the_cloud(tmp_path, drive_d, monkeypatch):
    cloud = tmp_path / "OneDrive"
    proj = make_tree(cloud / "Docs", {"a.txt": "x"})
    monkeypatch.setattr(fm, "_cloud_sync_roots", lambda: [cloud])
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "onedrive" in plan.refusal.lower()


@WINDOWS_ONLY
def test_a_folder_that_contains_a_junction_is_refused(tmp_path, drive_d, two_volumes):
    target = make_tree(tmp_path / "elsewhere", {"big.bin": "x"})
    proj = make_tree(tmp_path / "proj", {"a.txt": "x"})
    import _winapi
    _winapi.CreateJunction(str(target), str(proj / "link"))
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "link" in plan.refusal.lower()
    assert (target / "big.bin").exists()


@WINDOWS_ONLY
def test_a_folder_that_is_already_a_junction_is_reported_as_moved_already(tmp_path, drive_d):
    real = make_tree(drive_d / "Downloads", {"a.txt": "x"})
    old = tmp_path / "C_drive" / "Downloads"
    old.parent.mkdir(parents=True)
    import _winapi
    _winapi.CreateJunction(str(real), str(old))
    plan = fm.make_plan(str(old), str(drive_d))
    assert plan.refusal and "already" in plan.refusal.lower()


@WINDOWS_ONLY
def test_a_destination_that_is_really_a_system_folder_behind_a_link_is_refused(tmp_path, downloads, drive_d, two_volumes, monkeypatch):
    """The check on the spelling of the path is not the check on where it goes."""
    import _winapi

    win = make_tree(tmp_path / "Windows", {"x.dll": "x"})
    monkeypatch.setattr(fm, "_system_roots", lambda: [win])
    _winapi.CreateJunction(str(win), str(drive_d / "innocent"))
    plan = fm.make_plan(str(downloads), str(drive_d / "innocent"))
    assert plan.refusal and "windows" in plan.refusal.lower()
    assert (win / "x.dll").exists() and not (win / "Downloads").exists()


@WINDOWS_ONLY
def test_a_destination_that_is_really_inside_the_source_behind_a_link_is_refused(downloads, drive_d, two_volumes):
    import _winapi

    _winapi.CreateJunction(str(downloads / "sub"), str(drive_d / "back"))
    plan = fm.make_plan(str(downloads), str(drive_d / "back"))
    assert plan.refusal and "inside" in plan.refusal.lower()


def test_the_same_folder_is_not_moved_to_two_places_at_once(downloads, drive_d, tmp_path, two_volumes):
    other = tmp_path / "E_drive"
    other.mkdir()
    first = fm.make_plan(str(downloads), str(drive_d))
    state = fm._new_state(first)
    me = fm._self_identity()
    fm._update_state(state, state="copying", pid=me[0], pid_started=me[1], updated_at=time.time())
    second = fm.make_plan(str(downloads), str(other))
    assert second.refusal and "already being moved" in second.refusal.lower()


def test_a_placeholder_for_a_cloud_file_is_refused(downloads, drive_d, two_volumes, monkeypatch):
    """Reading an online-only OneDrive file downloads it; a 4 GB 'move' would pull 4 GB first."""
    monkeypatch.setattr(fm, "_attrs_of", lambda st: 0x00400000)  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal and ("online" in plan.refusal.lower() or "cloud" in plan.refusal.lower())


# ============================================================== copy + verify
def test_copy_tree_copies_everything_including_empty_folders_and_times(tmp_path):
    src = make_tree(tmp_path / "s", {"a.txt": "a", "x/y/z.bin": os.urandom(3000)})
    (src / "x" / "empty").mkdir()
    os.utime(src / "a.txt", ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    result = fm.copy_tree(src, tmp_path / "d")
    assert not result.errors
    assert snapshot(tmp_path / "d") == snapshot(src)
    assert (tmp_path / "d" / "a.txt").stat().st_mtime_ns == 1_700_000_000_000_000_000


def test_a_read_only_file_arrives_read_only_and_can_still_be_replaced(tmp_path):
    src = make_tree(tmp_path / "s", {"ro.txt": "v1"})
    os.chmod(src / "ro.txt", stat.S_IREAD)
    fm.copy_tree(src, tmp_path / "d")
    assert not os.access(tmp_path / "d" / "ro.txt", os.W_OK)
    os.chmod(src / "ro.txt", stat.S_IWRITE)
    (src / "ro.txt").write_text("v2-longer")
    os.chmod(src / "ro.txt", stat.S_IREAD)
    fm.copy_tree(src, tmp_path / "d")
    assert (tmp_path / "d" / "ro.txt").read_text() == "v2-longer"


@WINDOWS_ONLY
def test_paths_past_260_characters_are_copied_and_checked(tmp_path):
    deep = tmp_path / "s"
    for i in range(12):
        deep = deep / ("segment_number_" + str(i) * 4 + "_x")
    assert len(str(deep / "file.txt")) > 270
    os.makedirs(fm._lp(deep), exist_ok=True)
    Path(fm._lp(deep / "file.txt")).write_text("deep")
    result = fm.copy_tree(tmp_path / "s", tmp_path / "d")
    assert not result.errors, result.errors
    verdict = fm.verify_trees(tmp_path / "s", tmp_path / "d")
    assert not verdict.problems
    assert verdict.files == 1
    import shutil
    shutil.rmtree(fm._lp(tmp_path / "s"), ignore_errors=True)
    shutil.rmtree(fm._lp(tmp_path / "d"), ignore_errors=True)


def test_an_interrupted_copy_resumes_and_does_not_recopy_what_is_done(tmp_path, monkeypatch):
    src = make_tree(tmp_path / "s", {f"f{i}.txt": f"body {i}" for i in range(6)})
    real = fm._copy_one
    calls: list[str] = []

    class Crash(BaseException):
        pass

    def flaky(sp, dp):
        calls.append(os.path.basename(sp))
        if len(calls) == 4:
            # the process dies mid-file: a half-written part file is left behind
            Path(dp + fm.PART_SUFFIX).write_bytes(b"bod")
            raise Crash()
        real(sp, dp)

    monkeypatch.setattr(fm, "_copy_one", flaky)
    with pytest.raises(Crash):
        fm.copy_tree(src, tmp_path / "d")
    done_before = sorted(p.name for p in (tmp_path / "d").iterdir() if not p.name.endswith(fm.PART_SUFFIX))
    assert 1 <= len(done_before) <= 3

    monkeypatch.setattr(fm, "_copy_one", lambda sp, dp: (calls.append("again:" + os.path.basename(sp)), real(sp, dp)))
    calls.clear()
    result = fm.copy_tree(src, tmp_path / "d")
    assert not result.errors
    assert result.skipped == len(done_before)
    assert result.copied == 6 - len(done_before)
    assert len(calls) == 6 - len(done_before), "files that were already complete were copied again"
    verdict = fm.verify_trees(src, tmp_path / "d")
    assert [p for p in verdict.problems if p[0] != "extra"] == []


def test_a_half_written_file_with_the_right_name_is_not_trusted(tmp_path):
    src = make_tree(tmp_path / "s", {"big.bin": b"0123456789" * 100})
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "big.bin").write_bytes(b"01234")  # a truncated earlier attempt
    result = fm.copy_tree(src, tmp_path / "d")
    assert result.copied == 1 and result.skipped == 0
    assert (tmp_path / "d" / "big.bin").read_bytes() == b"0123456789" * 100


def test_a_file_that_changes_while_it_is_copied_is_copied_again(tmp_path, monkeypatch):
    src = make_tree(tmp_path / "s", {"log.txt": "line one"})
    real = fm._copy_one
    seen = {"n": 0}

    def writer_gets_in(sp, dp):
        real(sp, dp)
        seen["n"] += 1
        if seen["n"] == 1:
            Path(sp).write_text("line one and line two")  # another program appended

    monkeypatch.setattr(fm, "_copy_one", writer_gets_in)
    result = fm.copy_tree(src, tmp_path / "d")
    assert not result.errors
    assert (tmp_path / "d" / "log.txt").read_text() == "line one and line two"
    assert seen["n"] == 2


def test_a_file_that_never_stops_changing_is_an_error_not_a_silent_success(tmp_path, monkeypatch):
    src = make_tree(tmp_path / "s", {"log.txt": "x"})
    real = fm._copy_one
    counter = {"n": 0}

    def forever(sp, dp):
        real(sp, dp)
        counter["n"] += 1
        Path(sp).write_text("x" * (counter["n"] + 1))

    monkeypatch.setattr(fm, "_copy_one", forever)
    monkeypatch.setattr(fm, "RETRY_PAUSE_S", 0)
    result = fm.copy_tree(src, tmp_path / "d")
    assert result.errors and "log.txt" in result.errors[0]


def test_a_locked_file_is_reported_by_name_and_the_rest_still_copy(tmp_path, monkeypatch):
    src = make_tree(tmp_path / "s", {"locked.db": "x", "fine.txt": "y"})
    real = fm._copy_one

    def sharing_violation(sp, dp):
        if sp.endswith("locked.db"):
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real(sp, dp)

    monkeypatch.setattr(fm, "_copy_one", sharing_violation)
    monkeypatch.setattr(fm, "RETRY_PAUSE_S", 0)
    result = fm.copy_tree(src, tmp_path / "d")
    assert len(result.errors) == 1 and "locked.db" in result.errors[0]
    assert (tmp_path / "d" / "fine.txt").read_text() == "y"


def test_verify_passes_on_a_faithful_copy_and_says_how_much_was_hashed(tmp_path):
    src = make_tree(tmp_path / "s", {"a.txt": "a", "b/c.bin": os.urandom(2000)})
    fm.copy_tree(src, tmp_path / "d")
    v = fm.verify_trees(src, tmp_path / "d", seed="t")
    assert not v.problems and v.problem_count == 0
    assert v.files == 2 and v.hashed_files == 2 and v.hashed_bytes == 2001


def test_verify_finds_a_missing_an_extra_and_a_wrong_sized_file(tmp_path):
    src = make_tree(tmp_path / "s", {"a.txt": "aaaa", "b.txt": "bbbb", "c.txt": "cccc"})
    fm.copy_tree(src, tmp_path / "d")
    (tmp_path / "d" / "a.txt").unlink()
    (tmp_path / "d" / "stray.txt").write_text("?")
    (tmp_path / "d" / "b.txt").write_text("b")
    v = fm.verify_trees(src, tmp_path / "d")
    kinds = {kind for kind, _ in v.problems}
    assert kinds == {"missing", "extra", "differs"}, v.problems


def test_verify_catches_a_flipped_byte_that_size_and_time_cannot_see(tmp_path):
    src = make_tree(tmp_path / "s", {"photo.jpg": bytes(range(256)) * 20})
    fm.copy_tree(src, tmp_path / "d")
    target = tmp_path / "d" / "photo.jpg"
    stamp = target.stat().st_mtime_ns
    blob = bytearray(target.read_bytes())
    blob[1000] ^= 0xFF
    os.chmod(target, stat.S_IWRITE)
    target.write_bytes(bytes(blob))
    os.utime(target, ns=(stamp, stamp))
    v = fm.verify_trees(src, tmp_path / "d", seed="t")
    assert [k for k, _ in v.problems] == ["hash"], v.problems


def test_the_hash_sample_is_bounded_by_a_byte_budget(tmp_path, monkeypatch):
    src = make_tree(tmp_path / "s", {f"f{i}.bin": b"x" * 400 for i in range(10)})
    fm.copy_tree(src, tmp_path / "d")
    monkeypatch.setattr(fm, "SAMPLE_BYTE_BUDGET", 1000)
    v = fm.verify_trees(src, tmp_path / "d", seed="t")
    assert 1 <= v.hashed_files <= 3
    assert v.hashed_bytes <= 1200
    assert not v.problems


def test_files_that_look_protected_are_never_read_for_hashing(tmp_path, monkeypatch):
    src = make_tree(tmp_path / "s", {"node_modules/x/Secret.py": "s", "ok.txt": "o"})
    fm.copy_tree(src, tmp_path / "d")
    seen: list[str] = []
    real = fm._hash_file
    monkeypatch.setattr(fm, "_hash_file", lambda p: (seen.append(os.path.basename(p)), real(p))[1])
    fm.verify_trees(src, tmp_path / "d", seed="t")
    assert "Secret.py" not in seen
    assert "ok.txt" in seen


# ================================================================== run_move
def start(downloads, drive_d, leave_link=True):
    """Write the record a launched job would find, and return its path."""
    plan = fm.make_plan(str(downloads), str(drive_d), leave_link=leave_link)
    assert plan.refusal is None, plan.refusal
    return fm._new_state(plan)


@WINDOWS_ONLY
def test_a_move_copies_checks_removes_the_original_and_leaves_a_link(downloads, drive_d, two_volumes, capsys):
    before = snapshot(downloads)
    state = start(downloads, drive_d)
    assert fm.run_move(state) == 0
    final = drive_d / "Downloads"
    assert snapshot(final) == before
    assert os.path.isjunction(downloads), "the old path should still lead somewhere"
    assert snapshot(downloads) == before, "reading through the link gives the same files"
    assert not list(downloads.parent.glob("*" + fm.TRASH_MARK + "*")), "the renamed-aside original must be gone"
    record = fm._read_state(state)
    assert record["state"] == "done"
    out = capsys.readouterr().out
    assert "RESULT:" in out and "freed" in out.lower()


def test_without_a_link_the_old_path_is_simply_gone(downloads, drive_d, two_volumes):
    before = snapshot(downloads)
    state = start(downloads, drive_d, leave_link=False)
    assert fm.run_move(state) == 0
    assert snapshot(drive_d / "Downloads") == before
    assert not downloads.exists()


def test_the_job_checks_again_and_refuses_what_appeared_after_the_dry_run(downloads, drive_d, two_volumes):
    """
    The dry run may have been capped, or minutes old. The job scans the whole
    folder with no budget before it copies a byte, and refuses then.
    """
    before = snapshot(downloads)
    state = start(downloads, drive_d)
    (downloads / ".env").write_text("TOKEN=1")  # arrived after he heard the plan
    assert fm.run_move(state) != 0
    assert not (drive_d / "Downloads").exists(), "nothing should have been copied"
    assert (downloads / ".env").exists() and {k for k in snapshot(downloads)} == set(before) | {".env"}
    said = fm._read_state(state)["result"]
    assert "protected" in said.lower() and "before copying anything" in said.lower()


def test_a_failed_verification_removes_nothing(downloads, drive_d, two_volumes, monkeypatch):
    """THE property. If the copy cannot be proved, the original is not touched."""
    before = snapshot(downloads)
    real = fm._copy_one

    def corrupting(sp, dp):
        real(sp, dp)
        if sp.endswith("b.bin"):
            blob = bytearray(Path(dp).read_bytes())
            blob[10] ^= 0xFF
            stamp = os.stat(dp).st_mtime_ns
            os.chmod(dp, stat.S_IWRITE)
            Path(dp).write_bytes(bytes(blob))
            os.utime(dp, ns=(stamp, stamp))

    monkeypatch.setattr(fm, "_copy_one", corrupting)
    state = start(downloads, drive_d)
    code = fm.run_move(state)
    assert code != 0
    assert snapshot(downloads) == before, "the original changed even though the copy was bad"
    assert not list(downloads.parent.glob("*" + fm.TRASH_MARK + "*"))
    record = fm._read_state(state)
    assert record["state"] == "failed"
    assert "untouched" in record["result"].lower() or "nothing was removed" in record["result"].lower()


def test_a_repairable_mismatch_is_repaired_and_then_the_move_completes(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)
    real = fm._copy_one
    broke = {"done": False}

    def corrupt_once(sp, dp):
        real(sp, dp)
        if sp.endswith("b.bin") and not broke["done"]:
            broke["done"] = True
            blob = bytearray(Path(dp).read_bytes())
            blob[10] ^= 0xFF
            stamp = os.stat(dp).st_mtime_ns
            os.chmod(dp, stat.S_IWRITE)
            Path(dp).write_bytes(bytes(blob))
            os.utime(dp, ns=(stamp, stamp))

    monkeypatch.setattr(fm, "_copy_one", corrupt_once)
    state = start(downloads, drive_d, leave_link=False)
    assert fm.run_move(state) == 0
    assert snapshot(drive_d / "Downloads") == before


def test_files_that_could_not_be_copied_stop_the_move_before_anything_is_removed(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)
    real = fm._copy_one

    def locked(sp, dp):
        if sp.endswith("a.txt"):
            raise PermissionError(13, "in use")
        real(sp, dp)

    monkeypatch.setattr(fm, "_copy_one", locked)
    monkeypatch.setattr(fm, "RETRY_PAUSE_S", 0)
    state = start(downloads, drive_d)
    assert fm.run_move(state) != 0
    assert snapshot(downloads) == before
    assert "a.txt" in fm._read_state(state)["result"]


def test_a_source_that_something_is_using_is_left_alone_and_the_move_can_be_finished_later(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)
    real_rename = os.rename

    def in_use(a, b, *args, **kwargs):
        if os.path.normcase(str(a)).endswith(os.path.normcase(str(downloads))):
            raise PermissionError(5, "Access is denied")
        return real_rename(a, b, *args, **kwargs)

    monkeypatch.setattr(os, "rename", in_use)
    state = start(downloads, drive_d, leave_link=False)
    assert fm.run_move(state) != 0
    assert snapshot(downloads) == before
    assert fm._read_state(state)["state"] == "blocked"
    assert "using" in fm._read_state(state)["result"].lower()

    monkeypatch.setattr(os, "rename", real_rename)
    copies: list[str] = []
    real_copy = fm._copy_one
    monkeypatch.setattr(fm, "_copy_one", lambda sp, dp: (copies.append(sp), real_copy(sp, dp)))
    assert fm.run_move(state) == 0
    assert copies == [], "finishing a blocked move must not copy anything twice"
    assert not downloads.exists()
    assert snapshot(drive_d / "Downloads") == before


def test_a_file_that_appears_in_the_source_just_before_it_is_renamed_is_not_lost(downloads, drive_d, two_volumes, monkeypatch):
    real_rename = os.rename
    fired = {"n": 0}

    def a_download_lands(a, b, *args, **kwargs):
        if os.path.normcase(str(a)).endswith(os.path.normcase(str(downloads))) and not fired["n"]:
            fired["n"] += 1
            (downloads / "late.txt").write_text("arrived after the check")
        return real_rename(a, b, *args, **kwargs)

    monkeypatch.setattr(os, "rename", a_download_lands)
    state = start(downloads, drive_d, leave_link=False)
    assert fm.run_move(state) == 0
    assert (drive_d / "Downloads" / "late.txt").read_text() == "arrived after the check"


def test_a_crash_after_the_rename_is_finished_by_the_next_run_without_copying(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)

    class Crash(BaseException):
        pass

    def die(path):
        raise Crash()

    monkeypatch.setattr(fm, "_delete_tree", die)
    state = start(downloads, drive_d, leave_link=False)
    with pytest.raises(Crash):
        fm.run_move(state)
    trash = list(downloads.parent.glob("*" + fm.TRASH_MARK + "*"))
    assert len(trash) == 1 and not downloads.exists()

    monkeypatch.undo()
    monkeypatch.setattr(fm, "MOVES_DIR", state.parent)
    monkeypatch.setattr(fm, "ROOT", state.parent / "_jalen_repo")
    monkeypatch.setattr(fm, "_system_roots", lambda: [])
    monkeypatch.setattr(fm, "_own_interpreter_dirs", lambda: [])
    monkeypatch.setattr(fm, "_programs_running_from", lambda p: [])
    monkeypatch.setattr(fm, "_cloud_sync_roots", lambda: [])
    monkeypatch.setattr(fm, "_same_volume", lambda a, b: False)
    copies: list[str] = []
    real_copy = fm._copy_one
    monkeypatch.setattr(fm, "_copy_one", lambda sp, dp: (copies.append(sp), real_copy(sp, dp)))
    assert fm.run_move(state) == 0
    assert copies == []
    assert not list(downloads.parent.glob("*" + fm.TRASH_MARK + "*"))
    assert snapshot(drive_d / "Downloads") == before


@WINDOWS_ONLY
def test_a_crash_between_the_link_and_the_delete_is_finished_too(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)

    class Crash(BaseException):
        pass

    monkeypatch.setattr(fm, "_delete_tree", lambda p: (_ for _ in ()).throw(Crash()))
    state = start(downloads, drive_d)
    with pytest.raises(Crash):
        fm.run_move(state)
    assert os.path.isjunction(downloads)
    monkeypatch.undo()
    monkeypatch.setattr(fm, "MOVES_DIR", state.parent)
    monkeypatch.setattr(fm, "ROOT", state.parent / "_jalen_repo")
    for name, value in (("_system_roots", lambda: []), ("_own_interpreter_dirs", lambda: []),
                        ("_programs_running_from", lambda p: []), ("_cloud_sync_roots", lambda: []),
                        ("_same_volume", lambda a, b: False)):
        monkeypatch.setattr(fm, name, value)
    assert fm.run_move(state) == 0
    assert os.path.isjunction(downloads)
    assert snapshot(downloads) == before
    assert not list(downloads.parent.glob("*" + fm.TRASH_MARK + "*"))


def test_a_file_in_the_destination_that_i_did_not_copy_is_never_deleted(downloads, drive_d, two_volumes):
    """
    He may have put his own file at the destination in the meantime. The first
    version resumed INTO the destination and its repair pass deleted anything
    the original did not have, which is deleting data this module never wrote.
    Now the copy is built in a staging folder beside the destination, so there
    is nothing of his in the folder being written to, and a destination that
    has something in it stops the job before a byte is copied.
    """
    before = snapshot(downloads)
    plan = fm.make_plan(str(downloads), str(drive_d))
    state = fm._new_state(plan)
    fm._update_state(state, state="copying", pid=99999999, pid_started=1.0)
    (drive_d / "Downloads").mkdir()
    (drive_d / "Downloads" / "mine.txt").write_text("his own file")

    assert fm.run_move(state) != 0
    assert (drive_d / "Downloads" / "mine.txt").read_text() == "his own file"
    assert {p.name for p in (drive_d / "Downloads").iterdir()} == {"mine.txt"}
    assert snapshot(downloads) == before
    said = fm._read_state(state)["result"]
    assert "already something" in said and "before copying anything" in said.lower()


def test_my_own_half_written_part_file_is_cleaned_up_on_resume(downloads, drive_d, two_volumes):
    """A part file the killed run left in ITS OWN staging folder is not trusted and not left behind."""
    before = snapshot(downloads)
    plan = fm.make_plan(str(downloads), str(drive_d))
    state = fm._new_state(plan)
    fm._update_state(state, state="copying", pid=99999999, pid_started=1.0)
    staging = fm._staging_path(plan.dst, fm.move_key(plan.src, plan.dst))
    staging.mkdir()
    (staging / ("a.txt" + fm.PART_SUFFIX)).write_bytes(b"alp")
    assert fm.run_move(state) == 0
    assert snapshot(drive_d / "Downloads") == before
    assert not staging.exists()


@WINDOWS_ONLY
def test_a_program_that_recreates_the_old_folder_empty_does_not_stop_the_link(downloads, drive_d, two_volumes, monkeypatch):
    """
    Chrome recreates C:/Users/x/Downloads the moment it is gone. An empty
    folder in the way of the link is not a reason to give up.
    """
    before = snapshot(downloads)
    real_rename = os.rename
    fired = {"n": 0}

    def recreated(a, b, *args, **kwargs):
        real_rename(a, b, *args, **kwargs)
        if os.path.normcase(str(a)).endswith(os.path.normcase(str(downloads))) and not fired["n"]:
            fired["n"] += 1
            os.mkdir(str(a))

    monkeypatch.setattr(os, "rename", recreated)
    state = start(downloads, drive_d)
    assert fm.run_move(state) == 0
    assert os.path.isjunction(downloads)
    assert snapshot(downloads) == before


def test_a_program_that_fills_the_old_folder_stops_the_move_and_says_where_everything_is(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)
    real_rename = os.rename
    fired = {"n": 0}

    def refilled(a, b, *args, **kwargs):
        real_rename(a, b, *args, **kwargs)
        if os.path.normcase(str(a)).endswith(os.path.normcase(str(downloads))) and not fired["n"]:
            fired["n"] += 1
            os.mkdir(str(a))
            Path(str(a), "new.txt").write_text("a program wrote this")

    monkeypatch.setattr(os, "rename", refilled)
    state = start(downloads, drive_d)
    assert fm.run_move(state) != 0
    trash = list(downloads.parent.glob("*" + fm.TRASH_MARK + "*"))
    assert len(trash) == 1, "the original must still exist, renamed aside"
    assert snapshot(trash[0]) == before
    assert snapshot(drive_d / "Downloads") == before
    said = fm._read_state(state)["result"]
    assert str(trash[0]) in said, "he must be told where the original is"
    assert "put it back" not in said.lower() and "put the original back" not in said.lower()


def test_read_only_files_do_not_stop_the_old_copy_being_removed(tmp_path, drive_d, two_volumes):
    proj = make_tree(tmp_path / "proj", {".git/objects/ab/cdef": "x", "a.txt": "a"})
    os.chmod(proj / ".git" / "objects" / "ab" / "cdef", stat.S_IREAD)
    state = start(proj, drive_d, leave_link=False)
    assert fm.run_move(state) == 0
    assert not proj.exists()


def test_a_junction_that_cannot_be_made_puts_the_original_back(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)
    monkeypatch.setattr(fm, "_make_junction", lambda link, target: "access denied")
    state = start(downloads, drive_d, leave_link=True)
    assert fm.run_move(state) != 0
    assert snapshot(downloads) == before
    assert not list(downloads.parent.glob("*" + fm.TRASH_MARK + "*"))
    assert "link" in fm._read_state(state)["result"].lower()


def test_progress_is_written_while_it_copies(tmp_path, drive_d, two_volumes, monkeypatch):
    proj = make_tree(tmp_path / "proj", {f"f{i}.bin": b"x" * 100 for i in range(5)})
    monkeypatch.setattr(fm, "PROGRESS_EVERY_S", 0.0)
    state = start(proj, drive_d, leave_link=False)
    seen: list[int] = []
    real = fm._copy_one

    def watch(sp, dp):
        seen.append(fm._read_state(state).get("files_done", -1))
        real(sp, dp)

    monkeypatch.setattr(fm, "_copy_one", watch)
    fm.run_move(state)
    assert seen[0] == 0 and max(seen) >= 3


def test_the_same_move_has_the_same_key_so_it_can_be_resumed(downloads, drive_d):
    a = fm.move_key(downloads, drive_d / "Downloads")
    assert a == fm.move_key(str(downloads).upper(), str(drive_d / "Downloads").lower().replace("\\", "/"))
    assert a != fm.move_key(downloads, drive_d / "Other")


# ============================================================ tools and jobs
class Launched:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, args, cwd, label, summarise=None, timeout_s=None):
        self.calls.append({"args": list(args), "cwd": cwd, "label": label, "summarise": summarise,
                           "timeout_s": timeout_s})
        return f"Running {label} in the background - I'll tell you how it went."


@pytest.fixture
def launched(monkeypatch):
    from jarvis.tools import devwork
    spy = Launched()
    monkeypatch.setattr(devwork, "start_background_run", spy)
    return spy


def test_moving_without_having_heard_the_numbers_first_only_describes_it(downloads, drive_d, two_volumes, launched):
    said = fm.move_folder(str(downloads), str(drive_d))
    assert launched.calls == []
    assert "5 files" in said and "haven't moved anything" in said.lower()
    assert downloads.exists()


def test_after_the_plan_the_move_starts_in_the_background_and_says_so(downloads, drive_d, two_volumes, launched):
    plan_said = fm.plan_folder_move(str(downloads), str(drive_d))
    assert "5 files" in plan_said
    said = fm.move_folder(str(downloads), str(drive_d))
    assert len(launched.calls) == 1
    call = launched.calls[0]
    assert call["args"][1:4] == ["-m", "jarvis.tools.foldermove", "run"]
    state = Path(call["args"][4])
    record = fm._read_state(state)
    assert record["src"] == str(downloads) and record["dst"] == str(drive_d / "Downloads")
    assert record["leave_link"] is True
    assert "background" in said.lower() and "tell you" in said.lower()
    assert "nothing is removed" in said.lower() or "only after" in said.lower()
    assert downloads.exists(), "starting the job must not touch the source"


def test_a_plan_goes_stale(downloads, drive_d, two_volumes, launched, monkeypatch):
    fm.plan_folder_move(str(downloads), str(drive_d))
    later = time.monotonic() + fm.PLAN_TTL_S + 1
    monkeypatch.setattr(fm.time, "monotonic", lambda: later)
    fm.move_folder(str(downloads), str(drive_d))
    assert launched.calls == [], "a plan from a quarter of an hour ago is not what he heard"


def test_a_plan_for_one_folder_does_not_clear_a_move_of_another(downloads, drive_d, two_volumes, launched, tmp_path):
    other = make_tree(tmp_path / "C_drive" / "Videos", {"v.mp4": "v"})
    fm.plan_folder_move(str(other), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    assert launched.calls == []


def test_the_last_plan_is_remembered_for_go_ahead_and_move_it(downloads, drive_d, two_volumes, monkeypatch):
    assert fm.last_plan() is None
    fm.plan_folder_move(str(downloads), str(drive_d), leave_link=False)
    got = fm.last_plan()
    assert got == {"path": str(downloads), "destination": str(drive_d), "leave_link": False}
    later = time.monotonic() + fm.PLAN_TTL_S + 1
    monkeypatch.setattr(fm.time, "monotonic", lambda: later)
    assert fm.last_plan() is None


def test_a_refused_folder_is_never_launched(tmp_path, drive_d, launched, monkeypatch):
    win = make_tree(tmp_path / "Windows", {"a.dll": "x"})
    monkeypatch.setattr(fm, "_system_roots", lambda: [win])
    said = fm.move_folder(str(win), str(drive_d))
    assert launched.calls == [] and "windows" in said.lower()
    assert "traceback" not in said.lower()


def test_the_same_move_is_not_started_twice(downloads, drive_d, two_volumes, launched):
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    record_path = Path(launched.calls[0]["args"][4])
    me = fm._self_identity()
    fm._update_state(record_path, state="copying", pid=me[0], pid_started=me[1], updated_at=time.time())
    fm.plan_folder_move(str(downloads), str(drive_d))
    said = fm.move_folder(str(downloads), str(drive_d))
    assert len(launched.calls) == 1
    assert "already" in said.lower()


def test_an_interrupted_move_is_offered_as_a_resume(downloads, drive_d, two_volumes, launched):
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    record_path = Path(launched.calls[0]["args"][4])
    fm._update_state(record_path, state="copying", pid=99999999, pid_started=1.0, files_done=3,
                     files_total=5, bytes_done=2000, bytes_total=5015, updated_at=time.time())
    said = fm.plan_folder_move(str(downloads), str(drive_d))
    assert "interrupted" in said.lower() and "pick up" in said.lower()
    fm.move_folder(str(downloads), str(drive_d))
    assert len(launched.calls) == 2, "resuming is just starting the same move again"
    assert launched.calls[1]["args"][4] == launched.calls[0]["args"][4]


def test_the_job_uses_a_long_enough_deadline(downloads, drive_d, two_volumes, monkeypatch):
    """A move can outlast devwork's one-hour default; the deadline is passed on."""
    from jarvis.tools import devwork
    seen: dict = {}

    def spy(args, cwd, label, summarise=None, timeout_s=None):
        seen["timeout_s"] = timeout_s
        return f"Running {label} in the background - I'll tell you how it went."

    monkeypatch.setattr(devwork, "start_background_run", spy)
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    assert seen["timeout_s"] and seen["timeout_s"] > devwork.DEFAULT_TIMEOUT_S


def test_a_job_that_would_not_start_is_not_reported_as_started(downloads, drive_d, two_volumes, monkeypatch):
    from jarvis.tools import devwork
    monkeypatch.setattr(devwork, "start_background_run",
                        lambda *a, **k: "I couldn't write the job down, so I haven't started it.")
    fm.plan_folder_move(str(downloads), str(drive_d))
    said = fm.move_folder(str(downloads), str(drive_d))
    assert "haven't started" in said
    assert "Started" not in said


def test_a_job_that_would_not_start_leaves_no_record_to_be_mistaken_for_a_cut_short_move(downloads, drive_d, two_volumes, monkeypatch):
    from jarvis.tools import devwork
    monkeypatch.setattr(devwork, "start_background_run",
                        lambda *a, **k: "I couldn't write the job down, so I haven't started it.")
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    assert "no folder moves" in fm.folder_move_status().lower()
    again = fm.plan_folder_move(str(downloads), str(drive_d))
    assert "interrupted" not in again.lower()


@WINDOWS_ONLY
def test_the_spoken_result_names_drives_and_folders_not_long_paths(downloads, drive_d, two_volumes):
    """Text-to-speech reads 'C colon backslash Users backslash...' aloud, in full."""
    state = start(downloads, drive_d)
    assert fm.run_move(state) == 0
    said = fm._read_state(state)["result"]
    assert str(drive_d / "Downloads") not in said and str(downloads) not in said
    assert "Downloads" in said and "drive" in said.lower()


def test_the_announcement_is_the_result_line_the_job_printed():
    log = "copying...\n50%\nRESULT: Moved Downloads to D:\\Downloads. 4.2 gigabytes freed on the C drive.\n"
    assert fm.summarise(log) == "Moved Downloads to D:\\Downloads. 4.2 gigabytes freed on the C drive."
    assert "didn't say" in fm.summarise("").lower()
    assert "didn't say" in fm.summarise("Traceback (most recent call last):\n  boom").lower()


def test_same_drive_moves_are_an_instant_rename_with_a_link_when_asked_for(tmp_path, launched):
    """A link on the same drive frees nothing, so it is left only when he asks (see test_folder_move_review.py)."""
    src = make_tree(tmp_path / "work" / "Cafe", {"a.txt": "a", "s/b.txt": "b"})
    dest = tmp_path / "archive"
    dest.mkdir()
    before = snapshot(src)
    fm.plan_folder_move(str(src), str(dest), leave_link=True)
    said = fm.move_folder(str(src), str(dest), leave_link=True)
    assert launched.calls == [], "a rename needs no background job"
    assert snapshot(dest / "Cafe") == before
    assert "instant" in said.lower() or "straight away" in said.lower()
    if os.name == "nt":
        assert os.path.isjunction(src)


def test_same_drive_move_that_is_blocked_leaves_everything_where_it_was(tmp_path, launched, monkeypatch):
    src = make_tree(tmp_path / "work" / "Cafe", {"a.txt": "a"})
    dest = tmp_path / "archive"
    dest.mkdir()
    monkeypatch.setattr(os, "rename", lambda a, b, *x, **k: (_ for _ in ()).throw(PermissionError(5, "denied")))
    fm.plan_folder_move(str(src), str(dest))
    said = fm.move_folder(str(src), str(dest))
    assert (src / "a.txt").read_text() == "a"
    assert "using" in said.lower() and not (dest / "Cafe").exists()


def test_status_says_nothing_has_moved_when_nothing_has(launched):
    assert "no folder moves" in fm.folder_move_status().lower()


def test_status_reports_running_interrupted_and_finished(downloads, drive_d, two_volumes, launched):
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    path = Path(launched.calls[0]["args"][4])
    me = fm._self_identity()
    fm._update_state(path, state="copying", pid=me[0], pid_started=me[1], files_done=2, files_total=5,
                     bytes_done=2507, bytes_total=5015, updated_at=time.time())
    running = fm.folder_move_status()
    assert "50 percent" in running and "Downloads" in running
    fm._update_state(path, pid=99999999, pid_started=1.0)
    assert "interrupted" in fm.folder_move_status().lower()
    fm._update_state(path, state="done", result="Moved Downloads to D. 1 megabyte freed on the C drive.")
    assert "Moved Downloads" in fm.folder_move_status()


def test_a_record_that_was_queued_and_never_started_reads_as_interrupted(downloads, drive_d, two_volumes, launched):
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    path = Path(launched.calls[0]["args"][4])
    fm._update_state(path, updated_at=time.time() - fm.STALE_QUEUE_S - 5)
    assert "interrupted" in fm.folder_move_status().lower()


# ============================================ the link he leaves behind
@WINDOWS_ONLY
def test_the_disk_report_does_not_count_what_a_link_leads_to_as_this_drives_usage(tmp_path):
    """
    After "move my Downloads to D" there is a junction at C:/Users/x/Downloads.
    os.walk treats a junction as an ordinary folder, so disk_report would say
    "Downloads at 4.2 GB" about a folder that now lives on D: - and
    contradict the very sentence that said the move freed 4.2 GB on C:.
    """
    import _winapi

    from jarvis.tools import sysinfo

    elsewhere = make_tree(tmp_path / "D_drive" / "Downloads", {"big.bin": b"x" * 5000})
    root = make_tree(tmp_path / "Desktop", {"mine.txt": "hello"})
    _winapi.CreateJunction(str(elsewhere), str(root / "Downloads"))

    sizes, biggest, capped, scanned = sysinfo._scan_user_folders([root], [])
    assert scanned == 1, "the file behind the link was walked as if it lived here"
    assert sum(sizes.values()) == 5
    assert all("big.bin" not in path for _, path in biggest)


@WINDOWS_ONLY
def test_a_watched_folder_that_is_itself_a_link_is_not_scanned(tmp_path):
    import _winapi

    from jarvis.tools import sysinfo

    elsewhere = make_tree(tmp_path / "D_drive" / "Downloads", {"big.bin": b"x" * 5000})
    link = tmp_path / "C_drive" / "Downloads"
    link.parent.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(link))

    assert sysinfo._scan_user_folders([link], [])[3] == 0
    assert sysinfo._dir_total_size(link, []) == (0, 0, False)


@WINDOWS_ONLY
def test_old_downloads_behind_a_link_are_not_offered_as_space_to_free(tmp_path, monkeypatch):
    import _winapi

    from jarvis.tools import sysinfo

    home = tmp_path / "home"
    elsewhere = make_tree(tmp_path / "D_drive" / "Downloads", {"old.bin": b"x" * 5000})
    os.utime(elsewhere / "old.bin", (1_000_000_000, 1_000_000_000))
    home.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(home / "Downloads"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    assert sysinfo._old_downloads_size(30, []) == (0, 0, False)


# =========================================== the source name, spoken ways
def test_a_known_folder_name_resolves_to_the_real_one(monkeypatch):
    for spoken in ("downloads", "my downloads", "the downloads folder", "my Downloads folder"):
        path, why = fm._resolve_source(spoken)
        assert why is None
        assert path == Path.home() / "Downloads"
    assert fm._resolve_source("videos")[0] == Path.home() / "Videos"


def test_a_project_name_is_found_by_the_same_search_open_target_uses(tmp_path, monkeypatch):
    from jarvis.tools import launcher
    cafe = make_tree(tmp_path / "Desktop" / "Cafe", {"a": "a"})
    monkeypatch.setattr(launcher, "find_files", lambda q, limit=12, dirs_only=False: [str(cafe)])
    path, why = fm._resolve_source("my cafe folder")
    assert why is None and path == cafe


def test_two_folders_with_the_name_are_asked_about_not_guessed(tmp_path, monkeypatch):
    from jarvis.tools import launcher
    a = make_tree(tmp_path / "Desktop" / "Cafe", {"a": "a"})
    b = make_tree(tmp_path / "Documents" / "Cafe", {"b": "b"})
    monkeypatch.setattr(launcher, "find_files", lambda q, limit=12, dirs_only=False: [str(a), str(b)])
    path, why = fm._resolve_source("cafe")
    assert path is None and "which" in why.lower()


def test_folders_inside_dependency_folders_are_not_offered(tmp_path, monkeypatch):
    from jarvis.tools import launcher
    real = make_tree(tmp_path / "Desktop" / "projects", {"a": "a"})
    noise = make_tree(tmp_path / "Desktop" / "app" / "node_modules" / "x" / "projects", {"b": "b"})
    monkeypatch.setattr(launcher, "find_files", lambda q, limit=12, dirs_only=False: [str(noise), str(real)])
    path, why = fm._resolve_source("projects")
    assert why is None and path == real


def test_nothing_found_is_a_sentence(monkeypatch):
    from jarvis.tools import launcher
    monkeypatch.setattr(launcher, "find_files", lambda q, limit=12, dirs_only=False: [])
    path, why = fm._resolve_source("zzz-not-here")
    assert path is None and "couldn't find a folder" in why.lower()


# =================================================== the real CLI, end to end
def test_the_background_entry_point_really_moves_a_folder(tmp_path):
    """
    The one test that starts the real process the way devwork does:
    `python -m jarvis.tools.foldermove run <record>`. The child cannot see this
    process's patches, so the machine is made to look different through the
    environment instead - a profile that tmp_path is not inside - and the move
    is a same-volume one, which `run` still does as copy, check, rename, delete.
    """
    import subprocess

    repo = Path(__file__).resolve().parent.parent
    fake_home = tmp_path / "fakehome"
    (fake_home / "AppData" / "Local").mkdir(parents=True)
    (fake_home / "AppData" / "Roaming").mkdir(parents=True)
    src = make_tree(tmp_path / "work" / "Cafe", {"a.txt": "a", "s/b.bin": os.urandom(4000)})
    dest = tmp_path / "archive"
    dest.mkdir()
    before = snapshot(src)

    env = dict(os.environ)
    env.update({
        "USERPROFILE": str(fake_home), "HOME": str(fake_home),
        "LOCALAPPDATA": str(fake_home / "AppData" / "Local"),
        "APPDATA": str(fake_home / "AppData" / "Roaming"),
        "OneDrive": "", "OneDriveConsumer": "", "OneDriveCommercial": "",
    })
    # Plan in the child's world too, so the record is the one a real launch writes.
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from pathlib import Path;"
        "from jarvis.tools import foldermove as fm;"
        "fm.MOVES_DIR = Path(%r);"
        "fm._same_volume = lambda a, b: False;"
        "plan = fm.make_plan(%r, %r, leave_link=False);"
        "assert plan.refusal is None, plan.refusal;"
        "print(fm._new_state(plan))"
    ) % (str(repo), str(tmp_path / "_moves"), str(src), str(dest))
    made = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(repo), timeout=120)
    assert made.returncode == 0, made.stderr
    record = made.stdout.strip().splitlines()[-1]

    ran = subprocess.run([sys.executable, "-m", "jarvis.tools.foldermove", "run", record],
                         capture_output=True, text=True, env=env, cwd=str(repo), timeout=180)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "RESULT:" in ran.stdout
    assert snapshot(dest / "Cafe") == before
    assert not src.exists()


# ==================================================================== wiring
def test_the_three_tools_are_registered_specced_and_tiered():
    import yaml

    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.tools import REGISTRY

    tiers = yaml.safe_load(open("config/safety.yaml", encoding="utf-8"))
    for tool, tier in (("plan_folder_move", "green"), ("folder_move_status", "green"), ("move_folder", "red")):
        assert tool in REGISTRY and tool in TOOL_SPECS
        assert tool in tiers[tier]["tools"], f"{tool} should be {tier}"


def test_moving_a_folder_asks_out_loud_and_names_where_it_is_going():
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"
    verdict = engine.classify("move_folder", {"path": "C:/Users/user/Downloads", "destination": "D:/"})
    assert verdict.tier is Tier.RED
    assert "Downloads" in verdict.summary and "D:" in verdict.summary, verdict.summary
    assert verdict.summary.lower().startswith("move folder")


def test_the_brain_is_told_the_order_plan_then_move():
    """The model is the only thing that can make plan-before-move happen in a turn."""
    from jarvis.brain.agent import Brain
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    async def noop(*a, **k):
        return True

    prompt = Brain(CONFIG, SafetyEngine(CONFIG), None, confirm=noop, announce=noop).system_prompt()
    assert "plan_folder_move" in prompt and "move_folder" in prompt
    assert "never use move_file for a folder" in prompt.lower()
    assert "do not look for another way" in prompt.lower()


def test_a_page_cannot_start_a_move():
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    verdict = engine.classify("move_folder", {"path": "C:/x/Downloads", "destination": "D:/"}, origin="content")
    assert verdict.tier is Tier.BLACK


def test_the_gate_refuses_a_protected_source_or_destination_before_the_tool_runs():
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    assert engine.classify("move_folder", {"path": "~/.ssh", "destination": "D:/"}).tier is Tier.BLACK
    assert engine.classify("move_folder", {"path": "C:/x/Downloads", "destination": "~/.ssh"}).tier is Tier.BLACK
    assert engine.classify("move_folder", {"path": "C:/Windows", "destination": "D:/"}).tier is Tier.BLACK


def test_move_file_refuses_a_folder_and_points_at_move_folder(tmp_path):
    from jarvis.tools import filesystem as fs

    src = make_tree(tmp_path / "Cafe", {"a.txt": "a"})
    said = fs.move_file(str(src), str(tmp_path / "elsewhere"))
    assert "folder" in said.lower() and "move_folder" in said.replace(" ", "_")
    assert (src / "a.txt").exists() and not (tmp_path / "elsewhere").exists()


def test_move_file_still_moves_a_file(tmp_path):
    from jarvis.tools import filesystem as fs

    f = tmp_path / "x.txt"
    f.write_text("x")
    (tmp_path / "dest").mkdir()
    assert "Moved" in fs.move_file(str(f), str(tmp_path / "dest"))
    assert (tmp_path / "dest" / "x.txt").exists()


def test_copy_file_says_a_folder_is_a_folder(tmp_path):
    from jarvis.tools import filesystem as fs

    src = make_tree(tmp_path / "Cafe", {"a.txt": "a"})
    said = fs.copy_file(str(src), str(tmp_path / "copy"))
    assert "folder" in said.lower()
    assert "permission denied" not in said.lower()


def test_the_router_hears_move_my_downloads_to_d_as_a_dry_run_first():
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    router = IntentRouter(CONFIG)
    for phrase, path, dest in (
        ("move my downloads folder to d", "downloads", "d"),
        ("move my videos to the D drive", "videos", "d"),
        ("move the projects folder to d: drive", "projects", "d"),
        ("could you please move my cafe folder to D", "cafe", "d"),
        ("move my downloads to D:\\Stuff", "downloads", "D:\\Stuff"),
    ):
        intent = router.route(phrase)
        assert intent is not None and intent.tool == "plan_folder_move", phrase
        assert intent.args["path"].lower() == path.lower(), (phrase, intent.args)
        assert intent.args["destination"].lower().replace(" drive", "") .strip(": ") == dest.lower().strip(": "), (phrase, intent.args)


def test_the_router_does_not_take_over_moving_a_file_or_moving_things_on_screen():
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    router = IntentRouter(CONFIG)
    for phrase in ("move the mouse to the left", "move on", "move cafe.txt to the desktop",
                   "move this window to the other screen"):
        intent = router.route(phrase)
        assert intent is None or intent.tool != "plan_folder_move", phrase


def test_go_ahead_and_move_it_uses_the_plan_he_just_heard(downloads, drive_d, two_volumes):
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    router = IntentRouter(CONFIG)
    assert router.route("go ahead and move it") is None, "with no plan there is nothing to go ahead with"
    fm.plan_folder_move(str(downloads), str(drive_d))
    intent = router.route("go ahead and move it")
    assert intent is not None and intent.tool == "move_folder"
    # leave_link is passed on exactly as it was given (not given: the move
    # decides - a link across drives, none on the same drive).
    assert intent.args == {"path": str(downloads), "destination": str(drive_d), "leave_link": None}


def test_a_plan_in_the_conversation_does_not_outlive_its_ttl_for_the_router_either(downloads, drive_d, two_volumes, monkeypatch):
    from jarvis.brain.router import IntentRouter
    from jarvis.config import CONFIG

    router = IntentRouter(CONFIG)
    fm.plan_folder_move(str(downloads), str(drive_d))
    later = time.monotonic() + fm.PLAN_TTL_S + 1
    monkeypatch.setattr(fm.time, "monotonic", lambda: later)
    assert router.route("go ahead and move it") is None


# ====================================== a destination that is already occupied
# Found by running the dry run against the real machine on 2026-10-01: D: has a
# D:\Downloads in it (from December 2024, hundreds of old files), so the first
# thing he would ever say - "move my Downloads to D" - was answered "I won't
# merge a move into it, pick another place". Correct, and a dead end: the only
# way out was to dictate a full path. The move now goes beside it, under a name
# that says where it came from, and he is told so before anything is asked.
def _source_drive_letter(path: Path) -> str:
    return os.path.splitdrive(str(path))[0][:1].upper()


def test_an_occupied_destination_gets_a_free_name_beside_it_and_says_so(downloads, drive_d, two_volumes):
    make_tree(drive_d / "Downloads", {"old/2024.zip": "old", "keep.txt": "mine"})
    plan = fm.make_plan(str(downloads), str(drive_d))
    letter = _source_drive_letter(downloads)
    assert plan.refusal is None, plan.refusal
    assert plan.dst == drive_d / f"Downloads from {letter}"
    said = fm.describe_plan(plan)
    assert "already" in said.lower() and str(plan.dst) in said, said
    assert "haven't moved anything" in said.lower()


def test_the_free_name_is_never_one_that_is_taken_too(downloads, drive_d, two_volumes):
    letter = _source_drive_letter(downloads)
    make_tree(drive_d / "Downloads", {"keep.txt": "mine"})
    make_tree(drive_d / f"Downloads from {letter}", {"also.txt": "mine too"})
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal is None, plan.refusal
    assert plan.dst == drive_d / f"Downloads from {letter} 2"


def test_a_destination_he_named_exactly_is_never_redirected(downloads, drive_d, two_volumes):
    """He said WHERE. If that place is taken, that is a question for him, not something to quietly route around."""
    make_tree(drive_d / "Downloads", {"keep.txt": "mine"})
    plan = fm.make_plan(str(downloads), str(drive_d / "Downloads"))
    assert plan.refusal and "already" in plan.refusal.lower()


def test_an_empty_folder_in_the_way_is_used_not_routed_around(downloads, drive_d, two_volumes):
    (drive_d / "Downloads").mkdir()
    assert fm.make_plan(str(downloads), str(drive_d)).dst == drive_d / "Downloads"


def test_the_redirect_is_what_actually_runs(downloads, drive_d, two_volumes, launched):
    """The confirmation is asked about what he heard: the job's record carries the redirected path."""
    letter = _source_drive_letter(downloads)
    make_tree(drive_d / "Downloads", {"keep.txt": "mine"})
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    record = fm._read_state(Path(launched.calls[0]["args"][4]))
    assert record["dst"] == str(drive_d / f"Downloads from {letter}")


@WINDOWS_ONLY
def test_a_redirected_move_copies_into_the_new_name_and_leaves_his_old_folder_alone(downloads, drive_d, two_volumes):
    letter = _source_drive_letter(downloads)
    before = snapshot(downloads)
    make_tree(drive_d / "Downloads", {"old/2024.zip": "old", "keep.txt": "mine"})
    old = snapshot(drive_d / "Downloads")
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal is None, plan.refusal
    assert fm.run_move(fm._new_state(plan)) == 0
    assert snapshot(drive_d / f"Downloads from {letter}") == before
    assert snapshot(drive_d / "Downloads") == old, "what was already on D: must be exactly as it was"
    assert os.path.isjunction(downloads)


def test_an_interrupted_redirected_move_resumes_into_the_same_name(downloads, drive_d, two_volumes, launched):
    """Saying it again must not start a second copy in 'from C 2'."""
    letter = _source_drive_letter(downloads)
    make_tree(drive_d / "Downloads", {"keep.txt": "mine"})
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    record_path = Path(launched.calls[0]["args"][4])
    final = drive_d / f"Downloads from {letter}"
    # What the first run got done before it was cut: part of the copy, in the
    # staging folder beside the place it was going - never in the place itself.
    half = fm._staging_path(final, fm.move_key(downloads, final))
    make_tree(half, {"a.txt": "alpha"})
    fm._update_state(record_path, state="copying", pid=99999999, pid_started=1.0, files_done=1,
                     files_total=5, bytes_done=5, bytes_total=5015, updated_at=time.time())
    said = fm.plan_folder_move(str(downloads), str(drive_d))
    assert "interrupted" in said.lower() and "pick up" in said.lower(), said
    assert str(final) in said, "it must land on the same name, not 'from C 2'"
    fm.move_folder(str(downloads), str(drive_d))
    assert launched.calls[1]["args"][4] == launched.calls[0]["args"][4]


def test_a_move_that_is_running_into_the_redirect_is_reported_not_duplicated(downloads, drive_d, two_volumes, launched):
    make_tree(drive_d / "Downloads", {"keep.txt": "mine"})
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    record_path = Path(launched.calls[0]["args"][4])
    me = fm._self_identity()
    fm._update_state(record_path, state="copying", pid=me[0], pid_started=me[1], updated_at=time.time())
    said = fm.plan_folder_move(str(downloads), str(drive_d))
    assert "already" in said.lower() and "running" in said.lower(), said
    fm.move_folder(str(downloads), str(drive_d))
    assert len(launched.calls) == 1
