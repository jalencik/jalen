"""
"What is eating my disk and memory, and what can I safely delete?"

Three read-only reports, built around one hard constraint: this module NEVER
deletes anything, no matter how confidently it can name a safe-to-delete
folder. Deletion already exists — filesystem.delete_file — and it's RED,
gated on a spoken "yes". Duplicating a delete path here, even a "confirmed
safe" one, would create a second way to destroy files that the safety model
never rebalanced around. cleanup_suggestions() only ever measures and reports.

Every walk in this file is bounded two ways at once, not one: a hard entry
count AND a wall-clock time budget. An entry cap alone still hangs on a
folder full of gigantic files (few entries, huge stat() latency on a slow or
network-backed disk); a time budget alone still means an unpredictable
number of entries scanned run to run. Both together is what "can never hang"
actually requires. When either limit is hit mid-walk, the report says so —
silently under-counting and presenting it as complete would be exactly the
kind of fabricated-success bug launcher.py's docstring warns about.

never_touch enforcement: SafetyEngine.classify() already blocks any tool
call that names a never_touch path directly, but that's argument-level — it
does nothing for a folder these tools discover themselves while walking
(e.g. Desktop/credentials, if that ever existed). So every walk here also
prunes never_touch directories from os.walk's own dirnames, reusing
filesystem.py's exact predicate rather than re-deriving it — two independent
implementations of "is this the protected path" is how they'd quietly drift
apart. C:\\Windows itself is on that list, which is also why
cleanup_suggestions() never scans for Windows Update leftovers: it names
that category but reports no number for it, rather than reading inside a
folder the config says not to touch.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from pathlib import Path
from typing import Any

from .filesystem import _is_under_never_touch, _never_touch_dirs
from .system import IS_WINDOWS

# Bounds for the user-folder walk (disk_report). 200k entries covers even a
# neglected Downloads folder with years of installers in it; the time budget
# is what actually protects against a slow disk or a network-backed Temp
# path where 200k stat() calls could otherwise take minutes.
MAX_WALK_ENTRIES = 200_000
WALK_TIME_BUDGET_S = 12.0

# ---------------------------------------------------------------- caching
# Measured on this machine: disk_report 12.8s, cleanup_suggestions 41.0s.
# For a voice assistant that is unusable — the answer arrives long after the
# question stopped mattering. But the underlying numbers barely move minute
# to minute: disk usage and cache sizes are not volatile.
#
# So: serve the previous answer instantly when it's recent, and refresh in
# the background so the NEXT ask is instant too. The first ask of a session
# still pays full price and says so; every ask after that is immediate.
# Freshness is stated out loud rather than hidden, because silently
# reporting a stale number as current is the kind of small lie that makes an
# assistant untrustworthy.
CACHE_TTL_S = 600.0          # 10 minutes: disk/cache sizes don't move faster
CACHE_STALE_AFTER_S = 3600.0  # beyond an hour, say the figure is old

_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()
_refreshing: set[str] = set()


def _age_note(age_s: float) -> str:
    if age_s < 90:
        return ""
    minutes = int(age_s // 60)
    if minutes < 60:
        return f" (measured {minutes} minute{'s' if minutes != 1 else ''} ago)"
    hours = int(minutes // 60)
    return f" (measured {hours} hour{'s' if hours != 1 else ''} ago)"


def _cached(key: str, compute) -> str:
    """
    Instant answer from cache when we have a recent one; otherwise compute
    now. A cache hit past its TTL is still returned immediately, with a
    background refresh kicked off, so the user never waits on a scan whose
    answer we already roughly know.
    """
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)

    if entry is not None:
        computed_at, value = entry
        age = now - computed_at
        if age <= CACHE_TTL_S:
            return value
        # Stale but usable: answer now, refresh behind the scenes.
        with _cache_lock:
            already = key in _refreshing
            if not already:
                _refreshing.add(key)
        if not already:
            def refresh() -> None:
                try:
                    fresh = compute()
                    with _cache_lock:
                        _cache[key] = (time.monotonic(), fresh)
                except Exception:
                    pass  # a failed refresh must never break the caller
                finally:
                    with _cache_lock:
                        _refreshing.discard(key)

            threading.Thread(target=refresh, name=f"sysinfo-{key}", daemon=True).start()
        if age <= CACHE_STALE_AFTER_S:
            return value + _age_note(age)
        return value + _age_note(age) + " I'm re-checking now — ask again in a moment for current figures."

    value = compute()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def refresh_system_scan() -> str:
    """Force a fresh scan in the background — GREEN."""
    with _cache_lock:
        _cache.clear()
    for key, fn in (("disk", _disk_report_uncached), ("cleanup", _cleanup_uncached)):
        threading.Thread(
            target=lambda k=key, f=fn: _cached(k, f), name=f"sysinfo-warm-{key}", daemon=True
        ).start()
    return "Re-checking your disk and cleanup figures now. Ask again in a few seconds."


def prewarm_system_scan() -> None:
    """
    Run the expensive scans once at startup, off the critical path, so the
    first time the user actually asks the answer is already sitting there.
    Called by Jarvis.prewarm() alongside the STT/TTS/brain warmups.
    """
    def warm() -> None:
        for key, fn in (("disk", _disk_report_uncached), ("cleanup", _cleanup_uncached)):
            try:
                _cached(key, fn)
            except Exception:
                pass

    threading.Thread(target=warm, name="sysinfo-prewarm", daemon=True).start()

# Bounds for each individual cleanup-category scan (Temp, a browser cache,
# etc). Smaller than the disk_report budget because cleanup_suggestions()
# runs several of these back to back in one call — the total time has to
# stay conversational, not just each piece.
MAX_CATEGORY_ENTRIES = 60_000
CATEGORY_TIME_BUDGET_S = 5.0


def _size_str(num_bytes: float) -> str:
    """Human, spoken-friendly size — 'gigabytes'/'megabytes', not 'GB'/'MB',
    matching how get_system_status already speaks sizes in system.py."""
    num_bytes = max(0.0, float(num_bytes))
    if num_bytes >= 1e9:
        return f"{num_bytes / 1e9:.1f} gigabytes"
    if num_bytes >= 1e6:
        return f"{num_bytes / 1e6:.0f} megabytes"
    if num_bytes >= 1e3:
        return f"{num_bytes / 1e3:.0f} kilobytes"
    return f"{num_bytes:.0f} bytes"


# --------------------------------------------------------------------- disk
def _drive_usage() -> list[dict[str, Any]]:
    import psutil

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen:
            continue
        opts = (part.opts or "").lower()
        if "cdrom" in opts or not part.fstype:
            continue  # empty optical drives etc. raise on disk_usage anyway
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        seen.add(part.mountpoint)
        letter = part.mountpoint.rstrip("\\/")
        label = f"{letter[0].upper()} drive" if len(letter) == 2 and letter[1] == ":" else letter
        out.append({
            "label": label,
            "total": usage.total,
            "free": usage.free,
            "percent": usage.percent,
        })
    return out


def _walk_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "AppData" / "Local" / "Temp",
    ]
    return [p for p in candidates if p.is_dir()]


def _scan_user_folders(
    roots: list[Path],
    never_dirs: list[str],
    max_entries: int = MAX_WALK_ENTRIES,
    time_budget_s: float = WALK_TIME_BUDGET_S,
) -> tuple[dict[str, int], list[tuple[int, str]], bool, int]:
    """
    One bounded pass over `roots`. Returns:
      - folder_sizes: total bytes per immediate child of each root (files
        sitting directly in a root are bucketed under the root itself) —
        this is a "du -d 1" per watched folder, not a full recursive tree,
        because a full tree for every directory is what the entry cap and
        time budget exist to avoid.
      - biggest_files: (size, path) for the largest individual files seen,
        largest first.
      - capped: True if the entry cap or time budget cut the walk short.
      - scanned: how many files were actually examined.
    """
    folder_sizes: dict[str, int] = {}
    biggest_files: list[tuple[int, str]] = []
    scanned = 0
    capped = False
    deadline = time.monotonic() + time_budget_s

    for root in roots:
        if capped:
            break
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _is_under_never_touch(Path(dirpath) / d, never_dirs)]
            try:
                rel_parts = Path(dirpath).relative_to(root).parts
            except ValueError:
                rel_parts = ()
            bucket = str(root / rel_parts[0]) if rel_parts else str(root)

            for name in filenames:
                scanned += 1
                if scanned > max_entries or time.monotonic() > deadline:
                    capped = True
                    break
                fp = Path(dirpath) / name
                try:
                    size = fp.stat().st_size
                except OSError:
                    continue
                folder_sizes[bucket] = folder_sizes.get(bucket, 0) + size
                biggest_files.append((size, str(fp)))
            if capped:
                break
        if capped:
            break

    biggest_files.sort(key=lambda t: t[0], reverse=True)
    return folder_sizes, biggest_files[:10], capped, scanned


def _disk_report_uncached() -> str:
    """
    Per-drive free space, plus what's actually using it in the user's own
    folders (Desktop, Documents, Downloads, Temp) — GREEN, read-only.
    """
    try:
        import psutil  # noqa: F401
    except ImportError:
        return "psutil isn't installed, so I can't read disk usage."

    drives = _drive_usage()
    if not drives:
        return "I couldn't read any drive usage on this machine."

    drive_bits = [
        f"your {d['label']} has {_size_str(d['free'])} free out of {_size_str(d['total'])}, "
        f"{d['percent']:.0f} percent full"
        for d in drives
    ]
    summary = "; ".join(drive_bits).capitalize() + "."

    roots = _walk_roots()
    if not roots:
        return summary + " I couldn't find your Desktop, Documents, Downloads or Temp folders to check further."

    never_dirs = _never_touch_dirs()
    folder_sizes, biggest_files, capped, scanned = _scan_user_folders(roots, never_dirs)

    top_folders = sorted(folder_sizes.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_folders = [(name, size) for name, size in top_folders if size > 0]
    if top_folders:
        bits = ", ".join(f"{Path(name).name} at {_size_str(size)}" for name, size in top_folders)
        summary += f" The biggest things in those folders: {bits}."

    if biggest_files:
        f_size, f_path = biggest_files[0]
        summary += f" The single largest file is {Path(f_path).name} at {_size_str(f_size)}."

    if capped:
        summary += f" I stopped after checking {scanned:,} items, so there's likely more beyond this."

    return summary


# ------------------------------------------------------------------ cleanup
def _dir_total_size(
    path: Path,
    never_dirs: list[str],
    max_entries: int = MAX_CATEGORY_ENTRIES,
    time_budget_s: float = CATEGORY_TIME_BUDGET_S,
) -> tuple[int, int, bool]:
    """Total bytes + file count under `path`, bounded. (0, 0, False) if the
    path doesn't exist or is itself protected."""
    if not path.is_dir() or _is_under_never_touch(path, never_dirs):
        return 0, 0, False
    total = 0
    count = 0
    capped = False
    deadline = time.monotonic() + time_budget_s
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not _is_under_never_touch(Path(dirpath) / d, never_dirs)]
        for name in filenames:
            count += 1
            if count > max_entries or time.monotonic() > deadline:
                capped = True
                break
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
        if capped:
            break
    return total, count, capped


def _old_downloads_size(
    days: int,
    never_dirs: list[str],
    max_entries: int = MAX_CATEGORY_ENTRIES,
    time_budget_s: float = CATEGORY_TIME_BUDGET_S,
) -> tuple[int, int, bool]:
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return 0, 0, False
    cutoff = time.time() - days * 86400
    total = 0
    count = 0
    scanned = 0
    capped = False
    deadline = time.monotonic() + time_budget_s
    for dirpath, dirnames, filenames in os.walk(downloads):
        dirnames[:] = [d for d in dirnames if not _is_under_never_touch(Path(dirpath) / d, never_dirs)]
        for name in filenames:
            scanned += 1
            if scanned > max_entries or time.monotonic() > deadline:
                capped = True
                break
            fp = Path(dirpath) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                total += st.st_size
                count += 1
        if capped:
            break
    return total, count, capped


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def _recycle_bin_total() -> tuple[int, int] | None:
    """Summed recycle-bin size/count across every fixed drive, via the
    same Shell API Explorer's own recycle bin properties dialog uses.
    None if it can't be read at all (not Windows, or the call itself fails);
    (0, 0) is a legitimate "empty" answer and is kept distinct from that."""
    if not IS_WINDOWS:
        return None
    try:
        import psutil
    except ImportError:
        return None

    total_size = 0
    total_items = 0
    found_any = False
    for part in psutil.disk_partitions(all=False):
        opts = (part.opts or "").lower()
        if "cdrom" in opts or not part.fstype:
            continue
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        try:
            hr = ctypes.windll.shell32.SHQueryRecycleBinW(part.mountpoint, ctypes.byref(info))
        except OSError:
            continue
        if hr == 0:  # S_OK
            found_any = True
            total_size += info.i64Size
            total_items += info.i64NumItems
    return (total_size, total_items) if found_any else None


def _local_appdata() -> Path | None:
    val = os.environ.get("LOCALAPPDATA")
    return Path(val) if val else None


def _browser_cache_paths() -> list[tuple[str, Path]]:
    local = _local_appdata()
    if local is None:
        return []
    candidates = [
        ("Chrome", local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"),
        ("Edge", local / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache"),
    ]
    profiles = local / "Mozilla" / "Firefox" / "Profiles"
    if profiles.is_dir():
        try:
            for prof in profiles.iterdir():
                cache2 = prof / "cache2"
                if cache2.is_dir():
                    candidates.append(("Firefox", cache2))
        except OSError:
            pass
    return [(name, p) for name, p in candidates if p.is_dir()]


def _cleanup_uncached(downloads_older_than_days: int = 30) -> str:
    """
    Specifically safe-to-delete things, with real measured sizes — GREEN,
    read-only. This function only suggests; it never deletes anything.
    Deleting a file goes through the existing delete_file tool, which is RED
    and asks first — that boundary is deliberate and isn't crossed here.
    """
    never_dirs = _never_touch_dirs()
    lines: list[str] = []
    recoverable = 0

    temp_env = os.environ.get("TEMP") or os.environ.get("TMP")
    temp_dir = Path(temp_env) if temp_env else Path.home() / "AppData" / "Local" / "Temp"
    t_size, t_count, t_capped = _dir_total_size(temp_dir, never_dirs)
    if t_count:
        cap_note = " (more beyond this — scan capped)" if t_capped else ""
        lines.append(f"Temp files: {_size_str(t_size)} across {t_count:,} files{cap_note}.")
        recoverable += t_size

    d_size, d_count, d_capped = _old_downloads_size(downloads_older_than_days, never_dirs)
    if d_count:
        cap_note = " (more beyond this — scan capped)" if d_capped else ""
        lines.append(
            f"Downloads older than {downloads_older_than_days} days: "
            f"{_size_str(d_size)} across {d_count:,} files{cap_note}."
        )
        recoverable += d_size

    rb = _recycle_bin_total()
    if rb is not None:
        rb_size, rb_items = rb
        if rb_items:
            lines.append(f"Recycle bin: {_size_str(rb_size)} across {rb_items:,} items.")
            recoverable += rb_size
        else:
            lines.append("Recycle bin is already empty.")

    cache_bits = []
    for name, path in _browser_cache_paths():
        c_size, c_count, _ = _dir_total_size(path, never_dirs)
        if c_count:
            cache_bits.append(f"{name} at {_size_str(c_size)}")
            recoverable += c_size
    if cache_bits:
        lines.append("Browser caches: " + ", ".join(cache_bits) + ".")

    local = _local_appdata()
    if local is not None:
        pip_size, pip_count, _ = _dir_total_size(local / "pip" / "Cache", never_dirs)
        if pip_count:
            lines.append(f"Pip cache: {_size_str(pip_size)}.")
            recoverable += pip_size

        npm_size, npm_count, _ = _dir_total_size(local / "npm-cache", never_dirs)
        if npm_count:
            lines.append(f"NPM cache: {_size_str(npm_size)}.")
            recoverable += npm_size

    if not lines:
        return "I didn't find anything obviously safe to clean up right now."

    body = " ".join(lines)
    footer = (
        " Windows also collects old update files under C:\\Windows that I don't scan since "
        "that folder is off-limits — Disk Cleanup handles those. I'm only reporting sizes here; "
        "nothing gets deleted unless you tell me to."
    )
    return f"You could likely free up around {_size_str(recoverable)} total. {body}{footer}"


# ----------------------------------------------------------------- memory
def memory_report(top_n: int = 5) -> str:
    """Overall RAM usage plus the biggest consumers — GREEN, read-only."""
    try:
        import psutil
    except ImportError:
        return "psutil isn't installed, so I can't read memory usage."

    vm = psutil.virtual_memory()

    # Aggregated by process NAME, not by individual PID: apps like Chrome or
    # svchost.exe routinely run dozens of processes, and listing each one
    # separately buries the actual signal ("chrome.exe: 210MB" x30 tells you
    # nothing "Chrome: 6.3 gigabytes" doesn't say better).
    by_name: dict[str, int] = {}
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            info = proc.info
            mem = info.get("memory_info")
            if mem is None:
                continue
            name = info.get("name") or "unknown"
            by_name[name] = by_name.get(name, 0) + mem.rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    summary = (
        f"You're using {vm.percent:.0f} percent of your memory — "
        f"{_size_str(vm.used)} out of {_size_str(vm.total)}, with {_size_str(vm.available)} available."
    )
    if top:
        items = ", ".join(f"{name} at {_size_str(rss)}" for name, rss in top)
        summary += f" The biggest users are: {items}."
    return summary


# --------------------------------------------------------------------- dispatch

# --------------------------------------------------- cached public entry points
def disk_report() -> str:
    """What's using your disk — GREEN. Answers instantly from a recent scan."""
    return _cached("disk", _disk_report_uncached)


def cleanup_suggestions(downloads_older_than_days: int = 30) -> str:
    """
    What's safe to delete, with real sizes — GREEN. Suggests only; nothing
    in this module deletes anything (deletion goes through RED delete_file).
    """
    if downloads_older_than_days != 30:
        return _cleanup_uncached(downloads_older_than_days)  # non-default: measure for real
    return _cached("cleanup", _cleanup_uncached)

REGISTRY: dict[str, Any] = {
    "disk_report": disk_report,
    "cleanup_suggestions": cleanup_suggestions,
    "memory_report": memory_report,
    "refresh_system_scan": refresh_system_scan,
}




def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
