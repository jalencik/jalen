"""
Filesystem tools (Phase C).

The never-touch list and path/pattern protections in config/safety.yaml are
enforced by SafetyEngine.classify() BEFORE any of these ever run, on every
path — router, agent hook, Telegram alike (handoff §7). These functions
don't re-implement that check; duplicating it here would risk the two
drifting apart, which is worse than trusting the one place it's actually
tested (tests/test_safety.py).

search_files is the one exception: it additionally skips never_touch
directories during its own walk, as defense in depth — a search shouldn't
even enumerate filenames inside a credentials folder, even though reading
one back out is already blocked at the gate.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..config import CONFIG

MAX_READ_BYTES = 2_000_000  # ~2MB — keep file reads sane for a spoken/LLM context
MAX_LIST_ENTRIES = 200
MAX_SEARCH_RESULTS = 30
MAX_SEARCH_SCANNED = 50_000  # bounds the fallback walk so it can't run forever


def _resolve(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path)))


def _never_touch_dirs() -> list[str]:
    never = (CONFIG.get("safety_tiers", {}) or {}).get("never_touch", {}) or {}
    return [str(Path(p)).replace("\\", "/").rstrip("/").lower() for p in never.get("paths", []) or []]


def _is_under_never_touch(path: Path, never_dirs: list[str]) -> bool:
    norm = str(path).replace("\\", "/").rstrip("/").lower()
    return any(norm == d or norm.startswith(d + "/") for d in never_dirs)


# ---------------------------------------------------------------------- read
def read_file(path: str) -> str:
    """Read a text file's contents — GREEN."""
    p = _resolve(path)
    # Checked explicitly, not caught as IsADirectoryError: Windows raises
    # PermissionError (ERROR_ACCESS_DENIED) for open() on a directory, not
    # EISDIR — IsADirectoryError never actually fires here on this platform.
    # Found by the test for this exact case, not assumed from POSIX habits.
    if p.is_dir():
        return f"{path} is a folder, not a file — try list_directory."
    try:
        size = p.stat().st_size
        if size > MAX_READ_BYTES:
            return f"{p.name} is {size / 1e6:.1f} MB — too large to read in full."
        return p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return f"No file at {path}."
    except Exception as exc:
        return f"Couldn't read {path}: {exc}"


def list_directory(path: str) -> str:
    """List a folder's contents — GREEN."""
    p = _resolve(path)
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except FileNotFoundError:
        return f"No folder at {path}."
    except NotADirectoryError:
        return f"{path} is a file, not a folder."
    except Exception as exc:
        return f"Couldn't list {path}: {exc}"
    if not entries:
        return "(empty folder)"
    lines = [f"{'[dir] ' if e.is_dir() else ''}{e.name}" for e in entries[:MAX_LIST_ENTRIES]]
    if len(entries) > MAX_LIST_ENTRIES:
        lines.append(f"... and {len(entries) - MAX_LIST_ENTRIES} more")
    return "\n".join(lines)


def search_files(query: str, root: str | None = None) -> str:
    """
    Find files by name. Uses Everything (es.exe) when configured and
    actually installed — instant, whole-drive. Falls back to a bounded walk
    of index.content_index_paths (or `root`, if given) otherwise — GREEN.
    """
    everything_cli = CONFIG.get_path("index.everything_cli", "")
    if CONFIG.get_path("index.use_everything", True) and everything_cli and Path(everything_cli).exists():
        try:
            result = subprocess.run(
                [everything_cli, "-n", str(MAX_SEARCH_RESULTS), query],
                capture_output=True, text=True, timeout=10,
            )
            hits = [line for line in result.stdout.splitlines() if line.strip()]
            if hits:
                return "\n".join(hits)
        except Exception:
            pass  # fall through to the walk-based search

    roots = [root] if root else (CONFIG.get_path("index.content_index_paths", []) or [str(Path.home())])
    exclude_dirs = {d.lower() for d in (CONFIG.get_path("index.exclude_dirs", []) or [])}
    never_dirs = _never_touch_dirs()
    query_low = query.lower()
    hits: list[str] = []
    scanned = 0
    for r in roots:
        base = _resolve(r)
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in exclude_dirs and not _is_under_never_touch(Path(dirpath) / d, never_dirs)
            ]
            for name in filenames:
                scanned += 1
                if scanned > MAX_SEARCH_SCANNED:
                    break
                if query_low in name.lower():
                    hits.append(str(Path(dirpath) / name))
                    if len(hits) >= MAX_SEARCH_RESULTS:
                        return "\n".join(hits)
            if scanned > MAX_SEARCH_SCANNED:
                break
    return "\n".join(hits) if hits else f"No files matching {query!r} found."


# --------------------------------------------------------------------- write
def create_file(path: str, content: str = "") -> str:
    """Create a new text file — AMBER."""
    p = _resolve(path)
    try:
        if p.exists():
            return f"{path} already exists — use edit_file to change it."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Created {p.name}."
    except Exception as exc:
        return f"Couldn't create {path}: {exc}"


def edit_file(path: str, content: str) -> str:
    """Overwrite a file's contents — AMBER."""
    p = _resolve(path)
    try:
        if not p.exists():
            return f"No file at {path} — use create_file first."
        p.write_text(content, encoding="utf-8")
        return f"Updated {p.name}."
    except Exception as exc:
        return f"Couldn't edit {path}: {exc}"


def create_folder(path: str) -> str:
    """Create a folder, and any missing parents — AMBER."""
    p = _resolve(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return f"Created folder {p.name}."
    except Exception as exc:
        return f"Couldn't create folder {path}: {exc}"


def copy_file(path: str, destination: str) -> str:
    """Copy a file — AMBER."""
    src, dst = _resolve(path), _resolve(destination)
    try:
        if dst.is_dir():
            dst = dst / src.name
        shutil.copy2(src, dst)
        return f"Copied {src.name} to {dst}."
    except Exception as exc:
        return f"Couldn't copy {path}: {exc}"


def move_file(path: str, destination: str) -> str:
    """Move a file — AMBER."""
    src, dst = _resolve(path), _resolve(destination)
    try:
        if dst.is_dir():
            dst = dst / src.name
        shutil.move(str(src), str(dst))
        return f"Moved {src.name} to {dst}."
    except Exception as exc:
        return f"Couldn't move {path}: {exc}"


def rename_file(path: str, new_name: str) -> str:
    """Rename a file in place — AMBER."""
    src = _resolve(path)
    try:
        dst = src.with_name(new_name)
        src.rename(dst)
        return f"Renamed to {new_name}."
    except Exception as exc:
        return f"Couldn't rename {path}: {exc}"


def delete_file(path: str) -> str:
    """
    Delete a file — RED. By the time this runs, the gate has already
    confirmed it out loud (or via Telegram) and gotten a yes; this just
    does the deletion.
    """
    p = _resolve(path)
    try:
        if p.is_dir():
            return f"{path} is a folder — delete_file only deletes files."
        p.unlink()
        return f"Deleted {p.name}."
    except FileNotFoundError:
        return f"No file at {path} — already gone."
    except Exception as exc:
        return f"Couldn't delete {path}: {exc}"


# --------------------------------------------------------------------- dispatch
REGISTRY: dict[str, Any] = {
    "read_file": read_file,
    "list_directory": list_directory,
    "search_files": search_files,
    "create_file": create_file,
    "edit_file": edit_file,
    "create_folder": create_folder,
    "copy_file": copy_file,
    "move_file": move_file,
    "rename_file": rename_file,
    "delete_file": delete_file,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
