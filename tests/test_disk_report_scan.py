"""
"What's filling my C drive" was never answered.

Live QA of 2026-10-01, three tries: "still scanning, ask again" twice, then a
scan of four user folders (Desktop, Documents, Downloads, Temp) with a 12 second
budget that explained 0.7 GB of the 139 GB in use. Text mode never calls
Jalen.prewarm(), so the first ask always met a cold cache, and the report could
never find the real cause anyway because it never looked outside those folders.

What it does now (sysinfo.DriveScan and disk_report):
  - one background walk from the drive root, not four folders, with the places
    that usually hold the space (caches, virtual disks, ProgramData, Temp) walked
    FIRST so an early answer already says something real;
  - a time budget long enough to finish (measured), and an entry cap, both said
    out loud when they cut it short;
  - the five biggest places, each named and sized, found by descending into a
    folder while one part of it holds a fifth or more of it;
  - the part of the drive the scan is not allowed to open (Windows itself, which
    config/safety.yaml lists as never-touch) reported as a size, not guessed;
  - a "still counting" answer that carries what is known so far and is never the
    last word: the scan keeps going and the next ask gets the finished one;
  - text and Telegram modes start the scan when they start.

Nothing here reads a file's contents or deletes anything; it only adds up sizes.
"""
from __future__ import annotations

import inspect
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from jalen.tools import sysinfo

G = 1_000_000_000
WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows thing")


def P(*parts: str) -> str:
    """A path on a pretend C: drive, built the way the scanner builds its keys."""
    return os.path.join("C:\\", *parts)


HOME = P("Users", "u")


def make(root: Path, files: dict[str, int]) -> Path:
    """Small real files (bytes written, never sparse) under `root`."""
    for rel, size in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    return root


def snap(state="done", counted=0, own=None, biggest=(), elapsed_s=200.0, denied=0,
         files=1000, dirs=100, error="", root="C:\\"):
    return sysinfo.Snapshot(state=state, root=root, counted=counted, files=files, dirs=dirs,
                            denied=denied, elapsed_s=elapsed_s, own=dict(own or {}),
                            biggest=list(biggest), error=error)


# ===========================================================================
# THE WALK
# ===========================================================================
def test_the_walk_adds_up_every_byte_under_the_root(tmp_path):
    root = make(tmp_path / "c", {"a.bin": 100, "x/b.bin": 2000, "x/y/c.bin": 30000, "z/d.bin": 400})
    scan = sysinfo.DriveScan(str(root))
    scan.run()
    got = scan.snapshot()
    assert got.state == "done"
    assert got.counted == 100 + 2000 + 30000 + 400
    assert got.files == 4
    assert sum(got.own.values()) == got.counted


def test_bytes_are_attributed_to_the_folder_they_sit_in(tmp_path):
    root = make(tmp_path / "c", {"x/b.bin": 2000, "x/y/c.bin": 30000})
    scan = sysinfo.DriveScan(str(root))
    scan.run()
    own = scan.snapshot().own
    assert own[str(root / "x")] == 2000
    assert own[str(root / "x" / "y")] == 30000


def test_a_very_deep_tree_is_folded_into_its_depth_cap_without_losing_a_byte(tmp_path):
    """The map of folders must not grow with the depth of the disk: 141,267
    folders were measured on this C: and one of them was 4,007 levels deep."""
    deep = "/".join(f"d{i}" for i in range(12))
    root = make(tmp_path / "c", {f"{deep}/f.bin": 1000, "d0/d1/d2/side.bin": 500})
    scan = sysinfo.DriveScan(str(root), max_depth=3)
    scan.run()
    got = scan.snapshot()
    assert got.counted == 1500
    assert sum(got.own.values()) == 1500
    for key in got.own:
        below = Path(key).relative_to(root).parts
        assert len(below) <= 3, key
    assert got.own[str(root / "d0" / "d1" / "d2")] == 1500


@WINDOWS_ONLY
def test_a_junction_is_not_walked_so_moved_folders_are_not_counted_twice(tmp_path):
    """After a folder is moved to D: there is a junction at the old place. What is
    behind it lives on the other drive and must not be added to this one."""
    import _winapi

    elsewhere = make(tmp_path / "d_drive" / "Downloads", {"big.bin": 40000})
    root = make(tmp_path / "c", {"mine.txt": 10})
    _winapi.CreateJunction(str(elsewhere), str(root / "Downloads"))
    scan = sysinfo.DriveScan(str(root))
    scan.run()
    assert scan.snapshot().counted == 10


def test_never_touch_folders_are_not_entered_and_not_counted(tmp_path):
    root = make(tmp_path / "c", {"keep/a.bin": 1000, "Windows/System32/big.bin": 50000})
    never = [str(root / "Windows").replace("\\", "/").lower()]
    scan = sysinfo.DriveScan(str(root), never_dirs=never)
    scan.run()
    got = scan.snapshot()
    assert got.counted == 1000
    assert not any("windows" in key.lower() for key in got.own)


def test_a_never_touch_folder_deep_in_the_tree_is_pruned_and_a_deep_chain_is_still_walked(tmp_path):
    """The never-touch check is only asked down to the depth of the deepest protected
    folder (it was string work on the whole path at every level, 18,000 characters at
    the bottom of the 4,007-level chain on this C:), so the depth it stops asking at
    must still be where the deepest protected folder is."""
    chain = "/".join(f"n{i}" for i in range(15))
    root = make(tmp_path / "c", {"a/b/vault/x.bin": 7000, "a/b/ok/y.bin": 100, f"{chain}/z.bin": 55})
    never = [str(root / "a" / "b" / "vault").replace("\\", "/").lower(), "d:/elsewhere"]
    scan = sysinfo.DriveScan(str(root), never_dirs=never)
    scan.run()
    assert scan.snapshot().counted == 100 + 55


def test_a_folder_with_an_absurdly_long_path_is_not_entered_and_is_counted_as_unread(tmp_path):
    """Measured: one chain of 4,000 empty folders on this C: took 104 s to list, each level
    costing more than the one above. No real program makes a path like that."""
    chain = "/".join(f"n{i}" for i in range(40))
    root = make(tmp_path / "c", {"a.bin": 100, f"{chain}/deep.bin": 9000})
    scan = sysinfo.DriveScan(str(root), max_path=len(str(root)) + 60)
    scan.run()
    got = scan.snapshot()
    assert got.state == "done"
    assert got.counted == 100, "what is below the limit must not be counted or walked"
    assert got.denied >= 1


def test_the_time_budget_stops_the_walk_and_says_so(tmp_path):
    root = make(tmp_path / "c", {f"d{i}/f.bin": 100 for i in range(30)})
    scan = sysinfo.DriveScan(str(root), budget_s=0.0)
    scan.run()
    got = scan.snapshot()
    assert got.state == "budget"
    assert got.counted < 3000
    assert scan.done.is_set()


def test_the_walk_is_at_normal_priority_while_someone_may_be_waiting_and_then_steps_aside(tmp_path, monkeypatch):
    """Measured: the whole walk at background priority crawled while other work used the
    disk (the quick tier took more than 75 s instead of about 11). So the first FULL_SPEED_S
    seconds are at normal priority, and only the long chore after that gives way."""
    root = make(tmp_path / "c", {f"d{i}/f.bin": 10 for i in range(5)})
    calls: list[bool] = []
    monkeypatch.setattr(sysinfo, "_enter_background_mode", lambda begin: calls.append(begin) or True)
    monkeypatch.setattr(sysinfo, "FULL_SPEED_S", 3600.0)
    sysinfo.DriveScan(str(root)).run()
    assert calls == []                      # a short walk never leaves normal priority
    monkeypatch.setattr(sysinfo, "FULL_SPEED_S", -1.0)
    sysinfo.DriveScan(str(root)).run()
    assert calls == [True, False]           # lowered once, and given back when the thread is done


def test_the_entry_cap_stops_the_walk_and_says_so(tmp_path):
    root = make(tmp_path / "c", {f"f{i}.bin": 10 for i in range(40)})
    scan = sysinfo.DriveScan(str(root), max_entries=5)
    scan.run()
    got = scan.snapshot()
    assert got.state == "entries"
    assert got.files <= 5


def test_a_root_that_is_not_there_fails_with_a_reason_instead_of_hanging(tmp_path):
    scan = sysinfo.DriveScan(str(tmp_path / "nope"))
    scan.run()
    got = scan.snapshot()
    assert got.state == "failed" and got.error
    assert scan.done.is_set() and scan.quick_done.is_set()


def test_known_big_places_are_walked_first_and_counted_once(tmp_path, monkeypatch):
    root = make(tmp_path / "c", {"cache/a.bin": 4000, "cache/hf/b.bin": 9000, "other/c.bin": 700})
    order: list[str] = []
    real = sysinfo.DriveScan._walk_from

    def spy(self, start):
        order.append(Path(start).name)
        return real(self, start)

    monkeypatch.setattr(sysinfo.DriveScan, "_walk_from", spy)
    # A place inside another place: the inner one must not be added twice.
    scan = sysinfo.DriveScan(str(root), first=[str(root / "cache"), str(root / "cache" / "hf")])
    scan.run()
    assert order[0] == "cache"
    assert scan.snapshot().counted == 4000 + 9000 + 700


def test_the_quick_signal_comes_after_the_known_places_and_before_the_rest(tmp_path, monkeypatch):
    root = make(tmp_path / "c", {"cache/a.bin": 4000, "other/c.bin": 700})
    seen: list[bool] = []
    real = sysinfo.DriveScan._walk_from

    def spy(self, start):
        if Path(start).name == "c":
            seen.append(self.quick_done.is_set())
        return real(self, start)

    monkeypatch.setattr(sysinfo.DriveScan, "_walk_from", spy)
    scan = sysinfo.DriveScan(str(root), first=[str(root / "cache")])
    scan.run()
    assert seen == [True], "the quick signal must be set before the long walk starts"


def test_without_known_places_the_quick_signal_waits_for_the_whole_walk(tmp_path, monkeypatch):
    """A drive with no known big places (D:) has no quick tier: a reader that waits for
    the quick signal must be waiting for the real end, not be let go at the start."""
    root = make(tmp_path / "c", {"a/b.bin": 100})
    seen: list[bool] = []
    real = sysinfo.DriveScan._walk_from

    def spy(self, start):
        seen.append(self.quick_done.is_set())
        return real(self, start)

    monkeypatch.setattr(sysinfo.DriveScan, "_walk_from", spy)
    scan = sysinfo.DriveScan(str(root))
    scan.run()
    assert seen == [False]
    assert scan.quick_done.is_set() and scan.done.is_set()


def test_the_biggest_files_are_kept_largest_first(tmp_path):
    root = make(tmp_path / "c", {"a.bin": 50000, "x/b.bin": 30000, "x/c.bin": 12000, "d.bin": 100})
    scan = sysinfo.DriveScan(str(root), big_file_min=10000)
    scan.run()
    sizes = [size for size, _ in scan.snapshot().biggest]
    assert sizes == [50000, 30000, 12000]


def test_a_snapshot_can_be_read_while_the_walk_is_running(tmp_path):
    root = make(tmp_path / "c", {f"d{i}/f{j}.bin": 50 for i in range(40) for j in range(5)})
    scan = sysinfo.DriveScan(str(root))
    scan.start()
    states = set()
    deadline = time.monotonic() + 10
    while not scan.done.is_set() and time.monotonic() < deadline:
        states.add(scan.snapshot().state)
    assert scan.done.wait(10)
    final = scan.snapshot()
    assert final.state == "done" and final.counted == 40 * 5 * 50
    assert states <= {"running", "done"}


def test_the_walk_never_reads_a_file_it_only_adds_sizes():
    source = inspect.getsource(sysinfo.DriveScan)
    for forbidden in ("open(", ".read", "unlink(", "remove(", "rmtree(", "rename("):
        assert forbidden not in source, forbidden


# ===========================================================================
# CHOOSING THE FIVE PLACES
# ===========================================================================
def _typical() -> dict[str, int]:
    return {
        os.path.join(HOME, "AppData", "Local", "Google", "Chrome"): 18 * G,
        os.path.join(HOME, "AppData", "Local", "Docker"): 15 * G,
        os.path.join(HOME, "AppData", "Local", "Temp"): 3 * G,
        os.path.join(HOME, "Documents"): 20 * G,
        os.path.join(HOME, ".cache", "huggingface"): 8 * G,
        P("Program Files"): 6 * G,
        P("ProgramData"): 5 * G,
        P(): G // 3,
    }


def test_the_five_biggest_places_are_the_specific_ones_not_their_parents():
    got = sysinfo.pick_places(_typical(), "C:\\", n=5)
    assert [Path(p.path).name for p in got] == ["Documents", "Chrome", "Docker", "huggingface", "Program Files"]
    assert [p.size for p in got] == [20 * G, 18 * G, 15 * G, 8 * G, 6 * G]


def test_the_users_folder_is_not_reported_as_the_answer_when_one_folder_inside_holds_it():
    got = sysinfo.pick_places(_typical(), "C:\\", n=5)
    assert all(Path(p.path).name not in ("Users", "u", "AppData", "Local") for p in got)


def test_a_folder_of_many_small_things_is_reported_whole_not_split_into_thirty_pieces():
    own = {os.path.join(HOME, "Downloads", f"item{i}"): G for i in range(30)}
    own[P("Program Files")] = 10 * G
    got = sysinfo.pick_places(own, "C:\\", n=5)
    assert Path(got[0].path).name == "Downloads" and got[0].size == 30 * G
    assert Path(got[1].path).name == "Program Files"


def test_a_big_file_sitting_loose_in_a_folder_with_subfolders_is_its_own_entry():
    docker = os.path.join(HOME, "AppData", "Local", "Docker")
    own = {docker: 10 * G, os.path.join(docker, "logs"): 5 * G, os.path.join(docker, "cfg"): 4 * G}
    got = sysinfo.pick_places(own, "C:\\", n=3)
    assert got[0].loose is True and got[0].size == 10 * G and got[0].path == docker


def test_fewer_than_five_places_returns_what_there_is_and_nothing_when_empty():
    assert len(sysinfo.pick_places({P("Games"): 3 * G, P("tmp"): G}, "C:\\", n=5)) == 2
    assert sysinfo.pick_places({}, "C:\\", n=5) == []


def test_the_part_of_the_drive_that_is_not_opened_competes_for_a_place():
    got = sysinfo.pick_places(_typical(), "C:\\", n=5, system_bytes=29 * G)
    assert got[0].path == sysinfo.SYSTEM_PATH and got[0].size == 29 * G
    assert len(got) == 5


def test_a_small_leftover_is_not_called_the_system():
    got = sysinfo.pick_places(_typical(), "C:\\", n=5, system_bytes=sysinfo.MIN_SYSTEM_REPORT_BYTES - 1)
    assert all(p.path != sysinfo.SYSTEM_PATH for p in got)


# ---------------------------------------------------------------- the names
@pytest.mark.parametrize("path, said", [
    (os.path.join(HOME, ".cache", "huggingface"), "HuggingFace"),
    (os.path.join(HOME, "AppData", "Local", "Temp"), "Temp"),
    (os.path.join(HOME, "AppData", "Local", "pip"), "pip"),
    (os.path.join(HOME, "AppData", "Local", "npm-cache"), "npm"),
    (os.path.join(HOME, "Documents"), "Documents"),
    (P("Program Files (x86)"), "Program Files (x86)"),
    (P("$RECYCLE.BIN"), "Recycle Bin"),
    (HOME, "your user folder"),
])
def test_places_are_named_the_way_he_would_say_them(path, said):
    label = sysinfo.place_label(sysinfo.Place(path, G), "C:\\", HOME, [])
    assert said.lower() in label.lower(), label


def test_a_store_apps_folder_is_named_without_its_publisher_id():
    """Found on the real C: (2026-10-01): "Claude_pzs8sxrjxfjjc in Windows Store app data"."""
    path = os.path.join(HOME, "AppData", "Local", "Packages", "Claude_pzs8sxrjxfjjc")
    label = sysinfo.place_label(sysinfo.Place(path, G), "C:\\", HOME, [])
    assert label == "Claude app data"


def test_an_unknown_folder_is_named_with_the_folder_it_is_in():
    label = sysinfo.place_label(sysinfo.Place(os.path.join(HOME, "Documents", "thesis"), G), "C:\\", HOME, [])
    assert "thesis" in label and "Documents" in label


def test_a_folder_that_is_one_big_file_is_named_by_the_file():
    data = os.path.join(HOME, "AppData", "Local", "Docker", "wsl", "data")
    label = sysinfo.place_label(sysinfo.Place(data, 15 * G), "C:\\", HOME,
                                [(15 * G, os.path.join(data, "ext4.vhdx"))])
    assert "ext4.vhdx" in label


def test_loose_files_are_named_as_such():
    label = sysinfo.place_label(sysinfo.Place(os.path.join(HOME, "Documents"), 9 * G, loose=True),
                                "C:\\", HOME, [])
    assert "files" in label.lower() and "Documents" in label


def test_the_system_entry_says_what_it_is_and_that_it_is_not_opened():
    label = sysinfo.place_label(sysinfo.Place(sysinfo.SYSTEM_PATH, 29 * G), "C:\\", HOME, [])
    assert "Windows" in label and "don't" in label


def test_a_protected_file_is_not_named(monkeypatch):
    class Engine:
        def protected_path(self, path):
            return "protected"

    monkeypatch.setattr(sysinfo, "_protected_engine", lambda: Engine())
    data = os.path.join(HOME, "secrets")
    label = sysinfo.place_label(sysinfo.Place(data, 5 * G), "C:\\", HOME,
                                [(5 * G, os.path.join(data, "passwords.kdbx"))])
    assert "passwords.kdbx" not in label


# ===========================================================================
# WHAT IS SAID
# ===========================================================================
def test_a_finished_scan_names_five_places_with_sizes_and_the_system_remainder():
    own = _typical()
    counted = sum(own.values())
    used = counted + 29 * G
    text = sysinfo.describe_scan(snap(counted=counted, own=own), used, HOME)
    assert "Documents" in text and "20.0 gigabytes" in text
    assert "Chrome" in text and "18.0 gigabytes" in text
    assert "Windows" in text and "29.0 gigabytes" in text
    assert "nothing gets deleted" in text
    assert "so far" not in text and "still" not in text


def test_a_finished_scan_that_explains_the_drive_does_not_invent_a_system_remainder():
    own = _typical()
    counted = sum(own.values())
    text = sysinfo.describe_scan(snap(counted=counted, own=own), counted + 100_000_000, HOME)
    assert "Windows" not in text


def test_a_scan_in_progress_says_what_it_has_and_that_it_is_not_final():
    own = {os.path.join(HOME, "Documents"): 20 * G, P("Program Files"): 6 * G}
    text = sysinfo.describe_scan(snap(state="running", counted=26 * G, own=own, elapsed_s=15), 100 * G, HOME)
    assert "so far" in text
    assert "26.0 gigabytes" in text and "100.0 gigabytes" in text
    assert "Documents" in text and "20.0 gigabytes" in text          # real numbers, not just "wait"
    assert "isn't final" in text or "not final" in text
    assert "ask again" in text


def test_a_scan_that_has_found_nothing_yet_still_says_what_is_happening():
    text = sysinfo.describe_scan(snap(state="running", counted=0, own={}, elapsed_s=2), 100 * G, HOME)
    assert "started" in text and "ask again" in text


def test_a_scan_that_hit_its_budget_says_it_stopped_and_how_much_it_covered():
    own = {os.path.join(HOME, "Documents"): 20 * G}
    text = sysinfo.describe_scan(snap(state="budget", counted=60 * G, own=own, elapsed_s=600), 100 * G, HOME)
    assert "stopped" in text and "60.0 gigabytes" in text and "100.0 gigabytes" in text
    assert "Windows" not in text, "a scan that did not finish cannot say what the remainder is"


def test_a_failed_scan_has_nothing_to_say_so_the_caller_can_fall_back():
    assert sysinfo.describe_scan(snap(state="failed", error="no such drive"), 100 * G, HOME) is None


def test_what_the_scan_may_not_open_is_named_as_a_whole_and_sized_by_subtraction():
    text = sysinfo.describe_scan(snap(counted=G, own={P("Games"): G}), 40 * G, HOME)
    assert "Windows and other system files" in text and "39.0 gigabytes" in text


# ===========================================================================
# THE ASK: disk_report
# ===========================================================================
class FakeScan:
    """Stands in for DriveScan: what it knows, when it is 'quick done' and 'done'."""

    made: list["FakeScan"] = []

    def __init__(self, root, snapshot):
        self.root = root
        self._snapshot = snapshot
        self.quick_done = threading.Event()
        self.done = threading.Event()
        self.started_at = time.monotonic()
        self.finished_at = None
        self.started = False
        FakeScan.made.append(self)
        if snapshot.state != "running":      # a scan that failed is over before it begins
            self.finish()

    def start(self):
        self.started = True

    def snapshot(self):
        return self._snapshot

    def finish(self, snapshot=None, age_s=0.0):
        """The end of the scan. `done` is set BEFORE `quick_done`, as in DriveScan, so a
        reader woken by the quick signal can tell the final transition from the early one."""
        if snapshot is not None:
            self._snapshot = snapshot
        self.finished_at = time.monotonic() - age_s
        self.done.set()
        self.quick_done.set()


@pytest.fixture
def fakes(monkeypatch):
    FakeScan.made = []
    monkeypatch.setattr(sysinfo, "_drive_usage", lambda: [
        {"label": "C drive", "total": 157 * G, "free": 8 * G, "percent": 95.0, "mountpoint": "C:\\"},
        {"label": "D drive", "total": 500 * G, "free": 300 * G, "percent": 40.0, "mountpoint": "D:\\"},
    ])
    monkeypatch.setenv("SystemDrive", "C:")
    state = {"snapshot": snap(state="running", counted=26 * G,
                              own={os.path.join(HOME, "Documents"): 20 * G, P("Program Files"): 6 * G},
                              elapsed_s=12)}

    def make(root):
        return FakeScan(root, state["snapshot"])

    monkeypatch.setattr(sysinfo, "_make_scan", make)
    # prewarm and refresh also start the cleanup report, which walks the REAL Temp
    # folder: left alone, that thread outlived the test and was still listing
    # folders when tests/test_folder_move.py counted scandir calls.
    monkeypatch.setattr(sysinfo, "_cleanup_uncached", lambda *a, **k: "no cleanup in tests")
    monkeypatch.setattr(sysinfo, "ASK_WAIT_S", 0.05)     # the tests that wait say so themselves
    monkeypatch.setattr(sysinfo, "PREWARM_DELAY_S", 0.0)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(HOME)))
    sysinfo._forget_scans()
    yield state
    # prewarm and refresh start threads that look the stubs up when THEY run. One that
    # woke after the monkeypatch was undone ran the real cleanup report, walking the
    # real Temp and pip cache while another test counted scandir calls. Wait for them.
    for thread in threading.enumerate():
        if thread.name.startswith("sysinfo-") and thread is not threading.current_thread():
            thread.join(10)
    sysinfo._forget_scans()


def _later(seconds, fn):
    timer = threading.Timer(seconds, fn)
    timer.daemon = True
    timer.start()
    return timer


def test_a_cold_ask_waits_for_the_quick_places_not_for_the_whole_drive(fakes, monkeypatch):
    """The first ask used to be answered 'still scanning, ask again'. Now it waits for
    the known big places (seconds) and answers with real numbers, saying it is partial."""
    monkeypatch.setattr(sysinfo, "ASK_WAIT_S", 5.0)
    started = time.monotonic()

    def quick():
        FakeScan.made[0].quick_done.set()

    _later(0.2, quick)
    text = sysinfo.disk_report()
    waited = time.monotonic() - started
    assert 0.15 <= waited < 3.0, waited
    assert "so far" in text and "Documents" in text and "20.0 gigabytes" in text
    assert "ask again" in text


def test_a_cold_ask_whose_scan_finishes_while_it_waits_gets_the_finished_answer(fakes, monkeypatch):
    monkeypatch.setattr(sysinfo, "ASK_WAIT_S", 5.0)
    own = _typical()
    done = snap(counted=sum(own.values()), own=own)

    def finish():
        FakeScan.made[0].finish(done)

    _later(0.2, finish)
    text = sysinfo.disk_report()
    assert "so far" not in text
    assert "Documents" in text and "nothing gets deleted" in text


def test_the_wait_is_bounded(fakes, monkeypatch):
    monkeypatch.setattr(sysinfo, "ASK_WAIT_S", 0.3)
    started = time.monotonic()
    text = sysinfo.disk_report()            # nothing ever signals
    assert time.monotonic() - started < 3.0
    assert "so far" in text


def test_every_answer_starts_with_the_free_space_he_may_have_asked_for(fakes):
    text = sysinfo.disk_report()
    assert "8.0 gigabytes free" in text and "157.0 gigabytes" in text and "95 percent" in text
    assert "Your C drive" in text and "D drive" in text


def test_asking_twice_starts_one_scan_not_two(fakes):
    sysinfo.disk_report()
    sysinfo.disk_report()
    sysinfo.disk_report()
    assert len(FakeScan.made) == 1 and FakeScan.made[0].started


def test_a_finished_scan_is_served_instantly_and_not_redone_while_it_is_fresh(fakes):
    own = _typical()
    sysinfo.disk_report()
    FakeScan.made[0].finish(snap(counted=sum(own.values()), own=own), age_s=30)
    started = time.monotonic()
    text = sysinfo.disk_report()
    assert time.monotonic() - started < 0.5
    assert len(FakeScan.made) == 1
    assert "Documents" in text and "measured" not in text      # 30 s old: not worth a note


def test_an_old_result_is_still_answered_at_once_with_its_age_and_a_new_scan_starts(fakes):
    own = _typical()
    sysinfo.disk_report()
    FakeScan.made[0].finish(snap(counted=sum(own.values()), own=own),
                            age_s=sysinfo.DEEP_SCAN_TTL_S + 120)
    started = time.monotonic()
    text = sysinfo.disk_report()
    assert time.monotonic() - started < 0.5
    assert "Documents" in text and "measured" in text and "ago" in text
    assert len(FakeScan.made) == 2 and FakeScan.made[1].started


def test_the_rescan_does_not_replace_the_answer_until_it_has_finished(fakes):
    own = _typical()
    sysinfo.disk_report()
    FakeScan.made[0].finish(snap(counted=sum(own.values()), own=own), age_s=sysinfo.DEEP_SCAN_TTL_S + 60)
    sysinfo.disk_report()                                   # starts scan 2, still running
    text = sysinfo.disk_report()                            # must not drop to "so far" figures
    assert "so far" not in text and len(FakeScan.made) == 2
    newer = {os.path.join(HOME, "Videos"): 40 * G}
    FakeScan.made[1].finish(snap(counted=40 * G, own=newer))
    assert "Videos" in sysinfo.disk_report()


def test_another_drive_can_be_asked_about_by_letter(fakes):
    text = sysinfo.disk_report(drive="d")
    assert FakeScan.made and FakeScan.made[0].root.upper().startswith("D:")
    assert "D drive" in text


def test_a_drive_that_is_not_there_is_said_plainly(fakes):
    text = sysinfo.disk_report(drive="Z")
    assert "Z drive" in text and "can't see" in text
    assert not FakeScan.made


def test_when_the_whole_drive_cannot_be_read_it_falls_back_to_the_user_folders_and_says_so(fakes, tmp_path, monkeypatch):
    fakes["snapshot"] = snap(state="failed", error="access denied")
    desk = make(tmp_path / "Desktop", {"thesis/draft.bin": 9000})
    monkeypatch.setattr(sysinfo, "_walk_roots", lambda: [desk])
    text = sysinfo.disk_report()
    assert "8.0 gigabytes free" in text
    assert "thesis" in text
    assert "Desktop, Documents, Downloads" in text and "only" in text


def test_prewarm_starts_the_system_drive_scan_without_waiting_for_it(fakes):
    started = time.monotonic()
    sysinfo.prewarm_system_scan()
    assert time.monotonic() - started < 1.0
    deadline = time.monotonic() + 3
    while not any(s.started for s in FakeScan.made) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert FakeScan.made and FakeScan.made[0].root.upper().startswith("C:")


def test_prewarm_leaves_the_drive_alone_while_the_models_load(fakes, monkeypatch):
    monkeypatch.setattr(sysinfo, "PREWARM_DELAY_S", 0.6)
    sysinfo.prewarm_system_scan()
    time.sleep(0.2)
    assert not FakeScan.made, "the walk must not start beside the speech and brain warmups"
    deadline = time.monotonic() + 5
    while not FakeScan.made and time.monotonic() < deadline:
        time.sleep(0.02)
    assert FakeScan.made


def test_an_ask_during_the_prewarm_delay_starts_the_scan_itself_and_prewarm_does_not_start_another(fakes, monkeypatch):
    monkeypatch.setattr(sysinfo, "PREWARM_DELAY_S", 0.4)
    sysinfo.prewarm_system_scan()
    sysinfo.disk_report()                       # asked at once: starts the one scan
    time.sleep(0.8)                             # the prewarm wakes up and finds it already there
    assert len(FakeScan.made) == 1


def test_refreshing_starts_a_new_scan_and_says_it_takes_minutes(fakes):
    sysinfo.disk_report()
    FakeScan.made[0].finish(snap(counted=G, own={P("Games"): G}))
    said = sysinfo.refresh_system_scan()
    deadline = time.monotonic() + 3
    while len(FakeScan.made) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(FakeScan.made) == 2
    assert "minutes" in said


# ===========================================================================
# THE SUITE MUST NEVER WALK THE REAL DRIVE
# ===========================================================================
def test_the_suite_points_the_scan_at_a_scratch_folder_not_the_real_drive():
    """tests/test_sysinfo_tools.py calls disk_report() against 'the real machine'. With a
    whole-drive walk behind it that would be a 216 second walk of C: in every test run."""
    target = Path(sysinfo._scan_target("C:\\")).resolve()
    assert target != Path("C:\\") and "pytest" in str(target).lower()


def test_the_real_report_still_answers_in_the_suite_and_quickly():
    started = time.monotonic()
    text = sysinfo.disk_report()
    assert time.monotonic() - started < 20
    assert "free" in text.lower() and "percent" in text.lower()


# ===========================================================================
# TEXT AND TELEGRAM MODES START THE SCAN
# ===========================================================================
@pytest.mark.parametrize("mode, warms", [("text", True), ("telegram", True), ("voice", False)])
def test_text_and_telegram_modes_warm_the_scan_that_voice_mode_warms_in_prewarm(monkeypatch, mode, warms):
    """Only Jalen.run() calls prewarm(), so a typed session met a cold cache on the
    first 'what's filling my C drive'. Voice mode already warms it there."""
    import run

    calls = []
    monkeypatch.setattr(sysinfo, "prewarm_system_scan", lambda: calls.append(1))
    run._warm_what_the_first_ask_needs(mode)
    assert bool(calls) is warms


def test_voice_mode_still_warms_the_scan_in_prewarm():
    source = (Path(__file__).resolve().parent.parent / "jalen" / "app.py").read_text(encoding="utf-8")
    assert "prewarm_system_scan" in source
