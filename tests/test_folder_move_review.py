"""
The independent review of the folder-move work (branch files-and-pc), one
test per finding, each written to FAIL against the code as the builder left it.

Every test here uses tmp_path trees. Nothing touches a real folder, a real
drive, the network, a real browser or a real Telegram. Two volumes are
pretended by patching fm._same_volume, because tmp_path is on one.

NAMES: pytest builds each test's folder from the test's own name, and the
never-touch list (correctly) looks at every part of a path for words like
"secret", "password", "credential" and "token". Test names in this file avoid
those words so that a protected-name check is never tripped by the folder
the test happens to run in.
"""
from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.tools import foldermove as fm  # noqa: E402

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="junctions are an NTFS thing")

# Stands for what is inside a protected file. It must never be said, shown,
# logged or written to a record: only ever copied.
PRIVATE = "zq-9f31-never-say-this-aloud"


# ----------------------------------------------------------------- helpers
def make_tree(root: Path, files: dict[str, bytes | str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    return root


def snapshot(root: Path) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for dp, dn, fn in os.walk(root):
        for d in dn:
            out[os.path.relpath(os.path.join(dp, d), root).replace("\\", "/")] = None
        for f in fn:
            fp = os.path.join(dp, f)
            out[os.path.relpath(fp, root).replace("\\", "/")] = Path(fp).read_bytes()
    return out


class Crash(BaseException):
    """Stands for the job being killed: nothing in the job catches BaseException."""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "MOVES_DIR", tmp_path / "_moves")
    monkeypatch.setattr(fm, "ROOT", tmp_path / "_jalen_repo")
    monkeypatch.setattr(fm, "_system_roots", lambda: [])
    monkeypatch.setattr(fm, "_own_interpreter_dirs", lambda: [])
    monkeypatch.setattr(fm, "_programs_running_from", lambda path: [])
    monkeypatch.setattr(fm, "_cloud_sync_roots", lambda: [])
    fm._forget_plans()
    yield
    fm._forget_plans()
    from jalen import taint

    taint.he_asked_again()


@pytest.fixture
def two_volumes(monkeypatch):
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


class Launched:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, args, cwd, label, summarise=None, timeout_s=None):
        self.calls.append({"args": list(args), "cwd": cwd, "label": label})
        return f"Running {label} in the background - I'll tell you how it went."


@pytest.fixture
def launched(monkeypatch):
    from jalen.tools import devwork

    spy = Launched()
    monkeypatch.setattr(devwork, "start_background_run", spy)
    return spy


def launch(folder: Path, drive: Path, spy: Launched, **kw) -> Path:
    """Say the move the way he does - the dry run, then the move - and return the record the job would get."""
    fm.plan_folder_move(str(folder), str(drive), **kw)
    fm.move_folder(str(folder), str(drive), **kw)
    return Path(spy.calls[-1]["args"][4])


def kill(state: Path) -> None:
    """The process that had this move is gone: what a real kill leaves in the record."""
    fm._update_state(state, pid=99999999, pid_started=1.0)


def trash_of(folder: Path) -> list[Path]:
    return list(folder.parent.glob("*" + fm.TRASH_MARK + "*"))


def start(folder: Path, drive: Path, leave_link: bool = False) -> Path:
    plan = fm.make_plan(str(folder), str(drive), leave_link=leave_link)
    assert plan.refusal is None, plan.refusal
    return fm._new_state(plan)


# ============================================================================
# 1. A stale record let the job merge into a destination he had filled
# ============================================================================
def test_a_stale_record_cannot_merge_a_move_into_a_folder_he_has_since_filled(downloads, drive_d, two_volumes):
    """
    A record in a failed/blocked state never expired. With one on file,
    make_plan called the move a resume and skipped the never-merge refusal;
    the copy then replaced his same-named file on D: with the source's.
    """
    before = snapshot(downloads)
    state = start(downloads, drive_d)
    fm._update_state(state, state="blocked", result="Close whatever has it open",
                     updated_at=time.time() - 40 * 86400)
    his = make_tree(drive_d / "Downloads", {"a.txt": "HIS a.txt - not the same file", "mine.txt": "his own"})
    his_before = snapshot(his)

    assert fm.run_move(state) != 0
    assert snapshot(his) == his_before, "a file he had on D: was replaced or removed"
    assert snapshot(downloads) == before


def test_a_destination_that_fills_up_between_the_dry_run_and_the_job_is_refused(downloads, drive_d, two_volumes):
    before = snapshot(downloads)
    state = start(downloads, drive_d)  # queued, exactly as move_folder leaves it
    his = make_tree(drive_d / "Downloads", {"a.txt": "HIS a.txt - not the same file", "mine.txt": "his own"})
    his_before = snapshot(his)

    assert fm.run_move(state) != 0
    assert snapshot(his) == his_before
    assert snapshot(downloads) == before


def test_a_folder_he_makes_at_the_destination_while_the_move_runs_is_never_replaced(
        downloads, drive_d, two_volumes, monkeypatch):
    """The last step is a rename onto a place that must be empty or absent; if it is not, the original goes back."""
    before = snapshot(downloads)
    final = drive_d / "Downloads"
    real = fm._place

    def he_makes_it_just_before(staging, where):
        make_tree(final, {"a.txt": "HIS a.txt", "mine.txt": "his own"})
        return real(staging, where)

    monkeypatch.setattr(fm, "_place", he_makes_it_just_before)
    state = start(downloads, drive_d)
    assert fm.run_move(state) != 0
    assert snapshot(final) == {"a.txt": b"HIS a.txt", "mine.txt": b"his own"}
    assert snapshot(downloads) == before, "the original was not put back"
    assert not trash_of(downloads)
    said = fm._read_state(state)["result"]
    assert "waiting in" in said and "put the original back" in said, said


def test_with_a_stale_record_and_his_files_there_the_dry_run_goes_beside_them(downloads, drive_d, two_volumes):
    state = start(downloads, drive_d)
    fm._update_state(state, state="failed", result="old")
    make_tree(drive_d / "Downloads", {"mine.txt": "his own"})
    plan = fm.make_plan(str(downloads), str(drive_d))
    assert plan.refusal is None, plan.refusal
    assert plan.dst != drive_d / "Downloads", "it would have merged into his folder"


def test_the_copy_does_not_put_anything_at_the_final_place_until_it_has_all_been_checked(
        downloads, drive_d, two_volumes, monkeypatch):
    """
    The structural reason a move can never overwrite what it did not write: the
    copy is built beside the destination and only RENAMED into place at the
    end, so the destination holds nothing of his and nothing half-done.
    """
    final = drive_d / "Downloads"
    seen: list[bool] = []
    real = fm._copy_one

    def watch(sp, dp):
        seen.append(final.exists())
        real(sp, dp)

    monkeypatch.setattr(fm, "_copy_one", watch)
    state = start(downloads, drive_d)
    assert fm.run_move(state) == 0
    assert seen and not any(seen), "the final folder existed while files were still being copied into it"
    assert final.is_dir()


# ============================================================================
# 2. Cut short during the delete, or between the rename and the link
# ============================================================================
def _crash_in(monkeypatch, name: str, *, after=None):
    """Make fm.<name> raise Crash once; returns a switch that puts the real one back."""
    real = getattr(fm, name)
    on = {"crash": True}

    def maybe(*a, **k):
        if on["crash"]:
            if after is not None:
                after(*a, **k)
            raise Crash()
        return real(*a, **k)

    monkeypatch.setattr(fm, name, maybe)
    return on


@WINDOWS_ONLY
def test_a_move_cut_short_while_deleting_the_old_copy_can_be_said_again_and_finishes(
        downloads, drive_d, two_volumes, launched, monkeypatch):
    before = snapshot(downloads)
    switch = _crash_in(monkeypatch, "_delete_tree")
    state = launch(downloads, drive_d, launched)
    with pytest.raises(Crash):
        fm.run_move(state)
    kill(state)
    assert os.path.isjunction(downloads) and len(trash_of(downloads)) == 1

    said = fm.plan_folder_move(str(downloads), str(drive_d))
    assert "already a link" not in said.lower() and "moved already" not in said.lower(), said
    assert "finish" in said.lower(), said

    fm.move_folder(str(downloads), str(drive_d))
    assert len(launched.calls) == 2, "saying it again must start the job that finishes it"
    switch["crash"] = False
    assert fm.run_move(Path(launched.calls[-1]["args"][4])) == 0
    assert not trash_of(downloads)
    assert os.path.isjunction(downloads)
    assert snapshot(drive_d / "Downloads") == before
    assert snapshot(downloads) == before


@WINDOWS_ONLY
def test_a_job_cut_short_part_way_through_deleting_finishes_when_run_again(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)
    real_delete = fm._delete_tree

    def half(path):
        for rel in ("sub/c.txt", "sub/deeper/d.txt"):
            victim = Path(path) / rel
            os.chmod(victim, stat.S_IWRITE)
            victim.unlink()
        raise Crash()

    monkeypatch.setattr(fm, "_delete_tree", half)
    state = start(downloads, drive_d, leave_link=True)
    with pytest.raises(Crash):
        fm.run_move(state)
    assert len(trash_of(downloads)) == 1

    monkeypatch.setattr(fm, "_delete_tree", real_delete)
    code = fm.run_move(state)
    said = fm._read_state(state)["result"]
    assert code == 0, said
    assert not trash_of(downloads)
    assert snapshot(drive_d / "Downloads") == before


@WINDOWS_ONLY
def test_a_move_cut_short_between_the_rename_and_the_link_can_be_said_again(
        downloads, drive_d, two_volumes, launched, monkeypatch):
    before = snapshot(downloads)
    switch = _crash_in(monkeypatch, "_make_junction")
    state = launch(downloads, drive_d, launched)
    with pytest.raises(Crash):
        fm.run_move(state)
    kill(state)
    assert not downloads.exists() and len(trash_of(downloads)) == 1

    said = fm.plan_folder_move(str(downloads), str(drive_d))
    assert "no folder at" not in said.lower(), said
    assert "finish" in said.lower(), said

    fm.move_folder(str(downloads), str(drive_d))
    switch["crash"] = False
    assert fm.run_move(Path(launched.calls[-1]["args"][4])) == 0
    assert os.path.isjunction(downloads)
    assert not trash_of(downloads)
    assert snapshot(downloads) == before


@WINDOWS_ONLY
def test_the_status_does_not_say_nothing_was_removed_when_part_of_the_old_copy_is_gone(
        downloads, drive_d, two_volumes, launched, monkeypatch):
    def half(path):
        victim = Path(path) / "a.txt"
        os.chmod(victim, stat.S_IWRITE)
        victim.unlink()
        raise Crash()

    monkeypatch.setattr(fm, "_delete_tree", half)
    state = launch(downloads, drive_d, launched)
    with pytest.raises(Crash):
        fm.run_move(state)
    kill(state)
    said = fm.folder_move_status().lower()
    assert "interrupted" in said
    assert "nothing was removed" not in said, said
    assert "old copy" in said or "still on" in said, said


@WINDOWS_ONLY
def test_nothing_more_is_deleted_if_what_is_left_of_the_old_copy_is_not_all_on_the_new_drive(
        downloads, drive_d, two_volumes, monkeypatch):
    """The one thing that must stay true after any cut: a file that exists only in the old copy is never deleted."""
    real_delete = fm._delete_tree

    def half(path):
        victim = Path(path) / "sub" / "c.txt"
        os.chmod(victim, stat.S_IWRITE)
        victim.unlink()
        raise Crash()

    monkeypatch.setattr(fm, "_delete_tree", half)
    state = start(downloads, drive_d, leave_link=True)
    with pytest.raises(Crash):
        fm.run_move(state)
    (old,) = trash_of(downloads)
    (drive_d / "Downloads" / "a.txt").unlink()  # now a.txt exists ONLY in the old copy

    monkeypatch.setattr(fm, "_delete_tree", real_delete)
    assert fm.run_move(state) != 0
    assert (old / "a.txt").read_text() == "alpha", "the only copy of a file was deleted"
    assert "a.txt" in fm._read_state(state)["result"]


# ============================================================================
# The property behind findings 1, 2 and 5: a kill at ANY step
# ============================================================================
def _crash_before_step(monkeypatch, k: int) -> dict:
    """
    Raise Crash just before the k-th (counting from 0) thing that changes the
    disk - a rename, a replace, a delete, a copy, the link - so the job is
    "killed" between any two of its steps. Returns the counter.
    """
    import shutil

    count = {"n": 0}

    def wrap(owner, name):
        real = getattr(owner, name)

        def inner(*a, **kw):
            here = count["n"]
            count["n"] += 1
            if here == k:
                raise Crash()
            return real(*a, **kw)

        monkeypatch.setattr(owner, name, inner)

    for owner, name in ((os, "rename"), (os, "replace"), (os, "rmdir"), (os, "unlink"),
                        (shutil, "copyfile"), (fm, "_make_junction")):
        wrap(owner, name)
    return count


def _files_missing_everywhere(before: dict, roots: list[Path]) -> list[str]:
    missing = []
    for rel, body in before.items():
        if body is None:
            continue
        if not any(r.exists() and (r / rel).is_file() and (r / rel).read_bytes() == body for r in roots):
            missing.append(rel)
    return missing


_SWEEP_FILES = {"a.txt": "alpha", "b.bin": os.urandom(3000), "sub/c.txt": "gamma",
                "sub/deeper/d.txt": "delta", ".hidden/e.txt": "echo",
                # carried along after a named yes, so the sweep covers that path too
                ".env": f"K={PRIVATE}", "config/token.json": f'{{"t": "{PRIVATE}"}}'}
_SWEEP_BEFORE = {k: (v if isinstance(v, bytes) else v.encode()) for k, v in _SWEEP_FILES.items()}


def _sweep_once(base: Path, monkeypatch, launched, leave_link: bool, kill_before: int | None):
    """
    One move on a fresh tree under `base`, killed just before step `kill_before`
    (never, for None). Returns (record path, steps taken, crashed, folder, drive).
    """
    folder = make_tree(base / "C_drive" / "Downloads", _SWEEP_FILES)
    drive = base / "D_drive"
    drive.mkdir()
    with monkeypatch.context() as m:
        m.setattr(fm, "MOVES_DIR", base / "_moves")
        m.setattr(fm, "_same_volume", lambda a, b: False)
        state = launch(folder, drive, launched, leave_link=leave_link)
        crashed = False
        with monkeypatch.context() as steps_patch:
            counter = _crash_before_step(steps_patch, 10 ** 9 if kill_before is None else kill_before)
            try:
                fm.run_move(state)
            except Crash:
                crashed = True
    return state, counter["n"], crashed, folder, drive


@pytest.mark.parametrize("leave_link", [False, pytest.param(True, marks=WINDOWS_ONLY)])
def test_a_kill_at_any_step_loses_no_file_and_the_move_can_always_be_said_again_and_finished(
        tmp_path, monkeypatch, launched, leave_link):
    """
    The sweep that backs the claim "stop the background job and nothing is
    lost". For every step the job takes, kill it just before that step; then
    (1) every file of the original must still exist, byte for byte, in the old
    place, the renamed original, the staging folder or the destination, and
    (2) saying the move again must not be refused and must finish it, with the
    destination equal to the original and nothing left beside it.
    """
    _, steps, crashed, _, _ = _sweep_once(tmp_path / "count", monkeypatch, launched, leave_link, None)
    assert not crashed and steps > 10, "the sweep counted too few steps to mean anything"

    recovered = 0
    for k in range(steps):
        base = tmp_path / f"k{k}"
        state, _, crashed, folder, drive = _sweep_once(base, monkeypatch, launched, leave_link, k)
        if not crashed:
            continue  # this run took fewer steps than the counting one (a progress write that was skipped)
        final = drive / "Downloads"
        with monkeypatch.context() as m:
            m.setattr(fm, "MOVES_DIR", base / "_moves")
            m.setattr(fm, "_same_volume", lambda a, b: False)
            leftovers = [*base.joinpath("C_drive").glob("*" + fm.TRASH_MARK + "*"),
                         *drive.glob("*" + fm.STAGING_MARK + "*")]
            lost = _files_missing_everywhere(_SWEEP_BEFORE, [folder, final, *leftovers])
            assert not lost, f"killed before step {k}: {lost} exist nowhere"

            kill(state)
            if not leftovers and final.is_dir() and (os.path.isjunction(folder) or not folder.exists()):
                # Everything was done but the last line of the record: finished, and it must say so.
                assert "finished" in fm.folder_move_status().lower(), fm.folder_move_status()
                continue

            said = fm.plan_folder_move(str(folder), str(drive), leave_link=leave_link)
            for refusal in ("already a link", "no folder at", "already something", "couldn't"):
                assert refusal not in said.lower(), f"killed before step {k}: {said}"
            fm.move_folder(str(folder), str(drive), leave_link=leave_link)
            again = Path(launched.calls[-1]["args"][4])
            code = fm.run_move(again)
            assert code == 0, f"killed before step {k}: {fm._read_state(again)['result']}"
            assert {rel: body for rel, body in snapshot(final).items() if body is not None} == _SWEEP_BEFORE
            assert not list(base.joinpath("C_drive").glob("*" + fm.TRASH_MARK + "*"))
            assert not list(drive.glob("*" + fm.STAGING_MARK + "*"))
            if leave_link:
                assert os.path.isjunction(folder)
            recovered += 1
    print(f"sweep leave_link={leave_link}: {steps} steps, {recovered} kill points recovered and finished")
    assert recovered >= steps // 2, f"only {recovered} of {steps} kill points were exercised"


@WINDOWS_ONLY
def test_a_cut_short_move_into_a_redirected_place_is_finished_by_saying_the_same_thing(
        downloads, drive_d, two_volumes, launched, monkeypatch):
    """The move went to 'Downloads from C' because D:/Downloads was his; saying 'to D' again must find it."""
    before = snapshot(downloads)
    his = make_tree(drive_d / "Downloads", {"old.zip": "his old files"})
    his_before = snapshot(his)
    switch = _crash_in(monkeypatch, "_delete_tree")
    state = launch(downloads, drive_d, launched)
    with pytest.raises(Crash):
        fm.run_move(state)
    kill(state)

    said = fm.plan_folder_move(str(downloads), str(drive_d))
    letter = os.path.splitdrive(str(downloads))[0][:1].upper()
    assert "finish" in said.lower() and f"Downloads from {letter}" in said, said
    fm.move_folder(str(downloads), str(drive_d))
    switch["crash"] = False
    assert fm.run_move(Path(launched.calls[-1]["args"][4])) == 0
    assert snapshot(drive_d / f"Downloads from {letter}") == before
    assert snapshot(his) == his_before, "his own folder on D: was touched"


@WINDOWS_ONLY
def test_a_cut_short_move_blocks_a_new_one_to_a_different_place_and_says_how_to_finish_it(
        downloads, drive_d, two_volumes, launched, monkeypatch, tmp_path):
    _crash_in(monkeypatch, "_delete_tree")
    state = launch(downloads, drive_d, launched)
    with pytest.raises(Crash):
        fm.run_move(state)
    kill(state)
    elsewhere = tmp_path / "E_drive"
    elsewhere.mkdir()
    said = fm.plan_folder_move(str(downloads), str(elsewhere))
    assert "cut short" in said.lower() and "finish" in said.lower(), said
    assert fm.last_plan() is None or fm.last_plan()["destination"] != str(elsewhere)


@WINDOWS_ONLY
def test_a_cut_short_move_whose_record_is_gone_says_the_original_is_safe_and_changes_nothing(
        downloads, drive_d, two_volumes, launched, monkeypatch):
    _crash_in(monkeypatch, "_delete_tree")
    state = launch(downloads, drive_d, launched)
    with pytest.raises(Crash):
        fm.run_move(state)
    state.unlink()
    (old,) = trash_of(downloads)
    said = fm.plan_folder_move(str(downloads), str(drive_d))
    assert "record" in said.lower() and "nothing was lost" in said.lower(), said
    assert old.is_dir() and (old / "a.txt").exists()


def test_a_dry_run_that_runs_out_of_time_while_still_looking_for_protected_files_will_not_name_a_partial_list(
        tmp_path, drive_d, two_volumes, monkeypatch):
    from types import SimpleNamespace

    proj = make_tree(tmp_path / "C_drive" / "Cafe", {".env": "K=1", "a/x.txt": "x", "b/y.txt": "y", "c/z.txt": "z"})
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return 0.0 if ticks["n"] <= 3 else 10_000.0

    with monkeypatch.context() as m:
        # Only foldermove's own clock: the deadline is set, the root is read, one
        # more folder is read, and then the time is up with folders still unread.
        m.setattr(fm, "time", SimpleNamespace(monotonic=clock, time=time.time, sleep=time.sleep))
        plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "too big" in plan.refusal.lower() and ".env" in plan.refusal, plan.refusal


def test_more_protected_files_than_can_be_named_one_by_one_refuses_the_folder(tmp_path, drive_d, two_volumes):
    proj = make_tree(tmp_path / "C_drive" / "Many", {f"svc{i}/.env": "K=1" for i in range(fm.MAX_CARRIED + 1)})
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "more than" in plan.refusal.lower() and "protected" in plan.refusal.lower(), plan.refusal
    ok = make_tree(tmp_path / "C_drive" / "Some", {f"svc{i}/.env": "K=1" for i in range(fm.MAX_CARRIED)})
    assert fm.make_plan(str(ok), str(drive_d)).refusal is None


def test_the_question_never_raises_whatever_it_is_given(engine):
    from jalen.tools import foldermove

    for args in ({}, {"path": 3, "destination": None}, {"path": "", "destination": "d"}, {"path": "x"}):
        assert foldermove.confirmation_summary(args) is None
    assert engine.classify("move_folder", {}).summary == "move folder"


def test_known_folder_names_are_recognised_the_way_people_say_them():
    for said in ("downloads", "my videos", "the pictures folder", "My Documents", "desktop"):
        assert fm.is_known_folder_name(said), said
    for said in ("cafe", "report.pdf", "the window", "d", ""):
        assert not fm.is_known_folder_name(said), said


def test_a_dry_run_that_has_been_started_forgets_what_he_heard_so_it_cannot_be_confirmed_twice(
        downloads, drive_d, two_volumes, launched, engine):
    fm.plan_folder_move(str(downloads), str(drive_d))
    fm.move_folder(str(downloads), str(drive_d))
    assert len(launched.calls) == 1
    again = engine.classify("move_folder", {"path": str(downloads), "destination": str(drive_d)}).summary
    assert "measured" in again.lower(), again
    fm.move_folder(str(downloads), str(drive_d))
    assert len(launched.calls) == 1, "a second yes started the same move again"


# ============================================================================
# 5. A file that left the source after it was copied
# ============================================================================
def test_a_file_renamed_in_the_source_after_it_was_copied_does_not_wedge_the_move(
        tmp_path, drive_d, two_volumes, monkeypatch):
    proj = make_tree(tmp_path / "C_drive" / "Downloads", {"movie.crdownload": b"x" * 3000, "a.txt": "a"})
    real_verify = fm.verify_trees
    monkeypatch.setattr(fm, "verify_trees", lambda *a, **k: (_ for _ in ()).throw(Crash()))
    state = start(proj, drive_d)
    with pytest.raises(Crash):
        fm.run_move(state)  # killed right after the copy, before anything was checked

    monkeypatch.setattr(fm, "verify_trees", real_verify)
    os.rename(proj / "movie.crdownload", proj / "movie.pdf")  # the browser finished the download
    after = snapshot(proj)

    code = fm.run_move(state)
    assert code == 0, fm._read_state(state)["result"]
    assert snapshot(drive_d / "Downloads") == after
    assert "movie.crdownload" not in snapshot(drive_d / "Downloads")


# ============================================================================
# 10. How much is checked before the irreversible delete, and the record writes
# ============================================================================
def test_every_file_is_compared_by_content_not_only_a_sample_when_the_folder_is_not_huge(
        tmp_path, drive_d, two_volumes, monkeypatch):
    proj = make_tree(tmp_path / "C_drive" / "Proj", {f"f{i:02d}.bin": os.urandom(200) for i in range(25)})
    before = snapshot(proj)
    monkeypatch.setattr(fm, "SAMPLE_FILES", 0)
    monkeypatch.setattr(fm, "SAMPLE_LARGEST", 0)
    real = fm._copy_one

    def flip_one(sp, dp):
        real(sp, dp)
        if sp.endswith("f07.bin"):
            blob = bytearray(Path(dp).read_bytes())
            blob[3] ^= 0xFF
            stamp = os.stat(dp).st_mtime_ns
            os.chmod(dp, stat.S_IWRITE)
            Path(dp).write_bytes(bytes(blob))
            os.utime(dp, ns=(stamp, stamp))

    monkeypatch.setattr(fm, "_copy_one", flip_one)
    state = start(proj, drive_d)
    assert fm.run_move(state) != 0, "a copy that differs in content, with the same size and date, was accepted"
    assert snapshot(proj) == before
    assert not trash_of(proj)


def test_a_folder_too_big_to_hash_every_file_says_how_many_were_hashed_and_a_small_one_says_every_one(
        tmp_path, drive_d, two_volumes, monkeypatch):
    proj = make_tree(tmp_path / "C_drive" / "Proj", {f"f{i:02d}.bin": os.urandom(100) for i in range(20)})
    state = start(proj, drive_d)
    assert fm.run_move(state) == 0
    assert "every one by checksum" in fm._read_state(state)["result"]

    big = make_tree(tmp_path / "C_drive" / "Big", {f"f{i:02d}.bin": os.urandom(100) for i in range(20)})
    monkeypatch.setattr(fm, "FULL_HASH_UP_TO_BYTES", 10)
    monkeypatch.setattr(fm, "SAMPLE_FILES", 3)
    monkeypatch.setattr(fm, "SAMPLE_LARGEST", 1)
    state = start(big, drive_d)
    assert fm.run_move(state) == 0
    said = fm._read_state(state)["result"]
    assert "every one by checksum" not in said and "by checksum" in said, said


def test_a_file_where_a_folder_belongs_in_my_own_staging_folder_is_cleared_not_a_crash(downloads, drive_d, two_volumes):
    """A leftover of an earlier try is in a folder only this module wrote to; it may go."""
    before = snapshot(downloads)
    state = start(downloads, drive_d)
    plan = fm.make_plan(str(downloads), str(drive_d), leave_link=False)
    staging = fm._staging_path(plan.dst, fm.move_key(plan.src, plan.dst))
    staging.mkdir()
    (staging / "sub").write_text("a file where the folder 'sub' belongs")
    (staging / "a.txt").mkdir()  # a folder where the file a.txt belongs
    assert fm.run_move(state) == 0, fm._read_state(state)["result"]
    assert snapshot(drive_d / "Downloads") == before


def test_an_error_nobody_planned_for_is_said_in_a_sentence_and_removes_nothing(downloads, drive_d, two_volumes, monkeypatch):
    before = snapshot(downloads)
    monkeypatch.setattr(fm, "copy_tree", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    state = start(downloads, drive_d)
    assert fm.run_move(state) == 1
    record = fm._read_state(state)
    assert record["state"] == "failed" and "unexpectedly" in record["result"] and "boom" in record["result"]
    assert snapshot(downloads) == before


@WINDOWS_ONLY
def test_the_status_of_a_move_that_finished_but_was_stopped_before_it_wrote_that_down_says_so(
        downloads, drive_d, two_volumes, launched, monkeypatch):
    before = snapshot(downloads)
    state = launch(downloads, drive_d, launched)
    real = fm._finish
    monkeypatch.setattr(fm, "_finish", lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        fm.run_move(state)
    monkeypatch.setattr(fm, "_finish", real)
    kill(state)
    said = fm.folder_move_status().lower()
    assert "looks as if it finished" in said and "nothing was removed" not in said, said
    assert snapshot(downloads) == before


def test_a_failed_write_of_the_progress_record_does_not_stop_the_move_part_way(
        downloads, drive_d, two_volumes, monkeypatch):
    """C: is nearly always full, and the record lives on C:. It is progress, not the move."""
    before = snapshot(downloads)
    real = fm._write_atomic

    def full_disk(path, record):
        if record.get("state") in ("switching", "cleaning"):
            raise OSError(28, "No space left on device")
        return real(path, record)

    state = start(downloads, drive_d)
    monkeypatch.setattr(fm, "_write_atomic", full_disk)
    assert fm.run_move(state) == 0
    assert not trash_of(downloads)
    assert snapshot(drive_d / "Downloads") == before


# ============================================================================
# 3. The router must not take ordinary file moves
# ============================================================================
@pytest.fixture
def router():
    from jalen.brain.router import IntentRouter
    from jalen.config import CONFIG

    return IntentRouter(CONFIG)


@pytest.mark.parametrize("phrase", [
    "move report.pdf to d",
    "move my video.mp4 to d:\\videos",
    "move C:\\x\\notes.txt to D:\\Docs",
    "move the window to d",
    "move the cursor to c",
    "move cafe.txt to the d drive",
])
def test_the_router_does_not_answer_a_file_or_a_thing_on_screen_with_a_folder_refusal(router, phrase):
    intent = router.route(phrase)
    assert intent is None or intent.tool != "plan_folder_move", (phrase, intent)


@pytest.mark.parametrize("phrase", [
    "move my downloads to d", "move my downloads folder to the d drive", "move the videos folder to d",
    "move my cafe folder to d", "could you please move the pictures to D",
])
def test_the_router_still_takes_a_folder_it_can_tell_is_a_folder(router, phrase):
    intent = router.route(phrase)
    assert intent is not None and intent.tool == "plan_folder_move", phrase


def test_a_path_that_is_a_real_folder_is_taken_and_a_path_that_is_a_file_is_not(router, tmp_path):
    folder = make_tree(tmp_path / "Cafe", {"a.txt": "a"})
    (tmp_path / "notes.txt").write_text("x")
    taken = router.route(f"move {folder} to d")
    assert taken is not None and taken.tool == "plan_folder_move"
    left = router.route(f"move {tmp_path / 'notes.txt'} to d")
    assert left is None or left.tool != "plan_folder_move"


# ============================================================================
# 6. Same-drive moves
# ============================================================================
def test_a_folder_can_be_moved_into_a_known_folder_by_its_name(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    cafe = make_tree(tmp_path / "work" / "Cafe", {"a.txt": "a"})
    plan = fm.make_plan(str(cafe), "documents")
    assert plan.refusal is None, plan.refusal
    assert plan.dst == home / "Documents" / "Cafe"


def test_a_same_drive_move_leaves_no_link_unless_he_asks(tmp_path, launched):
    src = make_tree(tmp_path / "work" / "Cafe", {"a.txt": "a", "s/b.txt": "b"})
    dest = tmp_path / "archive"
    dest.mkdir()
    fm.plan_folder_move(str(src), str(dest))
    said = fm.move_folder(str(src), str(dest))
    assert launched.calls == []
    assert (dest / "Cafe" / "a.txt").exists()
    assert not src.exists(), "the old path was left behind as a link although nothing was freed"
    assert "link" not in said.lower()


@WINDOWS_ONLY
def test_a_same_drive_move_leaves_a_link_when_he_asks_for_one(tmp_path):
    src = make_tree(tmp_path / "work" / "Cafe", {"a.txt": "a"})
    dest = tmp_path / "archive"
    dest.mkdir()
    fm.plan_folder_move(str(src), str(dest), leave_link=True)
    fm.move_folder(str(src), str(dest), leave_link=True)
    assert os.path.isjunction(src)


def test_moving_onto_an_empty_folder_of_the_same_name_on_the_same_drive_works(tmp_path):
    src = make_tree(tmp_path / "work" / "Cafe", {"a.txt": "a"})
    dest = tmp_path / "archive"
    (dest / "Cafe").mkdir(parents=True)
    fm.plan_folder_move(str(src), str(dest))
    said = fm.move_folder(str(src), str(dest))
    assert "using" not in said.lower() and "winerror" not in said.lower(), said
    assert (dest / "Cafe" / "a.txt").read_text() == "a"


def test_a_folder_that_appears_at_the_destination_is_called_that_not_something_in_use(tmp_path):
    src = make_tree(tmp_path / "work" / "Cafe", {"a.txt": "a"})
    dest = tmp_path / "archive"
    dest.mkdir()
    fm.plan_folder_move(str(src), str(dest))
    make_tree(dest / "Cafe", {"his.txt": "his"})  # arrives after the dry run
    said = fm.move_folder(str(src), str(dest))
    assert "using" not in said.lower() and "winerror" not in said.lower(), said
    assert (dest / "Cafe" / "his.txt").read_text() == "his"
    assert (src / "a.txt").exists()


# ============================================================================
# 7. A page must not be able to arm a move
# ============================================================================
def test_the_dry_run_is_refused_when_the_request_came_from_something_he_read():
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    verdict = engine.classify("plan_folder_move", {"path": "downloads", "destination": "d"}, origin="content")
    assert verdict.tier is Tier.BLACK


def test_a_dry_run_made_on_a_turn_that_read_a_page_is_not_remembered_for_move_it(downloads, drive_d, two_volumes):
    from jalen import taint

    taint.mark("web page on example.com")
    fm.plan_folder_move(str(downloads), str(drive_d))
    assert fm.last_plan() is None, "a page armed 'move it'"


# ============================================================================
# 8. The confirmation names what will really happen
# ============================================================================
@pytest.fixture
def engine():
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine

    e = SafetyEngine(CONFIG)
    e.paranoid = False
    e.posture = "irreversible_only"
    return e


def test_the_confirmation_names_the_real_source_and_the_redirected_destination(downloads, drive_d, two_volumes, engine):
    make_tree(drive_d / "Downloads", {"old.zip": "his old files"})
    fm.plan_folder_move(str(downloads), str(drive_d))
    summary = engine.classify("move_folder", {"path": str(downloads), "destination": str(drive_d)}).summary
    letter = os.path.splitdrive(str(downloads))[0][:1].upper()
    assert f"Downloads from {letter}" in summary, summary
    assert str(downloads) in summary or str(downloads).replace("\\", "/") in summary, summary
    assert "link" in summary.lower(), summary


def test_the_confirmation_says_when_no_link_is_left(downloads, drive_d, two_volumes, engine):
    fm.plan_folder_move(str(downloads), str(drive_d), leave_link=False)
    summary = engine.classify("move_folder",
                              {"path": str(downloads), "destination": str(drive_d), "leave_link": False}).summary
    assert "no link" in summary.lower() or "without a link" in summary.lower(), summary


def test_a_confirmation_without_a_dry_run_behind_it_says_so(downloads, drive_d, engine):
    summary = engine.classify("move_folder", {"path": str(downloads), "destination": str(drive_d)}).summary
    assert summary.lower().startswith("move folder")
    assert "measured" in summary.lower() or "dry run" in summary.lower(), summary


# ============================================================================
# 9. open_target
# ============================================================================
@pytest.fixture
def desk(tmp_path, monkeypatch):
    from jalen.tools import launcher

    root = tmp_path / "Desktop"
    root.mkdir()
    opened: list[str] = []
    monkeypatch.setattr(launcher, "_search_roots", lambda: [root])
    monkeypatch.setattr(launcher, "_startfile", lambda target: opened.append(str(target)))
    monkeypatch.setattr(launcher, "_appeared", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "resolve_app", lambda name: (None, None))
    monkeypatch.setattr(launcher, "_load_aliases", lambda: {})

    class Desk:
        path = root
        launched = opened

    return Desk


@pytest.mark.parametrize("spoken", ["the stuff folder", "stuff", "the things folder", "my stuff"])
def test_a_folder_really_called_stuff_or_things_is_opened(desk, spoken):
    from jalen.tools import launcher

    word = spoken.replace("the ", "").replace("my ", "").replace(" folder", "")
    (desk.path / word.title()).mkdir()
    said = launcher.open_target(spoken)
    assert "not sure what" not in said.lower(), said
    assert desk.launched and desk.launched[0].lower().endswith(word), (said, desk.launched)


@pytest.mark.parametrize("spoken", ["that thing", "this one", "it up", "that stuff again"])
def test_a_pronoun_phrase_is_still_asked_about_even_when_a_file_contains_the_word(desk, spoken):
    from jalen.tools import launcher

    (desk.path / "something_else.txt").write_text("x")
    (desk.path / "one_more.txt").write_text("x")
    said = launcher.open_target(spoken)
    assert desk.launched == [], said
    assert "not sure what" in said.lower(), said


def test_a_script_found_in_place_of_a_missing_path_is_never_launched(desk, monkeypatch, tmp_path):
    from jalen.tools import launcher

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    script = downloads / "setup_tool.bat"
    script.write_text("echo hi")
    monkeypatch.setattr(launcher, "find_files", lambda q, limit=12, dirs_only=False: [str(script)])
    said = launcher.open_target(str(tmp_path / "gone" / "setup_tool.bat"))
    assert desk.launched == [], "a program found by name was run without being asked"
    assert "setup_tool.bat" in said and "Downloads" in said


def test_a_document_found_in_place_of_a_missing_path_is_still_opened_and_announced(desk, monkeypatch, tmp_path):
    from jalen.tools import launcher

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    doc = downloads / "notes.pdf"
    doc.write_text("x")
    monkeypatch.setattr(launcher, "find_files", lambda q, limit=12, dirs_only=False: [str(doc)])
    said = launcher.open_target(str(tmp_path / "gone" / "notes.pdf"))
    assert desk.launched == [str(doc)]
    assert "Downloads" in said


# ============================================================================
# 4. OWNER DECISION: a folder with .env files moves, after a named yes
# ============================================================================
@pytest.fixture
def project(tmp_path):
    return make_tree(tmp_path / "C_drive" / "Cafe", {
        "app.py": "print('hi')",
        "src/main.py": "x = 1",
        ".env": f"API_KEY={PRIVATE}\n",
        "config/token.json": f'{{"refresh": "{PRIVATE}"}}',
        "node_modules/pkg/.env": "NODE_VENDOR=1",
        "node_modules/pkg/index.js": "module.exports = 1",
    })


def test_a_folder_with_env_files_is_planned_and_each_protected_file_is_named(project, drive_d, two_volumes):
    said = fm.plan_folder_move(str(project), str(drive_d))
    assert "protected" in said.lower() and ".env" in said and "config/token.json" in said, said
    assert "2 protected files" in said, said
    assert "unread" in said.lower(), said
    assert "node_modules" not in said, "a vendor folder's files are not his and must not be listed"
    assert PRIVATE not in said


def test_the_confirmation_lists_the_protected_files_by_relative_path(project, drive_d, two_volumes, engine):
    fm.plan_folder_move(str(project), str(drive_d))
    summary = engine.classify("move_folder", {"path": str(project), "destination": str(drive_d)}).summary
    assert "2 protected files" in summary and "carried over unread" in summary, summary
    assert ".env" in summary and "config/token.json" in summary, summary
    assert PRIVATE not in summary


def test_the_move_carries_the_protected_files_byte_for_byte_and_never_says_what_is_in_them(
        project, drive_d, two_volumes, launched, capsys, engine):
    before = snapshot(project)
    spoken = [fm.plan_folder_move(str(project), str(drive_d))]
    spoken.append(engine.classify("move_folder", {"path": str(project), "destination": str(drive_d)}).summary)
    spoken.append(fm.move_folder(str(project), str(drive_d), leave_link=False))
    state = Path(launched.calls[-1]["args"][4])
    assert fm.run_move(state) == 0, fm._read_state(state)["result"]
    final = drive_d / "Cafe"
    assert snapshot(final) == before
    assert (final / ".env").read_bytes() == (b"API_KEY=" + PRIVATE.encode() + b"\n")
    assert not project.exists()

    spoken += [fm.folder_move_status(), capsys.readouterr().out]
    spoken += [p.read_text(encoding="utf-8", errors="replace") for p in fm.MOVES_DIR.glob("*")]
    for text in spoken:
        assert PRIVATE not in text, text[:200]


def test_a_protected_file_is_never_read_for_a_checksum(project, drive_d, two_volumes, monkeypatch):
    real = fm._hash_file
    touched: list[str] = []

    def spy(path):
        touched.append(os.path.basename(path))
        return real(path)

    monkeypatch.setattr(fm, "_hash_file", spy)
    state = start(project, drive_d)
    assert fm.run_move(state) == 0
    assert touched, "nothing was checked by content at all"
    assert ".env" not in touched and "token.json" not in touched


def test_a_protected_file_that_arrived_copied_wrongly_stops_the_move_and_the_original_stays(
        project, drive_d, two_volumes, monkeypatch, capsys):
    before = snapshot(project)
    real = fm._copy_one

    def flip(sp, dp):
        real(sp, dp)
        if sp.endswith(".env") and "node_modules" not in sp:
            blob = bytearray(Path(dp).read_bytes())
            blob[2] ^= 0xFF
            stamp = os.stat(dp).st_mtime_ns
            os.chmod(dp, stat.S_IWRITE)
            Path(dp).write_bytes(bytes(blob))
            os.utime(dp, ns=(stamp, stamp))

    monkeypatch.setattr(fm, "_copy_one", flip)
    state = start(project, drive_d)
    assert fm.run_move(state) != 0
    assert snapshot(project) == before
    said = fm._read_state(state)["result"] + capsys.readouterr().out
    assert ".env" in said and PRIVATE not in said


def test_a_protected_file_that_was_not_in_the_list_he_heard_stops_the_move_before_anything_is_copied(
        project, drive_d, two_volumes, launched):
    before = snapshot(project)
    fm.plan_folder_move(str(project), str(drive_d))
    (project / "deploy.env").write_text("X=1")  # arrives after he heard the list
    said = fm.move_folder(str(project), str(drive_d))
    assert launched.calls == [], said
    assert "deploy.env" in said, said
    assert snapshot(project) == before | {"deploy.env": b"X=1"}


def test_a_move_worded_differently_from_the_dry_run_cannot_carry_protected_files_without_a_question_that_names_them(
        project, drive_d, two_volumes, launched):
    """
    The question is worded from the dry run he heard, found by the words the
    move is called with. If the brain calls move_folder with other words (the
    same place, spelled another way) the question could not name the files, and
    a named yes is the whole condition: so nothing starts, and the next call
    has a question that does.
    """
    fm.plan_folder_move(str(project), str(drive_d))
    other_words = str(drive_d) + os.sep
    said = fm.move_folder(str(project), other_words)
    assert launched.calls == [] and ".env" in said and "didn't name" in said, said
    assert fm.move_folder(str(project), other_words).lower().startswith("started moving")
    assert len(launched.calls) == 1


def test_the_job_itself_refuses_a_protected_file_that_arrived_after_it_was_queued(project, drive_d, two_volumes, launched):
    fm.plan_folder_move(str(project), str(drive_d))
    fm.move_folder(str(project), str(drive_d))
    state = Path(launched.calls[-1]["args"][4])
    (project / "later.env").write_text("X=1")
    assert fm.run_move(state) != 0
    assert not (drive_d / "Cafe").exists()
    assert (project / "later.env").exists()
    assert "later.env" in fm._read_state(state)["result"]


@pytest.mark.parametrize("name", [
    "vault.json", "telegram.session", "telegram.session-journal", "id_rsa", "server.pem", "api.key",
    "keys.kdbx", "my passwords.txt", "credentials.json", "client_secret_123.json", "prod_secrets.yaml",
    "deploy.env.pem", "laptop.ovpn", "putty.ppk", "my_secret.env",
])
def test_every_other_kind_of_protected_file_still_refuses_the_whole_folder(tmp_path, drive_d, two_volumes, name):
    proj = make_tree(tmp_path / "C_drive" / "Proj", {"a.txt": "a", name: "x"})
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "protected" in plan.refusal.lower(), (name, plan.refusal)
    assert "never" in plan.refusal.lower() or "won't" in plan.refusal.lower()


def test_a_protected_folder_inside_the_folder_still_refuses_it(tmp_path, drive_d, two_volumes):
    proj = make_tree(tmp_path / "C_drive" / "Proj", {"a.txt": "a", ".env/bin/python": "x"})
    plan = fm.make_plan(str(proj), str(drive_d))
    assert plan.refusal and "protected" in plan.refusal.lower(), plan.refusal


@pytest.mark.parametrize("where", [".ssh", ".aws", ".gnupg", "Desktop/credentials"])
def test_his_key_and_cloud_credential_folders_still_refuse_to_be_moved(drive_d, where):
    plan = fm.make_plan(str(Path.home() / where), str(drive_d))
    assert plan.refusal and "protected" in plan.refusal.lower(), plan.refusal


def test_jalens_own_folder_is_still_refused_even_with_an_env_file_in_it(tmp_path, drive_d, monkeypatch):
    repo = make_tree(tmp_path / "_jalen_repo", {"run.py": "x", ".env": "X=1"})
    monkeypatch.setattr(fm, "ROOT", repo)
    plan = fm.make_plan(str(repo), str(drive_d))
    assert plan.refusal and "jalen" in plan.refusal.lower(), plan.refusal


def test_the_exception_does_not_leak_into_any_other_tool(tmp_path, engine):
    """
    The carve-out lives inside the folder-move path only. Every other tool
    still refuses the very same files, and the list itself is unchanged.
    """
    from jalen.safety import Tier

    env = str(tmp_path / "Cafe" / ".env")
    token = str(tmp_path / "Cafe" / "config" / "token.json")
    for tool, args in (
        ("read_file", {"path": env}), ("read_file", {"path": token}),
        ("copy_file", {"path": env, "destination": str(tmp_path / "x")}),
        ("move_file", {"path": token, "destination": str(tmp_path / "x")}),
        ("move_file", {"path": str(tmp_path / "a.txt"), "destination": env}),
        ("open_target", {"name": env}),
        ("send_telegram_file", {"to": "Saved Messages", "path": env}),
        ("move_folder", {"path": env, "destination": "D:/"}),
    ):
        assert engine.classify(tool, args).tier is Tier.BLACK, (tool, args)


def test_the_never_touch_list_is_exactly_what_it_was(engine):
    expected_patterns = [
        "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", ".env", "*.kdbx", "*credential*", "*password*",
        "*.session", "*.session-journal", "*token*.json", "client_secret*", ".env*", "*.env", "vault.json",
        "*.ovpn", "*.ppk", "*secret*",
    ]
    assert list(engine._never_patterns) == expected_patterns
    for path in ("c:/windows", "c:/program files/windowsapps", "c:/programdata/microsoft/windows defender"):
        assert path in engine._never_paths
    assert engine.protected_path("C:/anything/.env") and engine.protected_path("C:/anything/config/token.json")


def test_planning_a_move_does_not_change_what_the_safety_engine_protects(project, drive_d, two_volumes):
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine

    before = SafetyEngine(CONFIG)
    patterns = list(before._never_patterns)
    fm.plan_folder_move(str(project), str(drive_d))
    after = SafetyEngine(CONFIG)
    assert list(after._never_patterns) == patterns
    assert after.protected_path(str(project / ".env"))
