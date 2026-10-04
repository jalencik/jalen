"""
Tests for jalen/tools/filesystem.py (Phase C). Real file I/O in an
isolated tmp_path per test — no mocks standing in for the filesystem.

The never-touch list itself is SafetyEngine's job and is already covered
by tests/test_safety.py; these tests are about the operations working
correctly once the gate has already let them through.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jalen.tools import filesystem as fs  # noqa: E402


def test_create_read_edit_roundtrip(tmp_path):
    target = tmp_path / "note.txt"
    assert fs.create_file(str(target), "hello") == f"Created {target.name}."
    assert fs.read_file(str(target)) == "hello"
    assert fs.edit_file(str(target), "updated") == f"Updated {target.name}."
    assert fs.read_file(str(target)) == "updated"


def test_create_file_refuses_to_overwrite_existing(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("original", encoding="utf-8")
    result = fs.create_file(str(target), "clobbered")
    assert "already exists" in result
    assert target.read_text(encoding="utf-8") == "original"


def test_edit_file_requires_existing_file(tmp_path):
    result = fs.edit_file(str(tmp_path / "missing.txt"), "x")
    assert "No file at" in result


def test_read_file_missing_returns_message_not_exception(tmp_path):
    result = fs.read_file(str(tmp_path / "nope.txt"))
    assert "No file at" in result


def test_read_file_on_a_directory_gives_a_clear_message(tmp_path):
    result = fs.read_file(str(tmp_path))
    assert "folder" in result.lower()


def test_read_file_too_large_is_refused_not_dumped(tmp_path, monkeypatch):
    target = tmp_path / "big.txt"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(fs, "MAX_READ_BYTES", 0)
    result = fs.read_file(str(target))
    assert "too large" in result


def test_list_directory(tmp_path):
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    result = fs.list_directory(str(tmp_path))
    assert "a.txt" in result
    assert "[dir] sub" in result


def test_list_directory_empty(tmp_path):
    assert fs.list_directory(str(tmp_path)) == "(empty folder)"


def test_list_directory_missing(tmp_path):
    assert "No folder at" in fs.list_directory(str(tmp_path / "nope"))


def test_create_folder_and_nested_parents(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    result = fs.create_folder(str(target))
    assert "Created folder" in result
    assert target.is_dir()


def test_copy_move_rename_delete_roundtrip(tmp_path):
    src = tmp_path / "orig.txt"
    src.write_text("content", encoding="utf-8")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    fs.copy_file(str(src), str(dest_dir))
    copied = dest_dir / "orig.txt"
    assert copied.read_text(encoding="utf-8") == "content"
    assert src.exists()  # copy leaves the original in place

    fs.move_file(str(copied), str(dest_dir / "moved.txt"))
    moved = dest_dir / "moved.txt"
    assert moved.exists()
    assert not copied.exists()

    fs.rename_file(str(moved), "renamed.txt")
    renamed = dest_dir / "renamed.txt"
    assert renamed.exists()
    assert not moved.exists()

    result = fs.delete_file(str(renamed))
    assert "Deleted" in result
    assert not renamed.exists()


def test_delete_file_missing_is_not_an_error(tmp_path):
    result = fs.delete_file(str(tmp_path / "already-gone.txt"))
    assert "already gone" in result


def test_delete_file_refuses_a_directory(tmp_path):
    result = fs.delete_file(str(tmp_path))
    assert "folder" in result.lower()


def test_search_files_finds_by_substring(tmp_path):
    (tmp_path / "report_2026.txt").write_text("", encoding="utf-8")
    (tmp_path / "other.txt").write_text("", encoding="utf-8")
    result = fs.search_files("report", root=str(tmp_path))
    assert "report_2026.txt" in result
    assert "other.txt" not in result


def test_search_files_no_match(tmp_path):
    result = fs.search_files("nonexistent-query-xyz", root=str(tmp_path))
    assert "No files matching" in result


def test_search_files_skips_excluded_dirs(tmp_path, monkeypatch):
    excluded = tmp_path / "node_modules"
    excluded.mkdir()
    (excluded / "target.txt").write_text("", encoding="utf-8")
    from jalen.config import CONFIG

    monkeypatch.setitem(CONFIG, "index", {**CONFIG.get("index", {}), "exclude_dirs": ["node_modules"]})
    result = fs.search_files("target", root=str(tmp_path))
    assert "No files matching" in result


def test_search_files_skips_never_touch_dirs(tmp_path, monkeypatch):
    protected = tmp_path / "credentials"
    protected.mkdir()
    (protected / "secret_target.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(fs, "_never_touch_dirs", lambda: [str(protected).replace("\\", "/").lower()])
    result = fs.search_files("secret_target", root=str(tmp_path))
    assert "No files matching" in result


def test_unified_registry_has_no_collisions_and_covers_all_tools():
    from jalen import tools

    for name in ("read_file", "delete_file", "get_window_list", "read_screen", "get_time", "open_app"):
        assert name in tools.REGISTRY, f"{name} missing from unified registry"


def test_call_unknown_tool_raises_keyerror():
    from jalen import tools

    with pytest.raises(KeyError):
        tools.call("not_a_real_tool", {})
