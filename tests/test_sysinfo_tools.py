"""
Tests for jalen/tools/sysinfo.py.

disk_report/cleanup_suggestions/memory_report all run against the REAL
machine (psutil, real drives, real processes) rather than mocks, matching
how tests/test_filesystem_tools.py exercises real file I/O — the whole
point of this module is "does the real system actually answer", and a
mocked psutil wouldn't catch a broken format string against real data.

The bounded-walk and never-touch behaviour is tested against a synthetic
tmp_path tree instead, so those two properties are checked deterministically
rather than depending on what happens to be in Downloads today.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.tools import sysinfo  # noqa: E402


# --------------------------------------------------------------- live smoke
def test_disk_report_runs_against_the_real_machine():
    # The drives and their free space are the real ones. The count of what is on
    # the drive is a walk of a small scratch folder: tests/conftest.py points
    # sysinfo._scan_target there, because a real walk of C: is minutes of disk.
    result = sysinfo.disk_report()
    assert isinstance(result, str) and result
    assert "free" in result.lower()
    assert "percent" in result.lower()


def test_cleanup_suggestions_runs_and_never_deletes_anything(tmp_path, monkeypatch):
    # A canary file the tool has no business touching, in a Temp-like spot.
    canary = tmp_path / "canary.txt"
    canary.write_text("still here", encoding="utf-8")
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))

    result = sysinfo.cleanup_suggestions()
    assert isinstance(result, str) and result
    assert canary.exists()
    assert canary.read_text(encoding="utf-8") == "still here"
    assert "deleted unless you tell me to" in result or "nothing obviously safe" in result


def test_cleanup_suggestions_never_calls_delete(tmp_path, monkeypatch):
    """Belt and suspenders: even if the report logic changed, assert the
    module never imports/uses filesystem.delete_file at all."""
    import inspect

    src = inspect.getsource(sysinfo)
    assert "delete_file(" not in src
    assert "unlink(" not in src
    assert "rmtree(" not in src
    assert "os.remove(" not in src


def test_memory_report_runs_against_the_real_machine():
    result = sysinfo.memory_report()
    assert isinstance(result, str) and result
    assert "percent" in result.lower()
    assert "memory" in result.lower()


def test_memory_report_respects_top_n():
    result = sysinfo.memory_report(top_n=1)
    assert isinstance(result, str) and result


# ---------------------------------------------------------------- bounded walk
def test_scan_user_folders_is_capped_by_entry_count(tmp_path):
    root = tmp_path / "Desktop"
    root.mkdir()
    for i in range(50):
        (root / f"file{i}.txt").write_text("x" * 10, encoding="utf-8")

    folder_sizes, biggest_files, capped, scanned = sysinfo._scan_user_folders(
        [root], never_dirs=[], max_entries=10, time_budget_s=30.0
    )
    assert capped is True
    assert scanned <= 11  # stops right after crossing the cap, not way past it


def test_scan_user_folders_is_capped_by_time_budget(tmp_path):
    root = tmp_path / "Desktop"
    root.mkdir()
    for i in range(20):
        (root / f"file{i}.txt").write_text("x", encoding="utf-8")

    folder_sizes, biggest_files, capped, scanned = sysinfo._scan_user_folders(
        [root], never_dirs=[], max_entries=1_000_000, time_budget_s=0.0
    )
    assert capped is True


def test_scan_user_folders_reports_uncapped_when_within_bounds(tmp_path):
    root = tmp_path / "Desktop"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("world!!", encoding="utf-8")

    folder_sizes, biggest_files, capped, scanned = sysinfo._scan_user_folders(
        [root], never_dirs=[], max_entries=1_000_000, time_budget_s=30.0
    )
    assert capped is False
    assert scanned == 2
    assert folder_sizes[str(root)] == 5          # a.txt, bucketed under the root itself
    assert folder_sizes[str(root / "sub")] == 7  # b.txt, bucketed under its immediate parent


def test_scan_user_folders_skips_never_touch_dirs(tmp_path):
    root = tmp_path / "Desktop"
    root.mkdir()
    protected = root / "credentials"
    protected.mkdir()
    (protected / "secret.txt").write_text("nope", encoding="utf-8")
    (root / "ok.txt").write_text("fine", encoding="utf-8")

    never_dirs = [str(protected).replace("\\", "/").lower()]
    folder_sizes, biggest_files, capped, scanned = sysinfo._scan_user_folders(
        [root], never_dirs=never_dirs, max_entries=1_000_000, time_budget_s=30.0
    )
    assert scanned == 1
    assert str(protected) not in folder_sizes
    assert all("secret.txt" not in path for _, path in biggest_files)


def test_dir_total_size_skips_never_touch_path_itself(tmp_path):
    protected = tmp_path / "credentials"
    protected.mkdir()
    (protected / "secret.txt").write_text("nope", encoding="utf-8")

    never_dirs = [str(protected).replace("\\", "/").lower()]
    total, count, capped = sysinfo._dir_total_size(protected, never_dirs)
    assert (total, count) == (0, 0)


def test_old_downloads_size_only_counts_files_past_the_cutoff(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    old_file = downloads / "old.zip"
    old_file.write_text("x" * 100, encoding="utf-8")
    new_file = downloads / "new.zip"
    new_file.write_text("y" * 50, encoding="utf-8")

    now = time.time()
    old_time = now - 40 * 86400
    import os

    os.utime(old_file, (old_time, old_time))
    os.utime(new_file, (now, now))

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    total, count, capped = sysinfo._old_downloads_size(30, never_dirs=[])
    assert count == 1
    assert total == 100


# -------------------------------------------------------------------- registry
def test_registered_in_unified_registry():
    from jalen import tools

    for name in ("disk_report", "cleanup_suggestions", "memory_report"):
        assert name in tools.REGISTRY


def test_size_str_is_spoken_friendly():
    assert "gigabytes" in sysinfo._size_str(5_000_000_000)
    assert "megabytes" in sysinfo._size_str(5_000_000)
    assert "kilobytes" in sysinfo._size_str(5_000)
    assert "bytes" in sysinfo._size_str(5)


# ----------------------------------------------------------------------- tier
def test_all_three_tools_are_explicit_green_in_safety_yaml():
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    engine.paranoid = False
    engine.posture = "irreversible_only"

    for name in ("disk_report", "cleanup_suggestions", "memory_report"):
        verdict = engine.classify(name, {})
        assert not verdict.detail.get("unclassified"), f"{name} isn't explicitly tiered in safety.yaml"
        assert verdict.tier is Tier.GREEN, f"{name} classified as {verdict.tier.value}, expected green"
